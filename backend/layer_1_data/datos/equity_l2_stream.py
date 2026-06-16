"""Workers WS/REST para stream L2 de la watchlist equity. # [PD-3][TH]"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Literal

from backend.config.equity_l2_watchlist import (
    EQUITY_L2_DEPTH_LIMIT,
    EQUITY_L2_FAST_POLL_INTERVAL_S,
    EQUITY_L2_WS_DEPTH_LEVEL,
)
from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_ws_hub import BingXWebSocketHub, probe_ws_channel
from backend.services.scanner_symbol_routing import bingx_venue_symbol, normalize_scanner_symbol

if TYPE_CHECKING:
    from backend.services.equity_l2_feed_service import EquityL2FeedService

logger = get_logger(__name__)

StreamMode = Literal["ws_depth", "rest_diff"]


async def resolve_stream_mode(hub: BingXWebSocketHub, venue: str) -> StreamMode:
    """Probe BingX WS; stock perps currently fall back to REST diff."""
    suffix = f"depth{EQUITY_L2_WS_DEPTH_LEVEL}"
    if await probe_ws_channel(hub, venue, suffix):
        return "ws_depth"
    return "rest_diff"


async def run_symbol_stream(
    service: EquityL2FeedService,
    root: str,
    *,
    client: Any,
    hub: BingXWebSocketHub,
) -> None:
    """Loop per symbol: WS depth when supported, else fast REST diff."""
    normalized = normalize_scanner_symbol(root)
    venue = bingx_venue_symbol(normalized)
    if not venue:
        return
    mode = await resolve_stream_mode(hub, venue)
    service.set_stream_mode(normalized, mode)
    logger.info("equity_l2_stream.start root=%s venue=%s mode=%s", normalized, venue, mode)
    if mode == "ws_depth":
        await _ws_depth_loop(service, hub, normalized, venue)
    else:
        await _rest_diff_loop(service, client, normalized, venue)


async def _ws_depth_loop(
    service: EquityL2FeedService,
    hub: BingXWebSocketHub,
    root: str,
    venue: str,
) -> None:
    while True:
        try:
            async for book in hub.stream_depth(venue, depth_level=EQUITY_L2_WS_DEPTH_LEVEL):
                service.apply_depth_update(root, book, source="bingx_ws_depth")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "equity_l2_stream.ws_depth_retry root=%s error=%s",
                root,
                str(exc)[:160],
            )
            await asyncio.sleep(2.0)


async def _rest_diff_loop(
    service: EquityL2FeedService,
    client: Any,
    root: str,
    venue: str,
) -> None:
    interval = max(1, EQUITY_L2_FAST_POLL_INTERVAL_S)
    while True:
        try:
            depth = await client.fetch_order_book_perp(venue, limit=EQUITY_L2_DEPTH_LIMIT)
            depth["timestamp_ms"] = int(time.time() * 1000)
            depth["source"] = "bingx_perp_depth_fast"
            service.apply_depth_update(root, depth, source="bingx_rest_diff")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                "equity_l2_stream.rest_diff_failed root=%s error=%s",
                root,
                str(exc)[:120],
            )
        await asyncio.sleep(interval)


__all__ = ["StreamMode", "resolve_stream_mode", "run_symbol_stream"]
