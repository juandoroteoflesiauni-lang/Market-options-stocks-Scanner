"""Bridge a dark-pool fetch into a BingX candidate analysis block. # [PD-2][PD-3][TH]

Decouples ``bingx_candidate_analysis`` from the Hub: the caller injects a
``DarkPoolSnapshotFn`` (typically a closure over
``MarketDataHub.fetch_dark_pool_prints`` that unwraps the ``Result``). This
module never calls external APIs directly (PD-3) and never raises — every
failure path degrades to an unavailable block with a stable reason code.

``BingXDarkPoolBlock`` is owned by ``bingx_candidate_analysis`` (alongside the
other engine blocks); it is imported lazily here to avoid an import cycle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from backend.config.logger_setup import get_logger
from backend.models.dark_pool_snapshot import DarkPoolSnapshot

if TYPE_CHECKING:
    from backend.services.bingx_candidate_analysis import BingXDarkPoolBlock

logger = get_logger(__name__)

# A function resolving an underlying symbol to a dark-pool snapshot (or None).
DarkPoolSnapshotFn = Callable[[str], Awaitable[DarkPoolSnapshot | None]]

REASON_NO_DARK_POOL_FN = "no_dark_pool_fn"
REASON_DARK_POOL_UNAVAILABLE = "dark_pool_unavailable"
REASON_DARK_POOL_FETCH_FAILED = "dark_pool_fetch_failed"


def _unavailable(reason: str) -> BingXDarkPoolBlock:
    from backend.services.bingx_candidate_analysis import BingXDarkPoolBlock

    return BingXDarkPoolBlock(
        status="unavailable",
        source="none",
        bias="NEUTRAL",
        confidence=0.0,
        net_notional_usd="0",
        print_count_1h=0,
        reason=reason,
    )


async def build_dark_pool_block(
    underlying_symbol: str,
    *,
    dark_pool_fn: DarkPoolSnapshotFn | None,
) -> BingXDarkPoolBlock:
    """Resolve the dark-pool block for *underlying_symbol* (never raises)."""
    from backend.services.bingx_candidate_analysis import BingXDarkPoolBlock

    if dark_pool_fn is None:
        return _unavailable(REASON_NO_DARK_POOL_FN)

    try:
        snapshot = await dark_pool_fn(underlying_symbol)
    except Exception as exc:
        logger.warning(
            "bingx_dark_pool_bridge.fetch_failed symbol=%s error=%s",
            underlying_symbol,
            str(exc)[:180],
        )
        return _unavailable(REASON_DARK_POOL_FETCH_FAILED)

    if snapshot is None:
        return _unavailable(REASON_DARK_POOL_UNAVAILABLE)

    logger.info(
        "bingx_dark_pool_bridge.attached symbol=%s bias=%s confidence=%.3f prints=%d",
        underlying_symbol,
        snapshot.bias,
        snapshot.confidence,
        snapshot.print_count_1h,
    )
    return BingXDarkPoolBlock(
        status="available",
        source=snapshot.source,
        bias=snapshot.bias,
        confidence=snapshot.confidence,
        net_notional_usd=str(snapshot.net_notional_usd),  # PD-2: Decimal → str for JSON
        print_count_1h=snapshot.print_count_1h,
        reason=None,
        snapshot=snapshot,
    )


__all__ = [
    "REASON_DARK_POOL_FETCH_FAILED",
    "REASON_DARK_POOL_UNAVAILABLE",
    "REASON_NO_DARK_POOL_FN",
    "DarkPoolSnapshotFn",
    "build_dark_pool_block",
]
