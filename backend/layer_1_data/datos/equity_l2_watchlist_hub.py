"""Hub Layer 1: BingX depth + trades para watchlist equities (sin bot). # [PD-3][IM][TH]"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from backend.config.equity_l2_watchlist import (
    EQUITY_L2_DEPTH_LIMIT,
    EQUITY_L2_FETCH_CONCURRENCY,
    EQUITY_L2_TRADE_LIMIT,
    EQUITY_L2_WATCHLIST,
)
from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_trade_adapter import (
    BingXMicrostructureBundle,
    build_microstructure_bundle,
)
from backend.services.scanner_symbol_routing import bingx_venue_symbol, normalize_scanner_symbol

logger = get_logger(__name__)


class _BingXMicrostructureClient(Protocol):
    async def fetch_recent_trades_perp(
        self, symbol: str, *, limit: int = ...
    ) -> list[dict[str, Any]]: ...

    async def fetch_order_book_perp(
        self, symbol: str, *, limit: int = ...
    ) -> dict[str, Any]: ...


def is_watchlist_symbol(symbol: str) -> bool:
    """True si el root está en la watchlist L2."""
    root = normalize_scanner_symbol(symbol)
    return root in EQUITY_L2_WATCHLIST


async def fetch_equity_l2_microstructure(
    client: _BingXMicrostructureClient,
    root: str,
    *,
    depth_limit: int = EQUITY_L2_DEPTH_LIMIT,
    trade_limit: int = EQUITY_L2_TRADE_LIMIT,
) -> BingXMicrostructureBundle:
    """Descarga tape + depth BingX para un root de equity watchlist."""
    normalized = normalize_scanner_symbol(root)
    venue = bingx_venue_symbol(normalized)
    if not venue:
        return BingXMicrostructureBundle(
            symbol=normalized,
            venue_symbol="",
            ok=False,
            reason="missing_venue_symbol",
        )

    try:
        raw_trades, depth = await asyncio.gather(
            client.fetch_recent_trades_perp(venue, limit=trade_limit),
            client.fetch_order_book_perp(venue, limit=depth_limit),
        )
    except Exception as exc:
        logger.warning(
            "equity_l2_hub.fetch_failed root=%s venue=%s error=%s",
            normalized,
            venue,
            str(exc)[:160],
        )
        return BingXMicrostructureBundle(
            symbol=normalized,
            venue_symbol=venue,
            ok=False,
            reason="fetch_error",
        )

    return build_microstructure_bundle(
        symbol=normalized,
        venue_symbol=venue,
        raw_trades=raw_trades,
        depth_payload=depth,
        market_type="stock_perp",
    )


async def fetch_watchlist_microstructure(
    client: _BingXMicrostructureClient,
    *,
    symbols: tuple[str, ...] | None = None,
    concurrency: int = EQUITY_L2_FETCH_CONCURRENCY,
) -> dict[str, BingXMicrostructureBundle]:
    """Batch fetch para toda la watchlist (o subconjunto)."""
    targets = symbols or EQUITY_L2_WATCHLIST
    sem = asyncio.Semaphore(max(1, concurrency))
    out: dict[str, BingXMicrostructureBundle] = {}

    async def _one(root: str) -> None:
        async with sem:
            bundle = await fetch_equity_l2_microstructure(client, root)
        out[normalize_scanner_symbol(root)] = bundle

    await asyncio.gather(*[_one(sym) for sym in targets])
    return out


__all__ = [
    "fetch_equity_l2_microstructure",
    "fetch_watchlist_microstructure",
    "is_watchlist_symbol",
]
