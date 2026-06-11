"""Command Center payload assembler for the institutional home surface."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar, cast

from backend.config.logger_setup import get_logger
from backend.services.market_context_service import build_market_context_payload

logger = get_logger(__name__)

T = TypeVar("T")

MarketContextProvider = Callable[[str], Awaitable[dict[str, Any]]]
ArgentinaProvider = Callable[[], Awaitable[dict[str, Any] | None]]
OptionsProvider = Callable[[str], Awaitable[object]]
ThesisProvider = Callable[[str], Awaitable[object]]
ScannerProvider = Callable[[list[str]], Awaitable[object]]


class FMPClientLike(Protocol):
    def get_quote(self, symbol: str) -> Awaitable[object]: ...

    def get_stock_news(self, symbol: str, limit: int = 12) -> Awaitable[list[object]]: ...

    def get_economic_calendar(self, from_date: str, to_date: str) -> Awaitable[list[object]]: ...

    def get_earnings_calendar(self, from_date: str, to_date: str) -> Awaitable[list[object]]: ...

    def get_key_metrics_ttm(self, symbol: str) -> Awaitable[object]: ...

    def get_financial_scores(self, symbol: str) -> Awaitable[object]: ...

    def get_income_statements(
        self, symbol: str, limit: int = 2, period: str = "quarter"
    ) -> Awaitable[list[object]]: ...

    def get_balance_sheets(
        self, symbol: str, limit: int = 2, period: str = "quarter"
    ) -> Awaitable[list[object]]: ...

    def get_cash_flow_statements(
        self, symbol: str, limit: int = 2, period: str = "quarter"
    ) -> Awaitable[list[object]]: ...

    def get_price_target_consensus(self, symbol: str) -> Awaitable[object]: ...

    def get_stock_recommendations(self, symbol: str) -> Awaitable[list[object]]: ...

    def get_historical_prices(
        self, symbol: str, date_from: str | None = None
    ) -> Awaitable[list[object]]: ...


class _AsyncTTLCache:
    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = max(1, maxsize)
        self._lock = asyncio.Lock()
        self._items: dict[str, tuple[float, object]] = {}

    async def get(self, key: str, accept_stale: bool = False) -> object | None:
        now = time.monotonic()
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now and not accept_stale:
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: object, *, ttl_seconds: int) -> None:
        async with self._lock:
            if len(self._items) >= self._maxsize:
                oldest = min(self._items, key=lambda item_key: self._items[item_key][0])
                self._items.pop(oldest, None)
            self._items[key] = (time.monotonic() + max(1, ttl_seconds), value)


_BLOCK_CACHE = _AsyncTTLCache(maxsize=512)
_BLOCK_TTLS = {
    "market_context": 45,
    "active_asset": 45,
    "news": 300,
    "events": 900,
    "fundamentals": 21_600,
    "volatility": 45,
    "ai": 300,
    "argentina": 45,
    "scanner": 180,
}


async def build_command_center_payload(
    symbol: str,
    *,
    fmp_client: FMPClientLike | None = None,
    market_context_provider: MarketContextProvider = build_market_context_payload,
    argentina_provider: ArgentinaProvider | None = None,
    options_provider: OptionsProvider | None = None,
    thesis_provider: ThesisProvider | None = None,
    scanner_provider: ScannerProvider | None = None,
    use_cache: bool = True,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Build the home Command Center payload with per-block degradation."""
    sym = _normalize_symbol(symbol)
    now = _utc_iso()
    fmp = fmp_client if fmp_client is not None else _make_fmp_client()
    argentina = argentina_provider or _default_argentina_provider
    options = options_provider or _default_options_provider
    thesis = thesis_provider or _default_thesis_provider
    scanner = scanner_provider or _default_scanner_provider

    market_context, market_error = await _cached_value(
        f"{sym}:market_context",
        "market_context",
        use_cache,
        lambda: market_context_provider(sym),
        timeout_seconds=timeout_seconds,
    )
    market_context = market_context if isinstance(market_context, dict) else {}

    collectors = {
        "active_asset": lambda: _collect_active_asset(sym, fmp),
        "news": lambda: _collect_news(sym, fmp),
        "events": lambda: _collect_events(sym, fmp),
        "fundamentals": lambda: _collect_fundamentals(sym, fmp),
        "volatility": lambda: _collect_volatility(sym, options),
        "ai": lambda: _collect_ai(sym, thesis),
        "argentina": lambda: _collect_argentina(argentina),
        "scanner": lambda: _collect_scanner([sym], scanner),
    }

    collected = await asyncio.gather(
        *(
            _cached_block(
                sym,
                block_name,
                use_cache,
                collect,
                timeout_seconds=timeout_seconds,
            )
            for block_name, collect in collectors.items()
        )
    )
    block_map = dict(zip(collectors.keys(), collected, strict=True))

    blocks: dict[str, dict[str, Any]] = {
        "session": _build_session_block(now),
        "global_pulse": _build_global_pulse_block(market_context, market_error, now),
        "macro": _build_macro_block(market_context, market_error, now),
        "rates": _build_rates_block(market_context, market_error, now),
        **block_map,
    }
    health = _build_health(blocks, now)

    return {
        "as_of": now,
        "symbol": sym,
        **blocks,
        "health": health,
    }


async def _cached_value(
    key: str,
    block_name: str,
    use_cache: bool,
    collect: Callable[[], Awaitable[T]],
    *,
    timeout_seconds: float,
) -> tuple[T | None, str | None]:
    if use_cache:
        cached = await _BLOCK_CACHE.get(key)
        if cached is not None:
            return cast(T, cached), None
    try:
        value = await asyncio.wait_for(collect(), timeout=max(0.1, timeout_seconds))
        if use_cache:
            await _BLOCK_CACHE.set(key, value, ttl_seconds=_BLOCK_TTLS.get(block_name, 60))
        return value, None
    except Exception as exc:
        if use_cache:
            stale = await _BLOCK_CACHE.get(key, accept_stale=True)
            if stale is not None:
                logger.info(
                    "command_center.%s fetch failed, using stale cache: %s",
                    block_name,
                    str(exc)[:180],
                )
                return cast(T, stale), None
        logger.warning("command_center.%s degraded: %s", block_name, str(exc)[:180])
        return None, str(exc)[:240]


async def _cached_block(
    symbol: str,
    block_name: str,
    use_cache: bool,
    collect: Callable[[], Awaitable[dict[str, Any]]],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    value, error = await _cached_value(
        f"{symbol}:{block_name}",
        block_name,
        use_cache,
        collect,
        timeout_seconds=timeout_seconds,
    )
    if isinstance(value, dict):
        return value
    return _block(
        source=_source_for_block(block_name),
        freshness=_freshness_for_block(block_name),
        status="available",
        degraded_reason=error or "source unavailable",
        data=_empty_data_for_block(block_name),
    )


async def _collect_active_asset(symbol: str, fmp: FMPClientLike) -> dict[str, Any]:
    date_from = (datetime.now(tz=UTC).date() - timedelta(days=180)).isoformat()
    quote_res, history_res = await asyncio.gather(
        fmp.get_quote(symbol),
        fmp.get_historical_prices(symbol, date_from=date_from),
    )
    quote = _to_plain(quote_res)
    history = _to_plain(history_res)
    candles = [
        {
            "time": _get(row, "date") or _get(row, "time"),
            "open": _num(_get(row, "open")),
            "high": _num(_get(row, "high")),
            "low": _num(_get(row, "low")),
            "close": _num(_get(row, "close")),
            "volume": _num(_get(row, "volume")),
        }
        for row in (history if isinstance(history, list) else [])
        if _get(row, "close") is not None
    ][-90:]
    return _block(
        source="fmp_quote + fmp_historical",
        freshness="snapshot",
        data={
            "quote": {
                "symbol": _get(quote, "symbol") or symbol,
                "name": _get(quote, "name"),
                "price": _num(_get(quote, "price")),
                "change": _num(_get(quote, "change")),
                "change_percent": _num(_get(quote, "changesPercentage")),
                "volume": _num(_get(quote, "volume")),
                "market_cap": _num(_get(quote, "marketCap")),
                "pe": _num(_get(quote, "pe")),
                "eps": _num(_get(quote, "eps")),
                "earnings_announcement": _get(quote, "earningsAnnouncement"),
            },
            "chart": {"candles": candles},
        },
    )


async def _collect_news(symbol: str, fmp: FMPClientLike) -> dict[str, Any]:
    items = await fmp.get_stock_news(symbol, limit=12)
    return _block(
        source="fmp_stock_news",
        freshness="5m",
        data={
            "items": [
                {
                    "symbol": _get(item, "symbol") or symbol,
                    "published_date": _get(item, "publishedDate") or _get(item, "published_date"),
                    "title": _get(item, "title"),
                    "source": _get(item, "site") or _get(item, "source") or "unknown",
                    "url": _get(item, "url"),
                    "summary": _get(item, "text") or _get(item, "summary"),
                }
                for item in _as_list(items)
                if _get(item, "title")
            ],
        },
    )


async def _collect_events(symbol: str, fmp: FMPClientLike) -> dict[str, Any]:
    today = datetime.now(tz=UTC).date()
    to_date = today + timedelta(days=45)
    economic, earnings = await asyncio.gather(
        fmp.get_economic_calendar(today.isoformat(), to_date.isoformat()),
        fmp.get_earnings_calendar(today.isoformat(), to_date.isoformat()),
    )
    return _block(
        source="fmp_economic_calendar + fmp_earnings_calendar",
        freshness="15m",
        data={
            "economic_calendar": [_compact_event(item) for item in _as_list(economic)[:12]],
            "earnings_calendar": [
                _compact_earnings_event(item, symbol) for item in _as_list(earnings)[:12]
            ],
        },
    )


async def _collect_fundamentals(symbol: str, fmp: FMPClientLike) -> dict[str, Any]:
    (
        quote_res,
        key_metrics_res,
        financial_scores_res,
        income_res,
        balance_res,
        cash_flow_res,
        targets_res,
        recs_res,
    ) = await asyncio.gather(
        fmp.get_quote(symbol),
        fmp.get_key_metrics_ttm(symbol),
        fmp.get_financial_scores(symbol),
        fmp.get_income_statements(symbol, limit=2, period="quarter"),
        fmp.get_balance_sheets(symbol, limit=2, period="quarter"),
        fmp.get_cash_flow_statements(symbol, limit=2, period="quarter"),
        fmp.get_price_target_consensus(symbol),
        fmp.get_stock_recommendations(symbol),
    )
    quote = _to_plain(quote_res)
    key_metrics = _to_plain(key_metrics_res)
    scores = _to_plain(financial_scores_res)
    targets = _to_plain(targets_res)
    recommendations = _as_list(_to_plain(recs_res))
    return _block(
        source="fmp_fundamentals",
        freshness="6h",
        data={
            "metrics": {
                "market_cap": _num(_get(quote, "marketCap")),
                "pe": _num(_get(quote, "pe")),
                "eps": _num(_get(quote, "eps")),
                "roe": _num(_get(key_metrics, "roeTTM")),
                "debt_to_equity": _num(_get(key_metrics, "debtToEquityTTM")),
                "free_cash_flow_yield": _num(_get(key_metrics, "freeCashFlowYieldTTM")),
                "altman_z": _num(_get(scores, "altmanZScore")),
                "piotroski": _num(_get(scores, "piotroskiScore")),
                "target_consensus": _num(_get(targets, "targetConsensus")),
                "target_high": _num(_get(targets, "targetHigh")),
                "target_low": _num(_get(targets, "targetLow")),
            },
            "statements": {
                "income": _as_list(_to_plain(income_res))[:2],
                "balance": _as_list(_to_plain(balance_res))[:2],
                "cash_flow": _as_list(_to_plain(cash_flow_res))[:2],
            },
            "recommendations": recommendations[:4],
        },
    )


async def _collect_volatility(symbol: str, options_provider: OptionsProvider) -> dict[str, Any]:
    snapshot = _to_plain(await options_provider(symbol))
    chain = _as_list(_get(snapshot, "chain"))
    call_oi = sum(_num(_get(row, "call_oi")) or 0.0 for row in chain)
    put_oi = sum(_num(_get(row, "put_oi")) or 0.0 for row in chain)
    put_call_ratio = put_oi / call_oi if call_oi > 0 else None
    iv_surface = _get(snapshot, "iv_surface") or {}
    return _block(
        source="options_snapshot",
        freshness="snapshot",
        data={
            "iv_rank": _num(_get(iv_surface, "iv_rank")),
            "atm_iv": _num(_get(iv_surface, "atm_iv")),
            "put_call_ratio": put_call_ratio,
            "call_open_interest": call_oi or None,
            "put_open_interest": put_oi or None,
            "chain_count": len(chain),
            "levels": _get(snapshot, "levels") or _get(snapshot, "gex_levels") or [],
        },
    )


async def _collect_ai(symbol: str, thesis_provider: ThesisProvider) -> dict[str, Any]:
    thesis = _to_plain(await thesis_provider(symbol))
    return _block(
        source="probabilistic_thesis",
        freshness="5m",
        data={
            "symbol": _get(thesis, "symbol") or symbol,
            "bias": _get(thesis, "bias"),
            "conviction": _num(_get(thesis, "conviction")),
            "thesis": _get(thesis, "thesis"),
            "timestamp": _get(thesis, "timestamp"),
            "report": _get(thesis, "institutional_report"),
        },
    )


async def _collect_argentina(argentina_provider: ArgentinaProvider) -> dict[str, Any]:
    summary = _to_plain(await argentina_provider())
    summary = summary if isinstance(summary, dict) else {}
    return _block(
        source="argentina/market-summary",
        freshness="snapshot",
        data={
            "status_label": _get(summary, "status"),
            "fx": _get(summary, "fx") or {},
            "risk_country": _get(summary, "risk_country") or _get(summary, "country_risk"),
            "bonds": _as_list(_get(summary, "bonds"))[:8],
            "arbitrage": _as_list(_get(summary, "arbitrage"))[:6],
        },
    )


async def _collect_scanner(symbols: list[str], scanner_provider: ScannerProvider) -> dict[str, Any]:
    context = _to_plain(await scanner_provider(symbols))
    context = context if isinstance(context, dict) else {}
    return _block(
        source="market-scanner/context",
        freshness="3m",
        data={
            "market_brief": _as_list(_get(context, "market_brief"))[:8],
            "news": _as_list(_get(context, "news"))[:8],
            "sources": _get(context, "sources") or {},
        },
    )


def _build_session_block(now: str) -> dict[str, Any]:
    current = datetime.now(tz=UTC)
    minutes = current.hour * 60 + current.minute
    is_weekday = current.weekday() < 5
    if is_weekday and 13 * 60 + 30 <= minutes <= 20 * 60:
        label = "regular"
    elif is_weekday and 9 * 60 <= minutes < 13 * 60 + 30:
        label = "pre-market"
    elif is_weekday and 20 * 60 < minutes <= 24 * 60:
        label = "after-hours"
    else:
        label = "closed"
    return _block(
        source="system_clock",
        freshness="live",
        updated_at=now,
        data={
            "label": label,
            "timezone": "UTC",
            "modes": ["default", "macro", "earnings", "argentina", "risk"],
        },
    )


def _build_global_pulse_block(
    market_context: dict[str, Any], error: str | None, now: str
) -> dict[str, Any]:
    if error:
        return _block(
            source="market_context_service",
            freshness="snapshot",
            status="available",
            updated_at=now,
            degraded_reason=error,
            data={"items": [], "risk_regime": None},
        )
    benchmarks = _as_dict(_get(market_context, "benchmarks"))
    fx = _as_dict(_get(market_context, "fx"))
    vix = _as_dict(_get(market_context, "vix"))
    items = [_market_point(symbol, point) for symbol, point in benchmarks.items()]
    if vix:
        items.append(_market_point("^VIX", vix))
    if isinstance(fx, dict):
        items.extend(_market_point(symbol, point) for symbol, point in fx.items())
    sources = _as_dict(_get(market_context, "sources"))
    freshness = _as_dict(_get(market_context, "freshness"))
    return _block(
        source=str(sources.get("benchmarks") or "market_context_service"),
        freshness=str(freshness.get("benchmarks") or "snapshot"),
        updated_at=now,
        data={
            "items": [item for item in items if item["value"] is not None],
            "risk_regime": _get(market_context, "risk_regime"),
        },
    )


def _build_macro_block(
    market_context: dict[str, Any], error: str | None, now: str
) -> dict[str, Any]:
    if error:
        return _block(
            source="market_context_service",
            freshness="snapshot",
            status="available",
            updated_at=now,
            degraded_reason=error,
            data={"risk_regime": None, "drivers": []},
        )
    risk = _as_dict(_get(market_context, "risk_regime"))
    fx = _as_dict(_get(market_context, "fx"))
    return _block(
        source="market_context_service",
        freshness="snapshot",
        updated_at=now,
        data={
            "risk_regime": risk,
            "drivers": _as_list(_get(risk, "drivers")),
            "vix": _get(market_context, "vix"),
            "dxy": fx.get("DXY"),
        },
    )


def _build_rates_block(
    market_context: dict[str, Any], error: str | None, now: str
) -> dict[str, Any]:
    rates = _as_dict(_get(market_context, "rates"))
    if error:
        return _block(
            source="market_context_service",
            freshness="daily_snapshot",
            status="available",
            updated_at=now,
            degraded_reason=error,
            data={"items": [], "curve_10y2y": None},
        )
    items = [
        {"label": "US 2Y", "value": _num(_get(rates, "us_2y"))},
        {"label": "US 10Y", "value": _num(_get(rates, "us_10y"))},
        {"label": "US 30Y", "value": _num(_get(rates, "us_30y"))},
    ]
    return _block(
        source=str(_get(rates, "source") or "fmp_treasury"),
        freshness=str(_get(rates, "freshness") or "daily_snapshot"),
        updated_at=now,
        data={
            "items": [item for item in items if item["value"] is not None],
            "curve_10y2y": _num(_get(rates, "curve_10y2y")),
        },
    )


def _build_health(blocks: dict[str, dict[str, Any]], now: str) -> dict[str, Any]:
    degraded = [name for name, block in blocks.items() if str(block.get("status")) != "available"]
    return {
        "source": "command_center_service",
        "freshness": "live",
        "status": "degraded" if degraded else "available",
        "updated_at": now,
        "degraded_reason": ", ".join(degraded) if degraded else None,
        "degraded": bool(degraded),
        "degraded_blocks": degraded,
        "sources": {name: block.get("source") for name, block in blocks.items()},
    }


def _block(
    *,
    source: str,
    freshness: str,
    data: dict[str, Any],
    status: str = "available",
    updated_at: str | None = None,
    degraded_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "freshness": freshness,
        "status": status,
        "updated_at": updated_at or _utc_iso(),
        "degraded_reason": degraded_reason,
        **data,
    }


def _source_for_block(block_name: str) -> str:
    return {
        "active_asset": "fmp_quote + fmp_historical",
        "news": "fmp_stock_news",
        "events": "fmp_calendar",
        "fundamentals": "fmp_fundamentals",
        "volatility": "options_snapshot",
        "ai": "probabilistic_thesis",
        "argentina": "argentina/market-summary",
        "scanner": "market-scanner/context",
    }.get(block_name, "command_center_service")


def _freshness_for_block(block_name: str) -> str:
    return {
        "active_asset": "snapshot",
        "news": "5m",
        "events": "15m",
        "fundamentals": "6h",
        "volatility": "snapshot",
        "ai": "5m",
        "argentina": "snapshot",
        "scanner": "3m",
    }.get(block_name, "snapshot")


def _empty_data_for_block(block_name: str) -> dict[str, Any]:
    empty: dict[str, dict[str, Any]] = {
        "active_asset": {"quote": {}, "chart": {"candles": []}},
        "news": {"items": []},
        "events": {"economic_calendar": [], "earnings_calendar": []},
        "fundamentals": {"metrics": {}, "statements": {}, "recommendations": []},
        "volatility": {
            "iv_rank": None,
            "atm_iv": None,
            "put_call_ratio": None,
            "call_open_interest": None,
            "put_open_interest": None,
            "chain_count": 0,
            "levels": [],
        },
        "ai": {"bias": None, "conviction": None, "thesis": None, "timestamp": None},
        "argentina": {
            "status_label": None,
            "fx": {},
            "risk_country": None,
            "bonds": [],
            "arbitrage": [],
        },
        "scanner": {"market_brief": [], "news": [], "sources": {}},
    }
    return empty.get(block_name, {})


async def _default_argentina_provider() -> dict[str, Any] | None:
    from backend.services.market_scanner_context_service import fetch_argentina_summary

    return await fetch_argentina_summary()


async def _default_options_provider(symbol: str) -> object:
    from backend.routers.options_router import options_snapshot_service

    return await options_snapshot_service(symbol, None, 0.05)


async def _default_thesis_provider(symbol: str) -> object:
    from backend.routers.probabilistic_router import get_ai_thesis

    return await get_ai_thesis(symbol, include_snapshot=False)


async def _default_scanner_provider(symbols: list[str]) -> object:
    from backend.domain.market_scanner_models import MarketScannerContextRequest
    from backend.services.market_scanner_context_service import build_market_scanner_context

    universe = (
        "argentina_plus" if any(symbol.endswith(".BA") for symbol in symbols) else "wall_street"
    )
    request = MarketScannerContextRequest(
        universe=universe,
        symbols=symbols,
        leaders=symbols[:5],
        limit_per_symbol=2,
    )
    return await build_market_scanner_context(request)


def _make_fmp_client() -> FMPClientLike:
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

    return cast(FMPClientLike, FMPClient())


def _normalize_symbol(symbol: str) -> str:
    cleaned = (symbol or "NVDA").upper().strip()
    return cleaned or "NVDA"


def _utc_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _to_plain(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_to_plain(v) for v in value]
    if hasattr(value, "__dict__"):
        return {str(k): _to_plain(v) for k, v in vars(value).items() if not k.startswith("_")}
    return value


def _get(value: object, key: str, default: object = None) -> object:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _num(value: object) -> float | None:
    if not isinstance(value, str | bytes | int | float):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(n) or math.isinf(n):
        return None
    return n


def _market_point(symbol: str, point: object) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "value": _num(_get(point, "value") or _get(point, "price")),
        "change": _num(_get(point, "change")),
        "change_percent": _num(_get(point, "change_percent") or _get(point, "changesPercentage")),
        "source": _get(point, "source"),
    }


def _compact_event(item: object) -> dict[str, Any]:
    return {
        "date": _get(item, "date"),
        "event": _get(item, "event") or _get(item, "name") or _get(item, "title"),
        "country": _get(item, "country"),
        "impact": _get(item, "impact"),
        "actual": _get(item, "actual"),
        "estimate": _get(item, "estimate"),
        "previous": _get(item, "previous"),
    }


def _compact_earnings_event(item: object, active_symbol: str) -> dict[str, Any]:
    return {
        "date": _get(item, "date"),
        "symbol": _get(item, "symbol") or active_symbol,
        "eps_estimated": _num(_get(item, "epsEstimated")),
        "revenue_estimated": _num(_get(item, "revenueEstimated")),
        "time": _get(item, "time"),
    }
