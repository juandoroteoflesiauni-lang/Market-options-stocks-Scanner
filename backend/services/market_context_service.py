"""Market context payload builder for technical and dashboard surfaces."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

_BENCHMARK_SYMBOLS = ("SPY", "QQQ", "IWM", "DIA", "TLT", "DXY")
_VIX_SYMBOLS = ("^VIX", "VIX")


class _FMPClientLike(Protocol):
    def get_quote(self, symbol: str) -> Awaitable[object | None]: ...

    def get_quotes(self, symbols: list[str]) -> Awaitable[object]: ...

    def get_treasury_rates(self, from_date: str, to_date: str) -> Awaitable[object]: ...


class _FredFetcherLike(Protocol):
    def get_vix_close(self) -> Awaitable[object | None]: ...


class _SettingsLike(Protocol):
    market_context_refresh_seconds: int
    vix_fallback_fred_enabled: bool


class _LocalAsyncTTLCache:
    def __init__(self, maxsize: int = 512) -> None:
        self._maxsize = max(1, int(maxsize))
        self._lock = asyncio.Lock()
        self._items: dict[str, tuple[float, object]] = {}

    async def get(self, key: str) -> object | None:
        now = asyncio.get_running_loop().time()
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at < now:
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: object, *, ttl_secs: float) -> None:
        now = asyncio.get_running_loop().time()
        async with self._lock:
            if len(self._items) >= self._maxsize:
                oldest = min(self._items, key=lambda item_key: self._items[item_key][0])
                self._items.pop(oldest, None)
            self._items[key] = (now + max(1.0, float(ttl_secs)), value)


_CONTEXT_CACHE = _LocalAsyncTTLCache(maxsize=512)


async def build_market_context_payload(
    symbol: str,
    *,
    fmp_client: _FMPClientLike | None = None,
    fred_fetcher: _FredFetcherLike | None = None,
    settings: _SettingsLike | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Build the public market-context payload with cheap, cached snapshots."""
    sym = symbol.upper().strip()
    cfg = settings or _load_settings()
    refresh_seconds = max(15, int(cfg.market_context_refresh_seconds or 60))
    cache_key = f"market_context:{sym}"
    if use_cache:
        cached = await _CONTEXT_CACHE.get(cache_key)
        if isinstance(cached, dict):
            return cached

    fmp = fmp_client if fmp_client is not None else _make_fmp_client()
    fred = fred_fetcher if fred_fetcher is not None else _make_fred_fetcher()
    as_of = _utc_now()

    vix_task = _fetch_vix_context(fmp, fred, cfg)
    quotes_task = _fetch_benchmark_quotes(fmp)
    rates_task = _fetch_rates_context(fmp)
    vix, quote_points, rates = await asyncio.gather(vix_task, quotes_task, rates_task)

    fx = {"DXY": quote_points.get("DXY")} if quote_points.get("DXY") else {}
    benchmarks = {k: v for k, v in quote_points.items() if k != "DXY"}
    risk_regime = _derive_risk_regime(vix.get("value"), rates.get("curve_10y2y"), benchmarks)
    vix_freshness = str(vix.get("freshness") or "unavailable")
    if vix.get("stale_ok"):
        vix_freshness = f"{vix_freshness}/stale_ok"

    payload: dict[str, Any] = {
        "symbol": sym,
        "as_of": as_of,
        "vix": vix,
        "rates": rates,
        "fx": fx,
        "benchmarks": benchmarks,
        "risk_regime": risk_regime,
        "sources": {
            "vix": vix.get("source"),
            "rates": rates.get("source"),
            "fx": "fmp_quote" if fx else None,
            "benchmarks": "fmp_quote" if benchmarks else None,
        },
        "freshness": {
            "vix": vix_freshness,
            "rates": rates.get("freshness") or "unavailable",
            "fx": "snapshot" if fx else "unavailable",
            "benchmarks": "snapshot" if benchmarks else "unavailable",
            "cache_ttl_seconds": refresh_seconds,
        },
    }
    if use_cache:
        await _CONTEXT_CACHE.set(cache_key, payload, ttl_secs=refresh_seconds)
    return payload


async def _fetch_vix_context(
    fmp: _FMPClientLike,
    fred: _FredFetcherLike,
    settings: _SettingsLike,
) -> dict[str, Any]:
    for raw_symbol in _VIX_SYMBOLS:
        quote = await _safe_await(fmp.get_quote(raw_symbol), f"fmp_quote:{raw_symbol}")
        point = _quote_point(
            quote, source="fmp_quote", freshness="snapshot", fallback_symbol="^VIX"
        )
        if point["value"] is not None:
            point["description"] = "CBOE VIX volatility expectation proxy via quote snapshot"
            point["stale_ok"] = False
            return point

    if bool(settings.vix_fallback_fred_enabled):
        fred_data = await _safe_await(fred.get_vix_close(), "fred_vixcls")
        if isinstance(fred_data, dict):
            value = _finite_float(fred_data.get("value"))
            if value is not None:
                return {
                    "symbol": "^VIX",
                    "value": value,
                    "change": None,
                    "change_percent": None,
                    "as_of": fred_data.get("date"),
                    "source": str(fred_data.get("source") or "fred_vixcls"),
                    "freshness": "daily_close",
                    "stale_ok": True,
                    "description": "FRED VIXCLS daily close fallback; not realtime",
                }

    return {
        "symbol": "^VIX",
        "value": None,
        "change": None,
        "change_percent": None,
        "as_of": None,
        "source": None,
        "freshness": "unavailable",
        "stale_ok": False,
        "description": "VIX unavailable",
    }


async def _fetch_benchmark_quotes(fmp: _FMPClientLike) -> dict[str, dict[str, Any]]:
    quotes = await _safe_await(fmp.get_quotes(list(_BENCHMARK_SYMBOLS)), "fmp_benchmarks")
    if not isinstance(quotes, dict):
        return {}
    points: dict[str, dict[str, Any]] = {}
    for raw_symbol, quote in quotes.items():
        sym = str(raw_symbol or getattr(quote, "symbol", "") or "").upper().strip()
        point = _quote_point(quote, source="fmp_quote", freshness="snapshot", fallback_symbol=sym)
        if sym and point["value"] is not None:
            points[sym] = point
    return points


async def _fetch_rates_context(fmp: _FMPClientLike) -> dict[str, Any]:
    today = datetime.now(tz=UTC).date()
    from_date = today - timedelta(days=14)
    rows = await _safe_await(
        fmp.get_treasury_rates(from_date.isoformat(), today.isoformat()),
        "fmp_treasury",
    )
    if not isinstance(rows, list) or not rows:
        return {
            "us_2y": None,
            "us_10y": None,
            "us_30y": None,
            "curve_10y2y": None,
            "as_of": None,
            "source": None,
            "freshness": "unavailable",
        }
    latest = max(rows, key=lambda row: str(getattr(row, "date", "") or ""))
    year2 = _finite_float(getattr(latest, "year2", None))
    year10 = _finite_float(getattr(latest, "year10", None))
    year30 = _finite_float(getattr(latest, "year30", None))
    curve = _round4(year10 - year2) if year10 is not None and year2 is not None else None
    return {
        "us_2y": year2,
        "us_10y": year10,
        "us_30y": year30,
        "curve_10y2y": curve,
        "as_of": getattr(latest, "date", None),
        "source": "fmp_treasury",
        "freshness": "daily_snapshot",
    }


def _derive_risk_regime(
    vix_value: object,
    curve_10y2y: object,
    benchmarks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    score = 50
    drivers: list[str] = []
    vix = _finite_float(vix_value)
    if vix is not None:
        if vix >= 30:
            score -= 25
            drivers.append("VIX stress above 30")
        elif vix >= 22:
            score -= 12
            drivers.append("VIX elevated above 22")
        elif vix <= 16:
            score += 10
            drivers.append("VIX subdued below 16")

    curve = _finite_float(curve_10y2y)
    if curve is not None and curve < -0.1:
        score -= 8
        drivers.append("2s10s curve inverted")

    spy_change = _finite_float((benchmarks.get("SPY") or {}).get("change_percent"))
    if spy_change is not None:
        if spy_change <= -1:
            score -= 6
            drivers.append("SPY benchmark under pressure")
        elif spy_change >= 1:
            score += 6
            drivers.append("SPY benchmark bid")

    score = max(0, min(100, score))
    if score <= 30:
        label = "risk_off"
    elif score <= 45:
        label = "fragile"
    elif score >= 65:
        label = "risk_on"
    else:
        label = "neutral"
    return {"label": label, "score": score, "drivers": drivers}


def _quote_point(
    quote: object | None,
    *,
    source: str,
    freshness: str,
    fallback_symbol: str,
) -> dict[str, Any]:
    value = _finite_float(getattr(quote, "price", None)) if quote is not None else None
    return {
        "symbol": str(getattr(quote, "symbol", fallback_symbol) or fallback_symbol).upper(),
        "value": value,
        "change": _finite_float(getattr(quote, "change", None)) if quote is not None else None,
        "change_percent": (
            _finite_float(getattr(quote, "changesPercentage", None)) if quote is not None else None
        ),
        "as_of": _quote_as_of(getattr(quote, "timestamp", None)) if quote is not None else None,
        "source": source if value is not None else None,
        "freshness": freshness if value is not None else "unavailable",
        "stale_ok": False,
    }


async def _safe_await(awaitable: Awaitable[object | None], label: str) -> object | None:
    try:
        return await awaitable
    except Exception as exc:
        logger.debug("market_context: %s unavailable: %s", label, exc)
        return None


def _finite_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _round4(value: float) -> float:
    return round(value, 4)


def _quote_as_of(timestamp: object) -> str | None:
    try:
        raw = int(float(timestamp))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    if raw > 1_000_000_000_000:
        raw //= 1000
    return datetime.fromtimestamp(raw, tz=UTC).isoformat()


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _load_settings() -> _SettingsLike:
    from backend.config.settings import load_settings

    return cast(_SettingsLike, load_settings())


def _make_fmp_client() -> _FMPClientLike:
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

    return cast(_FMPClientLike, FMPClient())


def _make_fred_fetcher() -> _FredFetcherLike:
    from backend.layer_1_data.fetchers.fred_fetcher import FredFetcher

    return FredFetcher()
