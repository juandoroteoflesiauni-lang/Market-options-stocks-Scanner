"""Construye ``OptionsStrategyInput`` hidratado para operación en vivo. # [PD-3][TH]"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import pandas as pd

from backend.config.logger_setup import get_logger
from backend.domain.alpaca_options_models import Route1OptionsSnapshotContext
from backend.layer_1_data.datos.massive_equity_bars_fetcher import fetch_equity_daily_bars
from backend.models.market_snapshot import DataLineage, MarketSnapshot, OHLCVBar
from backend.models.options_strategy import OptionsStrategyInput
from backend.config.alpaca_options_route_config import get_options_config_for_route
from backend.services.alpaca_r1_options_context import load_route1_options_context
from backend.services.options_strategy.chain_hydrator import hydrate_options_context
from backend.services.options_strategy.r1_enrichment_builder import build_r1_enrichment

logger = get_logger(__name__)

_MIN_BARS = 30
_INPUT_CACHE: dict[str, OptionsStrategyInput] = {}


def clear_strategy_input_cache() -> None:
    """Limpia deduplicación por ciclo de opciones (mismo símbolo/ruta/bucket)."""
    _INPUT_CACHE.clear()


def _df_to_ohlcv(df: pd.DataFrame) -> tuple[OHLCVBar, ...]:
    bars: list[OHLCVBar] = []
    for _, row in df.iterrows():
        ts = row.get("t")
        if ts is not None:
            try:
                time_str = datetime.fromtimestamp(float(ts) / 1000.0, tz=UTC).isoformat()
            except (TypeError, ValueError, OSError):
                time_str = datetime.now(tz=UTC).isoformat()
        else:
            time_str = datetime.now(tz=UTC).isoformat()
        bars.append(
            OHLCVBar(
                time=time_str,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(max(float(row.get("volume", 0) or 0), 0))),
            )
        )
    return tuple(bars)


def _build_market_snapshot(
    symbol: str,
    as_of: datetime,
    options_ctx: Route1OptionsSnapshotContext | None,
) -> MarketSnapshot | None:
    _, df, meta = fetch_equity_daily_bars(symbol)
    if df is None or len(df) < _MIN_BARS:
        logger.warning(
            "options_strategy_input.bars_missing symbol=%s error=%s",
            symbol,
            meta.get("error"),
        )
        return None

    ohlcv = _df_to_ohlcv(df.tail(max(_MIN_BARS, len(df))))
    price = ohlcv[-1].close
    if options_ctx is not None:
        spot = (options_ctx.snapshot or {}).get("spot")
        if spot is not None:
            try:
                price = Decimal(str(float(spot)))
            except (TypeError, ValueError):
                pass

    return MarketSnapshot(
        ticker=symbol.upper(),
        exchange="US",
        price=price,
        volume=int(float(ohlcv[-1].volume)),
        exchange_timestamp=as_of,
        data_lineage=DataLineage(
            source=str(meta.get("source") or "massive_equity_bars"),
            ingestion_latency_ms=0,
            raw_field_count=len(ohlcv),
        ),
        ohlcv=ohlcv,
    )


def build_strategy_input(
    symbol: str,
    *,
    as_of: datetime | None = None,
    include_r1_enrichment: bool = True,
    route: Literal["priority", "scan"] = "priority",
) -> OptionsStrategyInput:
    """Hidrata contexto: R1 completo o R2 técnico con chain si está disponible."""
    moment = as_of or datetime.now(tz=UTC)
    sym = symbol.upper().strip()
    from backend.hub.market_data_ttl_cache import five_minute_bucket_key

    cache_key = f"{sym}:{route}:{five_minute_bucket_key(sym, suffix='strategy_input')}"
    cached_input = _INPUT_CACHE.get(cache_key)
    if cached_input is not None:
        return cached_input

    options_ctx = load_route1_options_context(sym)
    route_config = get_options_config_for_route(route)
    options_ctx = hydrate_options_context(
        sym,
        options_ctx,
        as_of=moment,
        config=route_config,
    )
    market_snapshot = _build_market_snapshot(sym, moment, options_ctx)
    enrichment = (
        build_r1_enrichment(sym, options_ctx=options_ctx)
        if include_r1_enrichment and route == "priority"
        else None
    )

    if options_ctx is None:
        logger.warning("options_strategy_input.no_options_context symbol=%s", sym)
    elif not options_ctx.available:
        logger.warning("options_strategy_input.options_context_unavailable symbol=%s", sym)

    result = OptionsStrategyInput(
        symbol=sym,
        as_of=moment,
        market_snapshot=market_snapshot,
        options_context=options_ctx,
        r1_enrichment=enrichment,
        route=route,
    )
    _INPUT_CACHE[cache_key] = result
    return result


__all__ = ["build_strategy_input", "clear_strategy_input_cache"]
