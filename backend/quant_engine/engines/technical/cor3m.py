"""
backend/engine/metrics/cor3m.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

COR3M Signal Engine — Implied Correlation panic and re-entry detector.
Stateless and vectorized implementation without pandas.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import Enum

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, model_validator

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.cor3m")

type FloatArray = npt.NDArray[np.float64]


class MarketState(Enum):
    """Internal regime states tracked by the signal engine."""

    NORMAL = "NORMAL"
    SYSTEMIC_PANIC_HOLD = "SYSTEMIC_PANIC_HOLD"
    LONG_LIQUIDITY_RALLY = "LONG_LIQUIDITY_RALLY"


class SignalType(Enum):
    """Output signal types (LONG-ONLY)."""

    BUY = "BUY"
    NEUTRAL = "NEUTRAL"


class SignalBar(BaseModel):
    """Immutable record for a single bar's engine output."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    cor3m_value: float
    percentile_rank: float
    market_state: MarketState
    signal: SignalType
    bars_since_panic: int
    note: str = ""


class EngineConfig(BaseModel):
    """All tunable hyper-parameters for COR3M_Signal_Engine."""

    model_config = ConfigDict(frozen=True)

    percentile_window: int = 252
    panic_threshold: float = 0.90
    signal_threshold: float = 0.85
    memory_window: int = 5
    min_periods: int | None = None

    @model_validator(mode="after")
    def validate_config(self) -> EngineConfig:
        if not (0.0 < self.signal_threshold < self.panic_threshold < 1.0):
            raise ValueError(
                f"Must satisfy 0 < signal_threshold ({self.signal_threshold}) "
                f"< panic_threshold ({self.panic_threshold}) < 1"
            )
        if self.percentile_window < 2:
            raise ValueError("percentile_window must be >= 2.")
        if self.memory_window < 1:
            raise ValueError("memory_window must be >= 1.")
        return self


def _compute_single_percentile(window: np.ndarray, val: float) -> float:
    """Compute the fractional rank (percentile) of a value within a window."""
    n = len(window)
    if n == 0:
        return 0.0
    return float((np.sum(window < val) + 0.5 * np.sum(window == val) + 0.5) / n)


class COR3M_Signal_Engine:  # noqa: N801
    """
    Long-only signal engine powered by the CBOE COR3M implied-correlation index.
    Purely stateless and vectorized.
    """

    def __init__(self, config: EngineConfig | None = None) -> None:
        self._cfg = config or EngineConfig()

    @property
    def config(self) -> EngineConfig:
        """Read-only view of the engine configuration."""
        return self._cfg

    def analyze_current_state(
        self,
        cor3m_history: FloatArray,
        timestamp: datetime | None = None,
    ) -> Result[SignalBar]:
        """
        Evaluate the current instant (the last value of the array).
        """
        try:
            n = len(cor3m_history)
            min_periods = (
                self._cfg.min_periods
                if self._cfg.min_periods is not None
                else self._cfg.percentile_window
            )
            min_required_len = min_periods + self._cfg.memory_window

            if n < min_required_len:
                return Result.failure(
                    reason=(
                        f"Insufficient history: got {n} elements, "
                        f"need at least {min_required_len} (min_periods={min_periods} "
                        f"+ memory_window={self._cfg.memory_window})"
                    )
                )

            if np.any(np.isnan(cor3m_history)):
                return Result.failure(reason="Input history contains NaN values")

            if np.any(cor3m_history < 0.0):
                logger.warning("COR3M contains negative values — verify data source.")

            # Compute percentile ranks for the last memory_window + 1 elements
            pct_ranks = []
            for t in range(n - self._cfg.memory_window - 1, n):
                window_start = max(0, t - self._cfg.percentile_window + 1)
                window = cor3m_history[window_start : t + 1]
                val = cor3m_history[t]
                pct = _compute_single_percentile(window, val)
                pct_ranks.append(pct)

            # State machine walk forward
            state = MarketState.NORMAL
            signal = SignalType.NEUTRAL
            bars_since_panic = 0
            recently_panicked = False
            last_note = ""

            for idx, _t in enumerate(range(n - self._cfg.memory_window - 1, n)):
                pct = pct_ranks[idx]

                last_note = ""
                signal = SignalType.NEUTRAL

                if pct >= self._cfg.panic_threshold:
                    state = MarketState.SYSTEMIC_PANIC_HOLD
                    recently_panicked = True
                    bars_since_panic = 0
                    last_note = f"Pct {pct:.1%} >= panic threshold {self._cfg.panic_threshold:.0%}"
                elif recently_panicked:
                    bars_since_panic += 1
                    state = MarketState.NORMAL

                    if bars_since_panic <= self._cfg.memory_window:
                        if pct < self._cfg.signal_threshold:
                            state = MarketState.LONG_LIQUIDITY_RALLY
                            signal = SignalType.BUY
                            last_note = (
                                f"▶ LONG ENTRY — pct {pct:.1%} crossed below "
                                f"signal threshold {self._cfg.signal_threshold:.0%} "
                                f"({bars_since_panic} bar(s) after panic peak)"
                            )
                            recently_panicked = False
                            bars_since_panic = 0
                        else:
                            last_note = (
                                f"Armed ({bars_since_panic}/{self._cfg.memory_window} bars) — "
                                f"waiting for pct < {self._cfg.signal_threshold:.0%}"
                            )
                    else:
                        recently_panicked = False
                        bars_since_panic = 0
                        last_note = "Memory window expired without BUY trigger — reset to NORMAL"
                else:
                    state = MarketState.NORMAL
                    bars_since_panic = 0

            ts = timestamp or datetime.now(tz=UTC)
            bar = SignalBar(
                timestamp=ts,
                cor3m_value=float(cor3m_history[-1]),
                percentile_rank=float(pct_ranks[-1]),
                market_state=state,
                signal=signal,
                bars_since_panic=bars_since_panic,
                note=last_note,
            )
            return Result.success(bar)
        except Exception as e:
            logger.error("COR3M engine analysis failed: %s", e)
            return Result.failure(reason=f"COR3M engine analysis failed: {e}")
