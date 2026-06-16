from __future__ import annotations
"""
backend/engine/metrics/delta_weighted_flow.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Delta-Weighted Premium Flow Engine — Capitulation and Mechanical Floor detector.
Stateless and vectorized implementation without pandas.
"""


import logging
from enum import Enum

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, model_validator

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.delta_weighted_flow")

type FloatArray = npt.NDArray[np.float64]


class MarketSignal(Enum):
    """Enumeration of all possible engine output signals (LONG-ONLY)."""

    NEUTRAL = "NEUTRAL"
    HOLD_STATE = "HOLD_STATE"
    EXHAUSTION_WARNING = "EXHAUSTION_WARNING"
    LONG_SETUP_TRIGGER = "LONG_SETUP_TRIGGER"


class FlowSnapshot(BaseModel):
    """Immutable result object returned after processing one options snapshot."""

    model_config = ConfigDict(frozen=True)

    total_call_flow: float
    total_put_flow: float
    pc_flow_ratio: float
    z_score: float | None
    signal: MarketSignal
    rolling_mean: float | None
    rolling_std: float | None
    is_in_exhaustion: bool


class EngineConfig(BaseModel):
    """All tunable hyper-parameters for DeltaWeightedFlow_Engine."""

    model_config = ConfigDict(frozen=True)

    contract_multiplier: int = 100
    rolling_window: int = 20
    panic_threshold: float = 3.0
    reset_threshold: float = 1.0

    @model_validator(mode="after")
    def validate_config(self) -> EngineConfig:
        if self.contract_multiplier <= 0:
            raise ValueError("contract_multiplier must be > 0.")
        if self.rolling_window < 2:
            raise ValueError("rolling_window must be >= 2.")
        if self.panic_threshold <= self.reset_threshold:
            raise ValueError("panic_threshold must be > reset_threshold.")
        return self


class DeltaWeightedFlow_Engine:  # noqa: N801
    """
    Calculates Delta-Weighted Premium Flow and detects institutional capitulation.
    Purely stateless and vectorized.
    """

    def __init__(self, config: EngineConfig | None = None) -> None:
        self._cfg = config or EngineConfig()

    @property
    def config(self) -> EngineConfig:
        """Read-only view of the engine configuration."""
        return self._cfg

    def analyze_flow(
        self,
        chain_data: FloatArray,
        ratio_history: FloatArray,
        was_in_exhaustion: bool,
    ) -> Result[FlowSnapshot]:
        """
        Processes one options chain snapshot and returns a FlowSnapshot.

        Parameters
        ----------
        chain_data : FloatArray
            2D NumPy array with shape (N, 4) where the columns are:
            0 = is_call (1.0 for Call, 0.0 for Put)
            1 = volume
            2 = mark_price
            3 = delta (calls delta positive, puts negative)
        ratio_history : FloatArray
            1D NumPy array containing the past PC ratios.
        was_in_exhaustion : bool
            Previous state indicating if we were in the panic/exhaustion phase.

        Returns
        -------
        Result[FlowSnapshot]
            The FlowSnapshot result wrapped in a Result monad.
        """
        try:
            # Basic validation
            if chain_data.ndim != 2 or chain_data.shape[1] != 4:
                return Result.failure(
                    reason=(
                        f"chain_data must be a 2D array of shape (N, 4), "
                        f"got shape {chain_data.shape}"
                    )
                )

            if len(chain_data) == 0:
                return Result.failure(reason="chain_data is empty")

            if np.any(np.isnan(chain_data)) or np.any(np.isnan(ratio_history)):
                return Result.failure(reason="Input data contains NaN values")

            is_call = chain_data[:, 0]
            volume = chain_data[:, 1]
            mark_price = chain_data[:, 2]
            delta = chain_data[:, 3]

            if np.any((is_call != 0.0) & (is_call != 1.0)):
                return Result.failure(
                    reason="is_call column (column 0) must contain only 0.0 or 1.0"
                )

            if np.any(volume < 0.0) or np.any(mark_price < 0.0):
                return Result.failure(reason="volume and mark_price must be non-negative")

            # Premium Flow & Delta-Weighted Flow calculations
            premium_flow = volume * mark_price * self._cfg.contract_multiplier
            dw_flow = premium_flow * np.abs(delta)

            call_mask = is_call == 1.0
            put_mask = is_call == 0.0

            total_call_flow = float(np.sum(dw_flow[call_mask]))
            total_put_flow = float(np.sum(dw_flow[put_mask]))

            if total_call_flow == 0.0:
                return Result.failure(reason="Call flow is zero. Cannot compute PC ratio.")

            pc_ratio = total_put_flow / total_call_flow

            # Build combined window for statistics
            combined = np.append(ratio_history, pc_ratio)
            if len(combined) > self._cfg.rolling_window:
                combined = combined[-self._cfg.rolling_window :]

            n = len(combined)
            if n < 2:
                z_score = None
                roll_mean = None
                roll_std = None
            else:
                finite_mask = np.isfinite(combined)
                if np.sum(finite_mask) < 2:
                    z_score = None
                    roll_mean = None
                    roll_std = None
                else:
                    roll_mean = float(combined[finite_mask].mean())
                    roll_std = float(combined[finite_mask].std(ddof=1))

                    if roll_std == 0.0:
                        z_score = 0.0
                    else:
                        if not np.isfinite(pc_ratio):
                            z_score = self._cfg.panic_threshold + 1.0
                        else:
                            z_score = (pc_ratio - roll_mean) / roll_std

            # State machine / signal classification
            is_in_exhaustion = was_in_exhaustion

            if z_score is None:
                signal = MarketSignal.NEUTRAL
            elif z_score > self._cfg.panic_threshold:
                is_in_exhaustion = True
                signal = MarketSignal.EXHAUSTION_WARNING
            elif was_in_exhaustion and z_score <= self._cfg.reset_threshold:
                is_in_exhaustion = False
                signal = MarketSignal.LONG_SETUP_TRIGGER
            elif total_call_flow > total_put_flow:
                signal = MarketSignal.HOLD_STATE
            else:
                signal = MarketSignal.NEUTRAL

            snapshot = FlowSnapshot(
                total_call_flow=total_call_flow,
                total_put_flow=total_put_flow,
                pc_flow_ratio=pc_ratio,
                z_score=z_score,
                signal=signal,
                rolling_mean=roll_mean,
                rolling_std=roll_std,
                is_in_exhaustion=is_in_exhaustion,
            )
            return Result.success(snapshot)

        except Exception as e:
            logger.error("DeltaWeightedFlow engine analysis failed: %s", e)
            return Result.failure(reason=f"DeltaWeightedFlow engine analysis failed: {e}")
