"""Dual-loop scheduler policy — fast monitor + slow full scan. # [PD-3][TH]"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

CycleMode = Literal["fast", "slow"]

_DEFAULT_FAST_S = 75
_DEFAULT_SLOW_S = 240
_MIN_FAST_S = 30
_MIN_SLOW_S = 60


@dataclass
class DualLoopConfig:
    """Timing policy for institutional-style dual loops."""

    enabled: bool = True
    fast_interval_s: int = _DEFAULT_FAST_S
    slow_interval_s: int = _DEFAULT_SLOW_S

    def __post_init__(self) -> None:
        if self.fast_interval_s < _MIN_FAST_S:
            raise ValueError(f"fast_interval_s must be >= {_MIN_FAST_S}")
        if self.slow_interval_s < _MIN_SLOW_S:
            raise ValueError(f"slow_interval_s must be >= {_MIN_SLOW_S}")
        if self.slow_interval_s < self.fast_interval_s:
            raise ValueError("slow_interval_s must be >= fast_interval_s")

    @classmethod
    def from_env(cls) -> DualLoopConfig:
        """Load dual-loop settings from environment."""
        enabled = os.getenv("BOT_DUAL_LOOP_ENABLED", "true").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        fast = int(os.getenv("BOT_FAST_CYCLE_INTERVAL_S", str(_DEFAULT_FAST_S)))
        slow = int(os.getenv("BOT_SLOW_CYCLE_INTERVAL_S", str(_DEFAULT_SLOW_S)))
        legacy = os.getenv("BOT_CYCLE_INTERVAL_S", "").strip()
        if legacy:
            try:
                slow = int(legacy)
            except ValueError:
                pass
        return cls(enabled=enabled, fast_interval_s=fast, slow_interval_s=slow)


class DualLoopGate:
    """Tracks slow-cycle cadence and prevents overlapping ticks (mutex)."""

    def __init__(self, config: DualLoopConfig) -> None:
        self._config = config
        self._last_slow_at: datetime | None = None
        self._in_flight = False

    @property
    def in_flight(self) -> bool:
        return self._in_flight

    def try_acquire(self) -> bool:
        """Return False when a previous cycle is still running."""
        if self._in_flight:
            return False
        self._in_flight = True
        return True

    def release(self) -> None:
        self._in_flight = False

    def resolve_mode(self, now: datetime, *, force_slow: bool = False) -> CycleMode:
        """Pick fast vs slow for this tick."""
        if not self._config.enabled or force_slow:
            return "slow"
        if self._last_slow_at is None:
            return "slow"
        elapsed = (now - self._last_slow_at).total_seconds()
        if elapsed >= self._config.slow_interval_s:
            return "slow"
        return "fast"

    def mark_slow_completed(self, now: datetime) -> None:
        self._last_slow_at = now
        logger.debug(
            "dual_loop.slow_marked at=%s next_slow_in_s=%d",
            now.isoformat(),
            self._config.slow_interval_s,
        )


__all__ = ["CycleMode", "DualLoopConfig", "DualLoopGate"]
