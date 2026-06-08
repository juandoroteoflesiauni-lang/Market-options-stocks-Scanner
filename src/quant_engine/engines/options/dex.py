"""
backend/engine/metrics/dex.py
Sector: Options / MM Delta Exposure (DEX) Engine
[ARCH-1, PD-4]

Theoretical basis:
    Calculates MM Delta Exposure (DEX) to estimate market maker hedging pressure
    and identify critical acceleration zones (gamma trap / flip levels).
"""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.dex")

type FloatArray = npt.NDArray[np.float64]


class DEXConfig(BaseModel):
    """Configuration options for Delta Exposure calculation."""
    model_config = ConfigDict(frozen=True)

    multiplier: int = 100


class StrikeProfile(BaseModel):
    """DEX exposure profile for a single strike price."""
    model_config = ConfigDict(frozen=True)

    strike: float
    call_dex: float
    put_dex: float
    net_dex: float
    dex_per_1pct_move: float
    dex_cumulative: float


class DEXReport(BaseModel):
    """Aggregate Delta Exposure report for an asset."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    spot_price: float
    dex_total_nominal: float
    dex_calls: float
    dex_puts: float
    dex_profile: list[StrikeProfile]
    top_strikes: list[StrikeProfile]
    dex_as_pct_adtv: float | None
    adtv: float | None
    gamma_flip_level: float | None


class DeltaExposureEngine:
    """
    vectorized engine for computing market maker delta exposure (DEX).
    """

    def __init__(self, config: DEXConfig | None = None) -> None:
        self.config = config or DEXConfig()

    def analyze(
        self,
        ticker: str,
        spot_price: float,
        adtv: float | None,
        chain_data: FloatArray,
    ) -> Result[DEXReport]:
        """
        Calculates the delta exposure report for a given options chain.

        Parameters
        ----------
        ticker : str
            Symbol of the underlying asset.
        spot_price : float
            Current price of the underlying asset.
        adtv : float, optional
            Average Daily Trading Value in USD.
        chain_data : FloatArray
            2D NumPy array with shape (N, 4) where the columns are:
            0 = strike
            1 = is_call (1.0 for Call, 0.0 for Put)
            2 = delta
            3 = open_interest

        Returns
        -------
        Result[DEXReport]
            The DEXReport wrapped in a Result monad.
        """
        try:
            # 1. Validations
            if not isinstance(chain_data, np.ndarray):
                return Result.failure(reason="chain_data must be a numpy ndarray")

            if chain_data.ndim != 2 or chain_data.shape[1] != 4:
                return Result.failure(
                    reason=(
                        f"chain_data must be a 2D array of shape (N, 4), "
                        f"got shape {chain_data.shape}"
                    )
                )

            if len(chain_data) == 0:
                return Result.failure(reason="chain_data is empty")

            if np.any(np.isnan(chain_data)):
                return Result.failure(reason="Input data contains NaN values")

            if spot_price <= 0.0:
                return Result.failure(reason="spot_price must be strictly positive")

            strikes = chain_data[:, 0]
            is_call = chain_data[:, 1]
            delta = chain_data[:, 2]
            open_interest = chain_data[:, 3]

            if np.any((is_call != 0.0) & (is_call != 1.0)):
                return Result.failure(
                    reason="is_call column (column 1) must contain only 0.0 or 1.0"
                )

            if np.any(open_interest < 0.0):
                return Result.failure(reason="open_interest cannot contain negative values")

            # 2. Vectorized Delta Sign Correction
            corrected_delta = np.where(is_call == 1.0, np.abs(delta), -np.abs(delta))

            # 3. Calculate DEX Nocional in a single line
            dex = corrected_delta * open_interest * self.config.multiplier * spot_price

            # 4. Group by strike using np.unique and np.bincount
            unique_strikes, inverse_indices = np.unique(strikes, return_inverse=True)
            u_len = len(unique_strikes)

            call_mask = is_call == 1.0
            put_mask = is_call == 0.0

            call_dex_by_strike = np.bincount(
                inverse_indices, weights=np.where(call_mask, dex, 0.0), minlength=u_len
            )
            put_dex_by_strike = np.bincount(
                inverse_indices, weights=np.where(put_mask, dex, 0.0), minlength=u_len
            )
            net_dex_by_strike = call_dex_by_strike + put_dex_by_strike

            # 5. Cumulative calculations (np.unique returns sorted unique elements)
            dex_cumulative = np.cumsum(net_dex_by_strike)
            dex_per_1pct_move = net_dex_by_strike * 0.01

            # 6. Gamma Flip Level calculation (vectorized sign change crossing)
            gamma_flip_level = None
            if u_len >= 2:
                cross_mask = dex_cumulative[:-1] * dex_cumulative[1:] < 0.0
                cross_indices = np.where(cross_mask)[0]
                if len(cross_indices) > 0:
                    gamma_flip_level = float(unique_strikes[cross_indices[0] + 1])

            # 7. Total sum of DEX
            dex_calls = float(np.sum(dex[call_mask]))
            dex_puts = float(np.sum(dex[put_mask]))
            dex_total = dex_calls + dex_puts

            # 8. ADTV normalisation
            dex_pct_adtv = None
            if adtv is not None and adtv > 0.0:
                dex_pct_adtv = (abs(dex_total) / adtv) * 100.0

            # 9. Build lists of StrikeProfile for report
            dex_profile: list[StrikeProfile] = []
            for i, strike_val in enumerate(unique_strikes):
                dex_profile.append(
                    StrikeProfile(
                        strike=float(strike_val),
                        call_dex=float(call_dex_by_strike[i]),
                        put_dex=float(put_dex_by_strike[i]),
                        net_dex=float(net_dex_by_strike[i]),
                        dex_per_1pct_move=float(dex_per_1pct_move[i]),
                        dex_cumulative=float(dex_cumulative[i]),
                    )
                )

            # Sort by absolute net DEX descending for top strikes
            sorted_indices = np.argsort(-np.abs(net_dex_by_strike))
            top_indices = sorted_indices[:5]
            top_strikes: list[StrikeProfile] = []
            for idx in top_indices:
                top_strikes.append(
                    StrikeProfile(
                        strike=float(unique_strikes[idx]),
                        call_dex=float(call_dex_by_strike[idx]),
                        put_dex=float(put_dex_by_strike[idx]),
                        net_dex=float(net_dex_by_strike[idx]),
                        dex_per_1pct_move=float(dex_per_1pct_move[idx]),
                        dex_cumulative=float(dex_cumulative[idx]),
                    )
                )

            report = DEXReport(
                ticker=ticker.upper(),
                spot_price=spot_price,
                dex_total_nominal=dex_total,
                dex_calls=dex_calls,
                dex_puts=dex_puts,
                dex_profile=dex_profile,
                top_strikes=top_strikes,
                dex_as_pct_adtv=dex_pct_adtv,
                adtv=adtv,
                gamma_flip_level=gamma_flip_level,
            )
            return Result.success(report)

        except Exception as e:
            logger.error("DeltaExposureEngine analysis failed: %s", e)
            return Result.failure(reason=f"DeltaExposureEngine analysis failed: {e}")


def get_dex_analysis(
    ticker: str,
    spot_price: float,
    adtv: float | None,
    chain_data: FloatArray,
    *,
    config: DEXConfig | None = None,
) -> Result[DEXReport]:
    """Stateless functional entry point for Delta Exposure (DEX) analysis."""
    engine = DeltaExposureEngine(config=config)
    return engine.analyze(ticker, spot_price, adtv, chain_data)
