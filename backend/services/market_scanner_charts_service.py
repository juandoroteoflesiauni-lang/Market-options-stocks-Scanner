"""Mini-chart service for market scanner.

Handles fetching, caching, and normalising 5m OHLCV candles
from FMP Enterprise or Alpaca for scanner card sparklines.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any

from backend.config.settings import load_settings
from backend.layer_1_data.datos.intraday_bars_fetcher import _fetch_alpaca_bars, _fetch_fmp_bars

_MIN_CHART_INTERVAL = "5m"
_MIN_CHART_FMP_INTERVAL = "5min"
_CACHE_TTL_SECONDS = 20.0

_cache: dict[tuple[str, int], tuple[float, dict[str, object]]] = {}
_in_flight: dict[tuple[str, int], asyncio.Task[dict[str, object]]] = {}
_lock = asyncio.Lock()


def normalise_mini_chart_limit(limit: int) -> int:
    return max(24, min(int(limit or 96), 390))


def _normalise_mini_chart_bar(row: dict[str, Any]) -> dict[str, float] | None:
    raw_time = row.get("time", row.get("t"))
    try:
        ts = float(raw_time)
        open_ = float(row.get("open", 0))
        high = float(row.get("high", 0))
        low = float(row.get("low", 0))
        close = float(row.get("close", 0))
        volume = float(row.get("volume", 0) or 0)
    except (TypeError, ValueError):
        return None
    if ts <= 0 or not all(math.isfinite(v) and v > 0 for v in (open_, high, low, close)):
        return None
    return {
        "time": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": max(volume, 0.0),
    }


async def _load_mini_chart(symbol: str, limit: int) -> dict[str, object]:
    cfg = load_settings()
    sym = symbol.upper().strip()
    bars: list[dict[str, Any]] | None = None
    source = ""

    fmp_key = (
        getattr(cfg, "fmp_key_market", None)
        or getattr(cfg, "fmp_key_technical", None)
        or getattr(cfg, "fmp_api_key", None)
        or os.getenv("FMP_API_KEY", "")
    )
    if fmp_key:
        bars = await asyncio.to_thread(
            _fetch_fmp_bars,
            sym,
            _MIN_CHART_INTERVAL,
            fmp_key,
            limit,
        )
        if bars:
            source = "fmp_enterprise"

    if not bars:
        alpaca_key = os.getenv("ALPACA_API_KEY", "") or getattr(cfg, "alpaca_api_key", "") or ""
        alpaca_secret = (
            os.getenv("ALPACA_SECRET_KEY", "") or getattr(cfg, "alpaca_secret_key", "") or ""
        )
        alpaca_base = os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets")
        if alpaca_key and alpaca_secret:
            bars = await asyncio.to_thread(
                _fetch_alpaca_bars,
                sym,
                _MIN_CHART_INTERVAL,
                alpaca_key,
                alpaca_secret,
                alpaca_base,
                limit,
                5,
            )
            if bars:
                source = "alpaca"

    candles = [
        candle for row in (bars or []) if (candle := _normalise_mini_chart_bar(row)) is not None
    ]
    candles = sorted(candles, key=lambda bar: bar["time"])[-limit:]
    return {
        "ok": bool(candles),
        "symbol": sym,
        "timeframe": _MIN_CHART_INTERVAL,
        "interval": _MIN_CHART_FMP_INTERVAL,
        "candles": candles,
        "count": len(candles),
        "meta": {"source": source or "unavailable"},
    }


async def fetch_mini_chart(symbol: str, limit: int = 96) -> dict[str, object]:
    sym = symbol.upper().strip()
    capped_limit = normalise_mini_chart_limit(limit)
    key = (sym, capped_limit)
    now = time.monotonic()

    async with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
        task = _in_flight.get(key)
        if task is None:
            task = asyncio.create_task(_load_mini_chart(sym, capped_limit))
            _in_flight[key] = task

    try:
        result = await task
    finally:
        async with _lock:
            if _in_flight.get(key) is task:
                _in_flight.pop(key, None)

    async with _lock:
        _cache[key] = (time.monotonic(), result)
    return result


def invalidate_mini_chart_cache(symbol: str | None = None, limit: int | None = None) -> int:
    keys: list[tuple[str, int]] = []
    for k in _cache:
        if symbol is None or (k[0] == symbol.upper().strip() and (limit is None or k[1] == limit)):
            keys.append(k)
    for k in keys:
        _cache.pop(k, None)
        _in_flight.pop(k, None)
    return len(keys)
