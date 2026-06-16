from __future__ import annotations
"""
backend/engine/metrics/volume_oi.py
Sector: Options / Volume & OI Dynamics Engine
[ARCH-1, PD-4]

Theoretical basis:
    Agarwal, K. (2024). Option Chain Dynamics: Analysing Open Interest,
    Trading Volume, and Last Traded Price Relationships.
    TURCOMAT, 15(2), 140-146.
"""


import logging
from enum import Enum

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.volume_oi")

type FloatArray = npt.NDArray[np.float64]

_UOA_VOL_OI_THRESHOLD = 3.0
_UOA_TOP_N = 3
_DOTM_DELTA_THRESHOLD = 0.20


class Signal(str, Enum):
    NEW_POSITION = "New Position / Institutional Entry"
    DAY_TRADING = "Day Trading / Speculation"
    PROFIT_TAKING = "Profit Taking / Closing"
    STAGNATION = "Stagnation / Exhaustion"
    NOISE = "Below Noise Floor"
    INDETERMINATE = "Indeterminate"


class AnalyzerConfig(BaseModel):
    """Central configuration for all classification thresholds (frozen)."""

    model_config = ConfigDict(frozen=True)

    volume_noise_floor: int = 50
    high_volume_percentile: float = 70.0
    low_volume_percentile: float = 30.0
    oi_increase_percentile: float = 60.0
    oi_decrease_percentile: float = 60.0
    flat_oi_ratio_threshold: float = 0.10


class ContractSignal(BaseModel):
    """Immutable model representing classified contract signal."""

    model_config = ConfigDict(frozen=True)

    strike: float
    is_call: bool
    volume: float
    open_interest: float
    prev_open_interest: float
    net_oi_change: float
    volume_oi_ratio: float | None
    signal_type: Signal


class UOAStrike(BaseModel):
    """Immutable model representing an Unusual Options Activity strike."""

    model_config = ConfigDict(frozen=True)

    strike: float
    type: str
    vol_oi_ratio: float
    premium_estimate: float | None
    direction_bias: str


class VolumeOIDynamicsReport(BaseModel):
    """Immutable master report summarizing volume & open interest dynamics."""

    model_config = ConfigDict(frozen=True)

    classified_contracts: list[ContractSignal]
    thresholds: dict[str, float]
    summary: dict[str, dict[str, int]]

    # Premium Flow (Lee-Ready)
    call_net_premium: float | None
    put_net_premium: float | None
    flow_signal: float | None

    # DOTM metrics
    dotm_put_oi: float | None
    dotm_call_oi: float | None
    dotm_ratio: float | None
    dotm_signal: float | None
    dotm_alert: bool

    # Unusual Options Activity (UOA)
    uoa_strikes: list[UOAStrike]


class OptionsMarketAnalyzer:
    """
    Calculates options contract classification and volume/OI metrics using
    stateless and vectorized operations.
    """

    def __init__(self, config: AnalyzerConfig | None = None) -> None:
        self.config = config or AnalyzerConfig()

    def analyze(
        self,
        chain_data: FloatArray,
        spot: float,
        dotm_ratio_history: list[float] | None = None,
    ) -> Result[VolumeOIDynamicsReport]:
        """
        Processes option chain matrix and returns a VolumeOIDynamicsReport.

        Parameters
        ----------
        chain_data : FloatArray
            2D NumPy array with shape (N, 9) where the columns are:
            0 = strike
            1 = is_call (1.0 for Call, 0.0 for Put)
            2 = volume
            3 = open_interest
            4 = prev_open_interest
            5 = delta (calls delta positive, puts negative)
            6 = bid
            7 = ask
            8 = last_price
        spot : float
            Current underlying spot price.
        dotm_ratio_history : list[float], optional
            Historical DOTM ratio series used for calculating percentile alerts.

        Returns
        -------
        Result[VolumeOIDynamicsReport]
            The VolumeOIDynamicsReport wrapped in a Result monad.
        """
        try:
            # 1. Validations
            if not isinstance(chain_data, np.ndarray):
                return Result.failure(reason="chain_data must be a numpy ndarray")

            if chain_data.ndim != 2 or chain_data.shape[1] != 9:
                return Result.failure(
                    reason=(
                        f"chain_data must be a 2D array of shape (N, 9), "
                        f"got shape {chain_data.shape}"
                    )
                )

            if len(chain_data) == 0:
                return Result.failure(reason="chain_data is empty")

            strike = chain_data[:, 0]
            is_call = chain_data[:, 1]
            volume = chain_data[:, 2]
            oi = chain_data[:, 3]
            prev_oi = chain_data[:, 4]
            delta = chain_data[:, 5]
            bid = chain_data[:, 6]
            ask = chain_data[:, 7]
            last_price = chain_data[:, 8]

            if np.any(np.isnan(strike)) or np.any(np.isnan(volume)) or np.any(np.isnan(oi)):
                return Result.failure(
                    reason="strike, volume, and open_interest columns cannot contain NaN values"
                )

            if np.any((is_call != 0.0) & (is_call != 1.0)):
                return Result.failure(
                    reason="is_call column (column 1) must contain only 0.0 or 1.0"
                )

            if np.any(volume < 0.0) or np.any(oi < 0.0):
                return Result.failure(reason="volume and open_interest must be non-negative")

            # 2. Impute NaNs in prev_open_interest explicitly and safely
            nan_prev_mask = np.isnan(prev_oi)
            prev_oi_imputed = np.where(nan_prev_mask, oi, prev_oi)

            # 3. Feature Engineering
            net_oi_change = oi - prev_oi_imputed
            volume_oi_ratio = np.where(volume > 0.0, net_oi_change / volume, np.nan)

            # 4. Adaptive threshold computation
            active_mask = volume >= self.config.volume_noise_floor
            active_volume = volume[active_mask]
            active_net_oi = net_oi_change[active_mask]

            if len(active_volume) == 0:
                active_volume = volume
                active_net_oi = net_oi_change

            vol_high = (
                float(np.nanpercentile(active_volume, self.config.high_volume_percentile))
                if len(active_volume) > 0
                else 0.0
            )
            vol_low = (
                float(np.nanpercentile(active_volume, self.config.low_volume_percentile))
                if len(active_volume) > 0
                else 0.0
            )

            pos_oi = active_net_oi[active_net_oi > 0.0]
            if len(pos_oi) > 0:
                oi_inc_min = float(np.nanpercentile(pos_oi, self.config.oi_increase_percentile))
            else:
                oi_inc_min = 1.0

            neg_oi = active_net_oi[active_net_oi < 0.0]
            if len(neg_oi) > 0:
                oi_dec_min = float(
                    np.nanpercentile(np.abs(neg_oi), self.config.oi_decrease_percentile)
                )
            else:
                oi_dec_min = 1.0

            # 5. Vectorized classification
            is_noise = volume < self.config.volume_noise_floor
            is_high_vol = volume >= vol_high
            is_low_vol = volume < vol_low
            is_sig_increase = net_oi_change >= oi_inc_min
            is_sig_decrease = net_oi_change <= -oi_dec_min
            is_flat_oi = np.isfinite(volume_oi_ratio) & (
                np.abs(volume_oi_ratio) < self.config.flat_oi_ratio_threshold
            )

            condlist = [
                is_noise,
                is_high_vol & is_sig_increase,
                is_high_vol & is_flat_oi,
                is_sig_decrease,
                is_low_vol & ~is_sig_increase,
            ]
            choicelist = [
                Signal.NOISE.value,
                Signal.NEW_POSITION.value,
                Signal.DAY_TRADING.value,
                Signal.PROFIT_TAKING.value,
                Signal.STAGNATION.value,
            ]
            signal_types = np.select(condlist, choicelist, default=Signal.INDETERMINATE.value)

            # 6. Premium Flow (Lee-Ready classification)
            valid_flow = ~np.isnan(bid) & ~np.isnan(ask) & ~np.isnan(last_price)
            if np.any(valid_flow):
                mid = (bid[valid_flow] + ask[valid_flow]) / 2.0
                buyer = last_price[valid_flow] > mid
                sign = np.where(buyer, 1.0, -1.0)

                is_call_flow = is_call[valid_flow]
                call_mask = is_call_flow == 1.0
                put_mask = is_call_flow == 0.0

                call_net = float(np.sum(sign[call_mask] * last_price[valid_flow][call_mask]))
                put_net = float(np.sum(sign[put_mask] * last_price[valid_flow][put_mask]))
                denom = abs(call_net) + abs(put_net) + 1.0
                flow_signal = float(np.clip((call_net - put_net) / denom, -1.0, 1.0))
            else:
                call_net = None
                put_net = None
                flow_signal = None

            # 7. DOTM put/call OI analysis
            valid_dotm = ~np.isnan(delta) & ~np.isnan(oi)
            if np.any(valid_dotm):
                dotm_mask = np.abs(delta[valid_dotm]) < _DOTM_DELTA_THRESHOLD
                is_call_dotm = is_call[valid_dotm]
                oi_dotm = oi[valid_dotm]

                dotm_put_oi = float(np.sum(oi_dotm[dotm_mask & (is_call_dotm == 0.0)]))
                dotm_call_oi = float(np.sum(oi_dotm[dotm_mask & (is_call_dotm == 1.0)]))
                dotm_ratio = dotm_put_oi / (dotm_call_oi + 1.0)
                dotm_signal = float(np.clip(dotm_ratio / 10.0, 0.0, 1.0))

                dotm_alert = False
                if dotm_ratio_history is not None and len(dotm_ratio_history) >= 5:
                    history_arr = np.array(dotm_ratio_history)
                    threshold = float(np.percentile(history_arr, 80.0))
                    dotm_alert = bool(dotm_ratio > threshold)
            else:
                dotm_put_oi = None
                dotm_call_oi = None
                dotm_ratio = None
                dotm_signal = None
                dotm_alert = False

            # 8. Unusual Options Activity (UOA)
            oi_safe = np.clip(oi, a_min=1.0, a_max=None)
            uoa_ratios = volume / oi_safe
            uoa_mask = uoa_ratios >= _UOA_VOL_OI_THRESHOLD
            uoa_indices = np.where(uoa_mask)[0]

            uoa_strikes: list[UOAStrike] = []
            if len(uoa_indices) > 0:
                sorted_uoa_indices = uoa_indices[np.argsort(-uoa_ratios[uoa_indices])]
                top_uoa_indices = sorted_uoa_indices[:_UOA_TOP_N]
                for idx in top_uoa_indices:
                    o_type = "CALL" if is_call[idx] == 1.0 else "PUT"
                    prem_est = float(last_price[idx]) if not np.isnan(last_price[idx]) else None
                    uoa_strikes.append(
                        UOAStrike(
                            strike=float(strike[idx]),
                            type=o_type,
                            vol_oi_ratio=round(float(uoa_ratios[idx]), 2),
                            premium_estimate=prem_est,
                            direction_bias=o_type,
                        )
                    )

            # 9. Pack results into report
            classified_contracts = []
            for i in range(len(chain_data)):
                ratio_val = float(volume_oi_ratio[i]) if not np.isnan(volume_oi_ratio[i]) else None
                classified_contracts.append(
                    ContractSignal(
                        strike=float(strike[i]),
                        is_call=bool(is_call[i] == 1.0),
                        volume=float(volume[i]),
                        open_interest=float(oi[i]),
                        prev_open_interest=float(prev_oi_imputed[i]),
                        net_oi_change=float(net_oi_change[i]),
                        volume_oi_ratio=ratio_val,
                        signal_type=Signal(signal_types[i]),
                    )
                )

            summary = {sig.value: {"CALL": 0, "PUT": 0} for sig in Signal}
            for contract in classified_contracts:
                side = "CALL" if contract.is_call else "PUT"
                summary[contract.signal_type.value][side] += 1

            thresholds = {
                "vol_high": vol_high,
                "vol_low": vol_low,
                "oi_inc_min": oi_inc_min,
                "oi_dec_min": oi_dec_min,
            }

            report = VolumeOIDynamicsReport(
                classified_contracts=classified_contracts,
                thresholds=thresholds,
                summary=summary,
                call_net_premium=call_net,
                put_net_premium=put_net,
                flow_signal=flow_signal,
                dotm_put_oi=dotm_put_oi,
                dotm_call_oi=dotm_call_oi,
                dotm_ratio=dotm_ratio,
                dotm_signal=dotm_signal,
                dotm_alert=dotm_alert,
                uoa_strikes=uoa_strikes,
            )
            return Result.success(report)

        except Exception as e:
            logger.error("VolumeOIEngine analysis failed: %s", e)
            return Result.failure(reason=f"VolumeOIEngine analysis failed: {e}")


def get_volume_oi_analysis(
    chain_data: FloatArray,
    spot: float,
    *,
    analyzer_config: AnalyzerConfig | None = None,
    dotm_ratio_history: list[float] | None = None,
) -> Result[VolumeOIDynamicsReport]:
    """Stateless functional entry point for volume/OI analysis."""
    analyzer = OptionsMarketAnalyzer(config=analyzer_config)
    return analyzer.analyze(chain_data, spot, dotm_ratio_history)
