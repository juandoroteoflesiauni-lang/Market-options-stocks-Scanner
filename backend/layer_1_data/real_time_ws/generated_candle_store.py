from __future__ import annotations
from typing import Any
"""In-memory store for generated chart candles shared by REST, WS and technical views."""


from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

DEFAULT_CANDLE_LIMIT = 12_000

TIMEFRAME_BUCKET_MS: dict[str, int] = {
    "1s": 1_000,
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


@dataclass(frozen=True)
class CandleSnapshot:
    """Immutable snapshot for one symbol/timeframe candle stream."""

    symbol: str
    timeframe: str
    candles: list[dict[str, Any]]
    source: str | None = None
    updated_at: str | None = None
    live_partial_bar: bool = False
    last_candle_time: int | None = None

    @property
    def count(self) -> int:
        return len(self.candles)


@dataclass
class _StoredCandles:
    candles: list[dict[str, Any]] = field(default_factory=list)
    source: str | None = None
    updated_at: str | None = None
    live_partial_bar: bool = False


def normalize_timeframe(timeframe: str) -> str:
    raw = str(timeframe or "1d").strip()
    aliases = {"1D": "1d", "1W": "1w", "1S": "1w"}
    if raw in aliases:
        return aliases[raw]
    return raw.lower()


def normalize_epoch_ms(raw: object) -> int | None:
    """Normalize seconds/ms epoch-ish values into milliseconds."""
    if raw is None:
        return None
    if not isinstance(raw, int | float | str):
        return None
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value < 1_000_000_000_000:
        value *= 1000
    return value


def bucket_start_ms(ts_ms: int, timeframe: str) -> int:
    step = TIMEFRAME_BUCKET_MS.get(normalize_timeframe(timeframe), 60_000)
    return (ts_ms // step) * step


def normalize_candle(row: dict[str, Any], timeframe: str) -> dict[str, Any] | None:
    """Return a JSON-safe OHLCV candle aligned to the requested timeframe bucket."""
    raw_time = row.get("time", row.get("t"))
    ts_ms = normalize_epoch_ms(raw_time)
    if ts_ms is None:
        return None
    try:
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        volume = float(row.get("volume") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None
    if min(open_price, high, low, close) <= 0:
        return None
    return {
        "time": bucket_start_ms(ts_ms, timeframe),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": max(volume, 0.0),
    }


class GeneratedCandleStore:
    """Thread-safe in-memory candle cache keyed by symbol and timeframe."""

    def __init__(self, default_limit: int = DEFAULT_CANDLE_LIMIT) -> None:
        self.default_limit = max(1, int(default_limit))
        self._lock = RLock()
        self._data: dict[tuple[str, str], _StoredCandles] = {}

    def seed(
        self,
        symbol: str,
        timeframe: str,
        candles: list[dict[str, Any]],
        *,
        source: str | None = None,
        live_partial_bar: bool = False,
        limit: int | None = None,
    ) -> CandleSnapshot:
        """Replace one stream with normalized, deduplicated historical candles."""
        sym, tf = self._key_parts(symbol, timeframe)
        normalized = self._dedupe(candles, tf, limit=limit)
        updated_at = self._utc_now()
        with self._lock:
            self._data[(sym, tf)] = _StoredCandles(
                candles=normalized,
                source=source,
                updated_at=updated_at,
                live_partial_bar=live_partial_bar,
            )
        return self.snapshot(sym, tf) or CandleSnapshot(sym, tf, [])

    def upsert(
        self,
        symbol: str,
        timeframe: str,
        candle: dict[str, Any],
        *,
        source: str | None = None,
        live_partial_bar: bool = True,
        limit: int | None = None,
    ) -> CandleSnapshot | None:
        """Insert or replace one generated candle by timeframe bucket."""
        sym, tf = self._key_parts(symbol, timeframe)
        normalized = normalize_candle(candle, tf)
        if normalized is None:
            return self.snapshot(sym, tf)

        cap = max(1, int(limit or self.default_limit))
        updated_at = self._utc_now()
        with self._lock:
            stored = self._data.setdefault((sym, tf), _StoredCandles())
            by_time = {int(row["time"]): row for row in stored.candles}
            by_time[int(normalized["time"])] = normalized
            stored.candles = [by_time[k] for k in sorted(by_time.keys())][-cap:]
            stored.source = source or stored.source
            stored.updated_at = updated_at
            stored.live_partial_bar = live_partial_bar
        return self.snapshot(sym, tf)

    def snapshot(self, symbol: str, timeframe: str) -> CandleSnapshot | None:
        """Return a copy of one stored stream."""
        sym, tf = self._key_parts(symbol, timeframe)
        with self._lock:
            stored = self._data.get((sym, tf))
            if stored is None:
                return None
            candles = [dict(row) for row in stored.candles]
            last_time = int(candles[-1]["time"]) if candles else None
            return CandleSnapshot(
                symbol=sym,
                timeframe=tf,
                candles=candles,
                source=stored.source,
                updated_at=stored.updated_at,
                live_partial_bar=stored.live_partial_bar,
                last_candle_time=last_time,
            )

    def clear(self) -> None:
        """Clear all streams. Intended for tests."""
        with self._lock:
            self._data.clear()

    def _dedupe(
        self,
        candles: list[dict[str, Any]],
        timeframe: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        cap = max(1, int(limit or self.default_limit))
        by_time: dict[int, dict[str, Any]] = {}
        for row in candles:
            normalized = normalize_candle(row, timeframe)
            if normalized is not None:
                by_time[int(normalized["time"])] = normalized
        return [by_time[k] for k in sorted(by_time.keys())][-cap:]

    @staticmethod
    def _key_parts(symbol: str, timeframe: str) -> tuple[str, str]:
        return symbol.upper().strip(), normalize_timeframe(timeframe)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(tz=UTC).isoformat()


_STORE = GeneratedCandleStore()


def get_generated_candle_store() -> GeneratedCandleStore:
    """Return the process-local generated candle store singleton."""
    return _STORE
