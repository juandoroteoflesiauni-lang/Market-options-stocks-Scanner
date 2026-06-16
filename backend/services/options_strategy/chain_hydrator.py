"""Hidrata cadena de opciones tradeable (multi-expiry) para el bot Alpaca. # [PD-3][TH]"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.options_strategy_loader import OptionsStrategyConfigBundle, get_options_strategy_config
from backend.domain.alpaca_options_models import Route1OptionsSnapshotContext
from backend.layer_1_data.datos.massive_options_fetcher import try_massive_option_chain
from backend.services.options_strategy._chain import (
    chain_rows,
    dte_from_expiry,
    leg_is_tradeable,
    parse_expiry_date,
)

logger = get_logger(__name__)

_DEFAULT_RISK_FREE = 0.04


def _hydration_min_legs() -> int:
    return int(os.getenv("OPTIONS_CHAIN_HYDRATION_MIN_LEGS", "4"))


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _leg_mark(row: dict[str, Any], prefix: str) -> float | None:
    """Resuelve mark/mid desde NBBO, last o cierre del día."""
    mark = row.get(f"{prefix}_mark") or row.get(f"{prefix}_mid")
    if mark is not None and _safe_float(mark) > 0:
        return _safe_float(mark)
    bid = _safe_float(row.get(f"{prefix}_bid"), 0.0)
    ask = _safe_float(row.get(f"{prefix}_ask"), 0.0)
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2.0, 4)
    last = _safe_float(row.get(f"{prefix}_last"), 0.0)
    if last > 0:
        return last
    close = _safe_float(row.get(f"{prefix}_day_close"), 0.0)
    return close if close > 0 else None


def enrich_chain_marks(chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Completa ``call_mark``/``put_mark`` cuando el snapshot GEX no los trae."""
    enriched: list[dict[str, Any]] = []
    for raw in chain:
        row = dict(raw)
        for prefix in ("call", "put"):
            mark = _leg_mark(row, prefix)
            if mark is not None:
                row[f"{prefix}_mark"] = mark
                row[f"{prefix}_mid"] = mark
        enriched.append(row)
    return enriched


def _tradeable_legs_in_window(
    chain: list[dict[str, Any]],
    *,
    as_of: datetime,
    dte_min: int,
    dte_max: int,
    min_daily_volume: int = 25,
) -> int:
    count = 0
    for row in chain:
        expiry = parse_expiry_date(str(row.get("expiration") or row.get("expiry") or ""))
        if expiry is None:
            continue
        dte = dte_from_expiry(expiry, as_of=as_of)
        if dte < dte_min or dte > dte_max:
            continue
        for prefix in ("call", "put"):
            if leg_is_tradeable(row, prefix=prefix, min_daily_volume=min_daily_volume):
                count += 1
    return count


def chain_needs_hydration(
    inp_chain: list[dict[str, Any]],
    *,
    as_of: datetime,
    dte_min: int,
    dte_max: int,
    min_legs: int | None = None,
    min_daily_volume: int = 25,
) -> bool:
    """True si la cadena del snapshot no cubre el ventana DTE operativa."""
    enriched = enrich_chain_marks(inp_chain)
    legs_floor = min_legs if min_legs is not None else _hydration_min_legs()
    return _tradeable_legs_in_window(
        enriched,
        as_of=as_of,
        dte_min=dte_min,
        dte_max=dte_max,
        min_daily_volume=min_daily_volume,
    ) < legs_floor


def _spot_from_raw(raw: dict[str, Any]) -> float:
    quote = raw.get("quote")
    if isinstance(quote, dict):
        spot = _safe_float(quote.get("c"), 0.0)
        if spot > 0:
            return spot
    data = raw.get("data")
    if isinstance(data, list):
        for block in data:
            if not isinstance(block, dict):
                continue
            underlying = block.get("underlying")
            if isinstance(underlying, dict):
                spot = _safe_float(underlying.get("close") or underlying.get("price"), 0.0)
                if spot > 0:
                    return spot
    return 100.0


def _fetch_multi_expiry_chain(
    symbol: str,
    *,
    as_of: datetime,
    dte_min: int,
    dte_max: int,
    risk_free: float = _DEFAULT_RISK_FREE,
) -> tuple[list[dict[str, Any]], str]:
    """Descarga cadena Massive y devuelve filas strike en ventana DTE."""
    from backend.api.routes.options_router import _parse_finnhub_chain

    shaped, source, _meta = try_massive_option_chain(symbol.upper(), None)
    if not shaped or not shaped.get("data"):
        return [], source or ""

    spot = _spot_from_raw(shaped)
    data_list = shaped.get("data") or []
    if not isinstance(data_list, list):
        return [], source or ""

    merged: list[dict[str, Any]] = []
    for block in data_list:
        if not isinstance(block, dict):
            continue
        exp_raw = str(block.get("expirationDate") or "")
        expiry = parse_expiry_date(exp_raw)
        if expiry is None:
            continue
        dte = dte_from_expiry(expiry, as_of=as_of)
        if dte < dte_min or dte > dte_max:
            continue
        rows, *_rest = _parse_finnhub_chain(shaped, spot, exp_raw[:10], risk_free)
        for row in rows:
            merged.append(enrich_chain_marks([row.model_dump()])[0])

    logger.info(
        "options_chain_hydrator.fetched symbol=%s legs=%d source=%s dte=%d-%d",
        symbol.upper(),
        len(merged),
        source or "unknown",
        dte_min,
        dte_max,
    )
    return merged, source or ""


def hydrate_options_context(
    symbol: str,
    ctx: Route1OptionsSnapshotContext | None,
    *,
    as_of: datetime | None = None,
    config: OptionsStrategyConfigBundle | None = None,
) -> Route1OptionsSnapshotContext | None:
    """Enriquece snapshot con cadena multi-expiry si el GEX store solo tiene 0DTE."""
    if ctx is None or not ctx.available:
        return ctx

    active = config or get_options_strategy_config()
    from datetime import UTC

    moment = as_of or datetime.now(tz=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    existing = enrich_chain_marks(chain_rows_from_context(ctx))
    dte_min = active.universe.dte_min
    dte_max = active.universe.dte_max

    if not chain_needs_hydration(
        existing,
        as_of=moment,
        dte_min=dte_min,
        dte_max=dte_max,
        min_daily_volume=active.universe.min_daily_volume,
    ):
        if existing != chain_rows_from_context(ctx):
            snapshot = dict(ctx.snapshot)
            snapshot["chain"] = existing
            return ctx.model_copy(update={"snapshot": snapshot})
        return ctx

    fetched, source = _fetch_multi_expiry_chain(
        symbol,
        as_of=moment,
        dte_min=dte_min,
        dte_max=dte_max,
    )
    if not fetched:
        logger.warning(
            "options_chain_hydrator.empty symbol=%s dte=%d-%d",
            symbol.upper(),
            dte_min,
            dte_max,
        )
        return ctx

    snapshot = dict(ctx.snapshot)
    snapshot["chain"] = fetched
    snapshot["chain_source"] = source or snapshot.get("chain_source")
    return ctx.model_copy(update={"snapshot": snapshot})


def chain_rows_from_context(ctx: Route1OptionsSnapshotContext) -> list[dict[str, Any]]:
    chain = ctx.snapshot.get("chain")
    if not isinstance(chain, list):
        return []
    return [row for row in chain if isinstance(row, dict)]


__all__ = [
    "chain_needs_hydration",
    "enrich_chain_marks",
    "hydrate_options_context",
]
