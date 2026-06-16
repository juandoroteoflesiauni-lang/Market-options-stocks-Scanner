"""Construye enriquecimiento R1: L2, 5m, motores híbridos y predictivo. # [PD-3][TH]"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.r1_enrichment_thresholds import (
    INTRADAY_5M_INTERVAL,
    INTRADAY_5M_LOOKBACK_DAYS,
    INTRADAY_5M_MAX_BARS,
    INTRADAY_5M_MIN_BARS,
)
from backend.domain.alpaca_options_models import Route1OptionsSnapshotContext
from backend.layer_1_data.datos.intraday_bars_fetcher import fetch_intraday_bars
from backend.models.options_strategy import R1EnrichmentContext
from backend.services.alpaca_r1_options_confluence import OptionsConfluenceScorer
from backend.services.alpaca_r1_options_replay import AlpacaR1OptionsReplay
from backend.services.alpaca_route1_context_service import fetch_route1_predictive_meta

logger = get_logger(__name__)


def _run_async(coro: Any) -> Any:
    """Ejecuta coroutine desde contexto sync o async."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _fetch_intraday_5m(symbol: str) -> tuple[tuple[dict[str, Any], ...], str]:
    """Barras 5m para replay híbrido (Massive/Polygon/Alpaca/FMP)."""
    payload = fetch_intraday_bars(
        symbol,
        INTRADAY_5M_INTERVAL,
        lookback_days=INTRADAY_5M_LOOKBACK_DAYS,
        max_bars=INTRADAY_5M_MAX_BARS,
        accept_stale_current_session=True,
    )
    bars = payload.get("bars") or []
    if not isinstance(bars, list) or len(bars) < INTRADAY_5M_MIN_BARS:
        return (), str(payload.get("source") or "")
    normalized: list[dict[str, Any]] = []
    for row in bars:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                "open": row.get("open", row.get("o")),
                "high": row.get("high", row.get("h")),
                "low": row.get("low", row.get("l")),
                "close": row.get("close", row.get("c")),
                "volume": row.get("volume", row.get("v")),
                "t": row.get("t"),
                "open_time_ms": row.get("t"),
            }
        )
    if len(normalized) < INTRADAY_5M_MIN_BARS:
        return (), str(payload.get("source") or "")
    return tuple(normalized), str(payload.get("source") or "intraday")


async def _fetch_l2_microstructure(symbol: str) -> tuple[dict[str, Any], bool, str]:
    """L2 BingX: cache del feed en vivo o fetch REST directo."""
    from backend.layer_1_data.datos.equity_l2_watchlist_hub import (
        fetch_equity_l2_microstructure,
        is_watchlist_symbol,
    )
    from backend.services.equity_l2_feed_service import (
        equity_l2_feed_enabled,
        get_equity_l2_feed,
    )

    if not is_watchlist_symbol(symbol):
        return {}, False, "not_watchlist"

    if equity_l2_feed_enabled():
        cached = get_equity_l2_feed().get_microstructure(symbol)
        if cached:
            return cached, bool(cached.get("ok")), "l2_feed_cache"

    try:
        from backend.layer_1_data.datos.bingx_client import BingXClient

        client = BingXClient()
        bundle = await fetch_equity_l2_microstructure(client, symbol)
        data = bundle.to_dict()
        return data, bundle.ok, "bingx_rest"
    except Exception as exc:
        logger.warning(
            "r1_enrichment.l2_fetch_failed symbol=%s error=%s",
            symbol,
            str(exc)[:120],
        )
        return {}, False, "l2_error"


async def build_r1_enrichment_async(
    symbol: str,
    *,
    options_ctx: Route1OptionsSnapshotContext | None,
) -> R1EnrichmentContext:
    """Hidrata L2 + 5m + 8 motores híbridos + meta predictivo."""
    sym = symbol.upper().strip()
    sources: dict[str, str] = {}

    intraday_bars, intraday_source = _fetch_intraday_5m(sym)
    if intraday_source:
        sources["intraday_5m"] = intraday_source

    klines = list(intraday_bars)
    hybrid_signals = AlpacaR1OptionsReplay.run(klines, options_ctx)
    hybrid_confluence = OptionsConfluenceScorer.score(hybrid_signals)
    if hybrid_signals:
        sources["hybrid_engines"] = f"replay_{len(hybrid_signals)}"

    l2_data, l2_ok, l2_source = await _fetch_l2_microstructure(sym)
    sources["l2"] = l2_source

    predictive_meta = await fetch_route1_predictive_meta(sym)
    if predictive_meta:
        sources["predictive_bridge"] = "bingx_predictive"

    return R1EnrichmentContext(
        hybrid_confluence=hybrid_confluence,
        hybrid_signal_count=len(hybrid_signals),
        l2_microstructure=l2_data,
        l2_ok=l2_ok,
        intraday_bars_5m=intraday_bars,
        predictive_meta=predictive_meta,
        sources=sources,
    )


def build_r1_enrichment(
    symbol: str,
    *,
    options_ctx: Route1OptionsSnapshotContext | None,
) -> R1EnrichmentContext:
    """Wrapper sync para CLI y signal loop."""
    return _run_async(build_r1_enrichment_async(symbol, options_ctx=options_ctx))


__all__ = ["build_r1_enrichment", "build_r1_enrichment_async"]
