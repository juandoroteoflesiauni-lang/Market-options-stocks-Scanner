"""TTL in-process cache para reducir llamadas REST repetidas (Massive/FMP). # [PD-3][TH]"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from typing import Any, TypeVar

from cachetools import TTLCache

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

_LOCK = threading.Lock()
_METRICS: dict[str, int] = {"hits": 0, "misses": 0, "negative_hits": 0}

_OPTIONS_TTL_S = int(os.getenv("MARKET_DATA_CACHE_TTL_OPTIONS_S", "300"))
_INTRADAY_TTL_S = int(os.getenv("MARKET_DATA_CACHE_TTL_INTRADAY_S", "300"))
_EQUITY_DAILY_TTL_S = int(os.getenv("MARKET_DATA_CACHE_TTL_EQUITY_DAILY_S", "1800"))
_NEGATIVE_TTL_S = int(os.getenv("MARKET_DATA_CACHE_NEGATIVE_TTL_S", "120"))

_OPTIONS_CHAIN_CACHE: TTLCache[str, tuple[dict[str, Any] | None, str, dict[str, Any]]] = TTLCache(
    maxsize=256,
    ttl=_OPTIONS_TTL_S,
)
_OPTIONS_NEGATIVE_CACHE: TTLCache[str, bool] = TTLCache(maxsize=128, ttl=_NEGATIVE_TTL_S)
_INTRADAY_CACHE: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=1024, ttl=_INTRADAY_TTL_S)
_EQUITY_DAILY_CACHE: TTLCache[str, tuple[list[float], list[dict[str, Any]], dict[str, Any]]] = (
    TTLCache(maxsize=512, ttl=_EQUITY_DAILY_TTL_S)
)


def five_minute_bucket_key(symbol: str, *, suffix: str = "") -> str:
    """Clave alineada al bucket GEX de 5 minutos."""
    now = datetime.now(tz=UTC)
    minute = (now.minute // 5) * 5
    bucket = now.replace(minute=minute, second=0, microsecond=0)
    base = f"{symbol.upper()}:{bucket.isoformat()}"
    return f"{base}:{suffix}" if suffix else base


def cache_metrics() -> dict[str, int]:
    with _LOCK:
        return dict(_METRICS)


def get_massive_options_chain(
    symbol: str,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]] | None:
    """Devuelve cadena Massive cacheada o None si miss / negative."""
    key = five_minute_bucket_key(symbol.upper(), suffix="options_chain")
    with _LOCK:
        if key in _OPTIONS_NEGATIVE_CACHE:
            _METRICS["negative_hits"] += 1
            logger.debug("market_data_cache.options_negative_hit symbol=%s", symbol.upper())
            return None, "", {}
        if key in _OPTIONS_CHAIN_CACHE:
            _METRICS["hits"] += 1
            shaped, src, meta = _OPTIONS_CHAIN_CACHE[key]
            logger.debug("market_data_cache.options_hit symbol=%s", symbol.upper())
            return shaped, src, {**meta, "cache_hit": True}
        _METRICS["misses"] += 1
    return None


def put_massive_options_chain(
    symbol: str,
    payload: tuple[dict[str, Any] | None, str, dict[str, Any]],
) -> None:
    key = five_minute_bucket_key(symbol.upper(), suffix="options_chain")
    shaped, _src, _meta = payload
    with _LOCK:
        if shaped and shaped.get("data"):
            _OPTIONS_CHAIN_CACHE[key] = payload
            _OPTIONS_NEGATIVE_CACHE.pop(key, None)
        else:
            _OPTIONS_NEGATIVE_CACHE[key] = True


def get_intraday_bars(cache_key: str) -> dict[str, Any] | None:
    with _LOCK:
        hit = _INTRADAY_CACHE.get(cache_key)
        if hit is not None:
            _METRICS["hits"] += 1
            return {**hit, "cache_hit": True}
        _METRICS["misses"] += 1
    return None


def put_intraday_bars(cache_key: str, payload: dict[str, Any]) -> None:
    with _LOCK:
        _INTRADAY_CACHE[cache_key] = payload


def intraday_cache_key(
    symbol: str,
    interval: str,
    *,
    max_bars: int | None,
    lookback_days: int | None,
    accept_stale: bool,
) -> str:
    return five_minute_bucket_key(
        symbol.upper(),
        suffix=f"intraday:{interval}:{max_bars or 0}:{lookback_days or 0}:{int(accept_stale)}",
    )


def get_equity_daily_bars(
    symbol: str,
) -> tuple[list[float], list[dict[str, Any]], dict[str, Any]] | None:
    key = five_minute_bucket_key(symbol.upper(), suffix="equity_daily")
    with _LOCK:
        hit = _EQUITY_DAILY_CACHE.get(key)
        if hit is not None:
            _METRICS["hits"] += 1
            return hit
        _METRICS["misses"] += 1
    return None


def put_equity_daily_bars(
    symbol: str,
    closes: list[float],
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    key = five_minute_bucket_key(symbol.upper(), suffix="equity_daily")
    with _LOCK:
        _EQUITY_DAILY_CACHE[key] = (closes, rows, meta)


__all__ = [
    "cache_metrics",
    "five_minute_bucket_key",
    "get_equity_daily_bars",
    "get_intraday_bars",
    "get_massive_options_chain",
    "intraday_cache_key",
    "put_equity_daily_bars",
    "put_intraday_bars",
    "put_massive_options_chain",
]
