"""Market Breadth Tracker — contador atómico en memoria del SuperTrend Regime.

Mientras Scanner.scan_universe() procesa miles de tickers, este tracker
acumula cuántos están en régimen alcista (bullish) vs bajista (bearish)
según el SuperTrend, y expone la proporción en endpoints de la API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class MarketBreadthSnapshot:
    """Instantánea del breadth en un momento dado."""

    bullish: int = 0
    bearish: int = 0
    no_data: int = 0
    total_scanned: int = 0
    last_updated: str | None = None

    @property
    def bullish_pct(self) -> float:
        if self.total_scanned == 0:
            return 0.0
        return round(self.bullish / self.total_scanned * 100, 2)

    @property
    def bearish_pct(self) -> float:
        if self.total_scanned == 0:
            return 0.0
        return round(self.bearish / self.total_scanned * 100, 2)

    @property
    def coverage_pct(self) -> float:
        """Porcentaje de tickers con datos (bullish + bearish) sobre el total."""
        if self.total_scanned == 0:
            return 0.0
        return round((self.bullish + self.bearish) / self.total_scanned * 100, 2)

    def to_dict(self) -> dict[str, object]:
        return {
            "bullish": self.bullish,
            "bearish": self.bearish,
            "no_data": self.no_data,
            "total_scanned": self.total_scanned,
            "bullish_pct": self.bullish_pct,
            "bearish_pct": self.bearish_pct,
            "coverage_pct": self.coverage_pct,
            "last_updated": self.last_updated,
        }


class MarketBreadthTracker:
    """Tracker thread-safe del breadth de mercado basado en SuperTrend.

    Uso:
        tracker = MarketBreadthTracker()
        tracker.reset()
        tracker.record_bullish()
        tracker.record_bearish()
        tracker.record_no_data()
        summary = tracker.snapshot()
    """

    def __init__(self) -> None:
        self._bullish: int = 0
        self._bearish: int = 0
        self._no_data: int = 0
        self._total_scanned: int = 0
        self._last_updated: datetime | None = None

    def reset(self) -> None:
        """Reinicia todos los contadores para un nuevo scan cycle."""
        self._bullish = 0
        self._bearish = 0
        self._no_data = 0
        self._total_scanned = 0
        self._last_updated = None

    def record_bullish(self) -> None:
        self._bullish += 1
        self._total_scanned += 1
        self._last_updated = datetime.now(UTC)

    def record_bearish(self) -> None:
        self._bearish += 1
        self._total_scanned += 1
        self._last_updated = datetime.now(UTC)

    def record_no_data(self) -> None:
        self._no_data += 1
        self._total_scanned += 1
        self._last_updated = datetime.now(UTC)

    @property
    def snapshot(self) -> MarketBreadthSnapshot:
        return MarketBreadthSnapshot(
            bullish=self._bullish,
            bearish=self._bearish,
            no_data=self._no_data,
            total_scanned=self._total_scanned,
            last_updated=self._last_updated.isoformat() if self._last_updated else None,
        )
