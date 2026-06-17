"""Resolver de snapshots GEX: SQLite R1 primero, live Massive solo si stale. # [PD-3][TH]"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.shared_options_tier_policy import options_query_symbol_for_root
from backend.services.alpaca_r1_options_context import load_route1_options_context

logger = get_logger(__name__)

_SHARED_OPTIONS_SNAPSHOT_MAX_AGE_S = int(os.getenv("SHARED_OPTIONS_SNAPSHOT_MAX_AGE_S", "300"))


def _parse_as_of(as_of: str) -> datetime | None:
    try:
        return datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_snapshot_fresh(as_of: str, *, max_age_s: int | None = None) -> bool:
    parsed = _parse_as_of(as_of)
    if parsed is None:
        return False
    age_s = (datetime.now(tz=UTC) - parsed.astimezone(UTC)).total_seconds()
    limit = max_age_s if max_age_s is not None else _SHARED_OPTIONS_SNAPSHOT_MAX_AGE_S
    return age_s <= float(limit)


def _snapshot_object_from_context(snapshot: dict[str, Any]) -> Any:
    """Adapta JSON persistido al shape esperado por ``bingx_options_bridge``."""
    payload = dict(snapshot)
    payload.setdefault("ok", True)
    return SimpleNamespace(**payload)


def snapshot_from_route1_sqlite(symbol: str) -> Any | None:
    """Lee snapshot GEX de SQLite si existe y está fresco."""
    query_sym = options_query_symbol_for_root(symbol.upper().strip())
    ctx = load_route1_options_context(query_sym)
    if ctx is None or not ctx.available:
        return None
    if not _is_snapshot_fresh(ctx.as_of):
        logger.debug(
            "shared_options_snapshot.stale symbol=%s as_of=%s",
            query_sym,
            ctx.as_of,
        )
        return None
    logger.debug(
        "shared_options_snapshot.sqlite_hit symbol=%s as_of=%s",
        query_sym,
        ctx.as_of,
    )
    return _snapshot_object_from_context(ctx.snapshot)


async def shared_options_snapshot_service(
    symbol: str,
    expiry: str | None,
    r: float,
) -> Any:
    """Fetcher tier-aware: DB R1 compartida → ``options_snapshot_service`` live."""
    sym = symbol.upper().strip()
    cached = snapshot_from_route1_sqlite(sym)
    if cached is not None:
        return cached

    from backend.api.routes.options_router import options_snapshot_service

    logger.debug("shared_options_snapshot.live_fetch symbol=%s", sym)
    return await options_snapshot_service(sym, expiry, r)


__all__ = [
    "shared_options_snapshot_service",
    "snapshot_from_route1_sqlite",
]
