from __future__ import annotations
from typing import Any, Protocol
"""BingX L2 → ``LOBSnapshot`` bridge between Layer 1 and Layer 3 (tecnico).

The Layer 1 adapter (:mod:`backend.layer_1_data.datos.bingx_l2_adapter`) is
deliberately pure and does not import from Layer 2+. This module performs the
upward bridge: it takes a :class:`BingXL2AdapterResult` and produces either a
:class:`LOBSnapshot` consumed by
:func:`backend.quant_engine.engines.technical.lob_dynamics_engine.analyze_lob_dynamics`
or an explicit unavailable :class:`LOBDynamicsAnalysis` payload.

Funding-survival framing: when L2 is unavailable / empty / invalid we surface
``LOBDynamicsAnalysis(ok=False, error=<reason>, source=<adapter_source>)`` so
the risk gate downstream can degrade or block — we never fabricate a snapshot.
"""


from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_l2_adapter import (
    BingXL2AdapterResult,
    BingXL2Metrics,
    fetch_bingx_l2_snapshot,
)
from backend.quant_engine.engines.technical.lob_dynamics_engine import (
    LOBConfig,
    LOBDynamicsAnalysis,
    LOBLevel,
    LOBSnapshot,
    analyze_lob_dynamics,
)

logger = get_logger(__name__)

# Thresholds for ``_compute_data_quality_score``. Survival framing: a wide
# spread or a thin book degrades trust in the snapshot — the score collapses
# linearly to 0 long before the book becomes unusable so the funding gate has a
# chance to size down before the Risk Desk has to block.
DATA_QUALITY_SPREAD_PCT_BEST: float = 0.05  # spread ≤ 0.05% of mid → component 1.0
DATA_QUALITY_SPREAD_PCT_WORST: float = 1.0  # spread ≥ 1.0% of mid → component 0.0
DATA_QUALITY_DEFAULT_DEPTH_TARGET: float = 1000.0  # bid_depth + ask_depth target


class _OrderBookFetcherProto(Protocol):
    """Structural placeholder — duck-typed in the runtime signature below."""

    async def fetch_order_book_perp(self, symbol: str, *, limit: int = 20) -> dict[str, Any]:  # type: ignore[type-arg]
        ...


def _compute_data_quality_score(
    metrics: BingXL2Metrics,
    *,
    mid_price: float | None = None,
    depth_target: float = DATA_QUALITY_DEFAULT_DEPTH_TARGET,
) -> float:
    """Compute a 0.0-1.0 data-quality score from raw L2 metrics.

    Two components, weighted equally:

    * **Spread component** — uses the *relative* spread vs the mid-price (so
      ``$0.5`` on a $100 instrument is treated the same as ``$50`` on $10 000).
      A relative spread ``≤ 0.05 %`` scores ``1.0``; ``≥ 1.0 %`` scores
      ``0.0``; linear in between. When ``mid_price`` is unknown (e.g. only the
      raw ``BingXL2Metrics`` is available), the absolute spread itself is
      treated as a percent — a deliberately conservative fallback.
    * **Depth component** — total depth ``bid_depth + ask_depth`` normalized
      against ``depth_target`` (default 1 000 units). ``0`` depth → ``0.0``,
      ``>= depth_target`` → ``1.0``, linear in between.

    The score is intentionally simple and explainable — the Risk Desk consumes
    it as a *hint*, not as a hard gate.
    """
    spread_pct = _relative_spread_pct(metrics.spread, mid_price)
    spread_component = _linear_window(
        value=spread_pct,
        best=DATA_QUALITY_SPREAD_PCT_BEST,
        worst=DATA_QUALITY_SPREAD_PCT_WORST,
    )
    total_depth = float(metrics.bid_depth) + float(metrics.ask_depth)
    if depth_target <= 0.0:
        depth_component = 1.0 if total_depth > 0.0 else 0.0
    else:
        depth_component = max(0.0, min(1.0, total_depth / depth_target))
    score = (spread_component + depth_component) / 2.0
    return round(max(0.0, min(1.0, score)), 4)


def _relative_spread_pct(spread: float, mid_price: float | None) -> float:
    """Return spread as a percentage of mid (or absolute fallback)."""
    if mid_price is None or mid_price <= 0.0:
        # No mid available — treat absolute spread as percent (conservative).
        return max(0.0, float(spread))
    return max(0.0, float(spread) / float(mid_price) * 100.0)


def _linear_window(*, value: float, best: float, worst: float) -> float:
    """Map ``value`` into [0, 1] linearly: ``best``→1.0, ``worst``→0.0."""
    if worst <= best:
        return 1.0 if value <= best else 0.0
    if value <= best:
        return 1.0
    if value >= worst:
        return 0.0
    return 1.0 - (value - best) / (worst - best)


def _mid_price_from_adapter(result: BingXL2AdapterResult) -> float | None:
    """Best-bid / best-ask midpoint, or ``None`` if either side is empty."""
    if not result.bids or not result.asks:
        return None
    try:
        best_bid = max(level.price for level in result.bids)
        best_ask = min(level.price for level in result.asks)
    except ValueError:
        return None
    if best_bid <= 0.0 or best_ask <= 0.0:
        return None
    return (best_bid + best_ask) / 2.0


def adapter_result_to_lob_snapshot(
    result: BingXL2AdapterResult,
) -> LOBSnapshot | None:
    """Convert a successful adapter result into an ``LOBSnapshot``.

    Returns ``None`` if the adapter result is not ``ok`` — callers must then
    surface an explicit unavailable :class:`LOBDynamicsAnalysis`.
    """
    if not result.ok:
        return None
    bids = tuple(LOBLevel(price=level.price, quantity=level.quantity) for level in result.bids)
    asks = tuple(LOBLevel(price=level.price, quantity=level.quantity) for level in result.asks)
    return LOBSnapshot(
        timestamp=result.timestamp_ms,
        bids=bids,
        asks=asks,
    )


def _unavailable_analysis(
    result: BingXL2AdapterResult,
    config: LOBConfig | None,
) -> LOBDynamicsAnalysis:
    """Build an explicit unavailable analysis carrying the adapter reason."""
    return LOBDynamicsAnalysis(
        ok=False,
        error=f"l2_unavailable:{result.reason}",
        source=result.source,
        config=config or LOBConfig(),
    )


async def analyze_bingx_l2(
    client: _OrderBookFetcherProto,
    symbol: str,
    *,
    market_type: str | None = None,
    limit: int = 20,
    config: LOBConfig | None = None,
) -> LOBDynamicsAnalysis:
    """Fetch a BingX perp depth snapshot, adapt it, and run LOB analysis.

    Convenience wrapper for routers/services. Returns a typed
    :class:`LOBDynamicsAnalysis` — ``ok=True`` with a populated ``result`` when
    the snapshot is valid, otherwise ``ok=False`` with ``error`` carrying the
    adapter reason and ``source`` set to ``bingx_l2_unavailable``.
    """
    adapter_result = await fetch_bingx_l2_snapshot(
        client,
        symbol,
        market_type=market_type,
        limit=limit,
    )
    snapshot = adapter_result_to_lob_snapshot(adapter_result)
    if snapshot is None:
        logger.debug(
            "bingx_l2_integration.unavailable symbol=%s reason=%s market_type=%s",
            adapter_result.symbol,
            adapter_result.reason,
            adapter_result.market_type,
        )
        return _unavailable_analysis(adapter_result, config)
    analysis = analyze_lob_dynamics(snapshot=snapshot, config=config)
    mid_price = _mid_price_from_adapter(adapter_result)
    quality_score = _compute_data_quality_score(
        adapter_result.metrics,
        mid_price=mid_price,
    )

    # Calculate LOB HHI concentration
    bids = adapter_result.bids
    asks = adapter_result.asks
    bid_total = sum(b.quantity for b in bids)
    ask_total = sum(a.quantity for a in asks)
    bid_hhi = sum((b.quantity / bid_total) ** 2 for b in bids) if bid_total > 0.0 else 0.0
    ask_hhi = sum((a.quantity / ask_total) ** 2 for a in asks) if ask_total > 0.0 else 0.0
    lob_hhi = (bid_hhi + ask_hhi) / 2.0 if (bid_total > 0.0 or ask_total > 0.0) else 0.0

    # Tag the analysis with the canonical BingX REST source so downstream
    # consumers can distinguish bingx_l2_snapshot_rest from a hypothetical
    # streaming source, attach the data-quality score so the funding gate
    # can degrade sizing without re-deriving it, and bridge the raw book
    # metrics (spread/depth/mid) so the execution-quality gate in
    # ``BingXBotService`` can evaluate them without re-importing layer_1.
    return analysis.model_copy(
        update={
            "source": adapter_result.source,
            "data_quality_score": quality_score,
            "spread": float(adapter_result.metrics.spread),
            "bid_depth": float(adapter_result.metrics.bid_depth),
            "ask_depth": float(adapter_result.metrics.ask_depth),
            "mid_price": mid_price,
            "hhi_concentration": lob_hhi,
        }
    )


def order_book_dict_to_lob_analysis(
    order_book: dict[str, Any],
    *,
    symbol: str = "",
    market_type: str | None = "stock_perp",
    config: LOBConfig | None = None,
) -> LOBDynamicsAnalysis:
    """Build LOB analysis from a BingX ``fetch_order_book`` payload."""
    from backend.layer_1_data.datos.bingx_l2_adapter import build_l2_snapshot_from_bingx_depth

    adapter_result = build_l2_snapshot_from_bingx_depth(
        symbol or str(order_book.get("symbol") or ""),
        order_book,
        market_type=market_type,
    )
    snapshot = adapter_result_to_lob_snapshot(adapter_result)
    if snapshot is None:
        return _unavailable_analysis(adapter_result, config)
    return analyze_lob_dynamics(snapshot=snapshot, config=config)


__all__ = [
    "DATA_QUALITY_DEFAULT_DEPTH_TARGET",
    "DATA_QUALITY_SPREAD_PCT_BEST",
    "DATA_QUALITY_SPREAD_PCT_WORST",
    "_compute_data_quality_score",
    "adapter_result_to_lob_snapshot",
    "analyze_bingx_l2",
    "order_book_dict_to_lob_analysis",
]
