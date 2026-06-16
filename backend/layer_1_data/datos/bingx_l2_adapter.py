from __future__ import annotations
from typing import Protocol, Literal, Any
"""BingX L2 (depth) → normalized adapter — Layer 1.

Converts raw BingX REST depth payloads (``/openApi/swap/v2/quote/depth`` for
synthetic-stock perpetuals; ``/openApi/spot/v1/market/depth`` for spot) into a
typed, JSON-safe adapter result with explicit ``ok``/``reason`` semantics and
minimal book metrics (spread, depth, imbalance).

This module deliberately does **not** import from Layer 2+ (per repo
architecture rule: data flows strictly top-to-bottom). The bridge to the
technical specialist's ``LOBSnapshot`` lives in ``backend/services``.

Funding-survival framing: when L2 cannot be sourced for a given instrument we
return ``ok=False`` with an explicit ``reason`` (``l2_unavailable``,
``empty_book``, ``invalid_payload``, ``fetch_error``). Callers must degrade
or block sizing — never fabricate book state.
"""


import time
from dataclasses import asdict, dataclass, field

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# Market types that BingX supports L2 depth snapshots for via the swap endpoint.
SUPPORTED_L2_MARKET_TYPES: frozenset[str] = frozenset(
    {
        "stock_perp",
        "stock_index_perp",
        "crypto_standard",
    }
)

# Source identifiers carried on every adapter result. Stable across versions —
# downstream consumers (scanner, risk gate) key off these strings.
L2_SOURCE_PERP_REST: str = "bingx_l2_snapshot_rest"
L2_SOURCE_UNAVAILABLE: str = "bingx_l2_unavailable"

# Reason codes — kept as explicit literals to avoid magic strings drifting.
L2Reason = Literal[
    "ok",
    "l2_unavailable",
    "empty_book",
    "invalid_payload",
    "fetch_error",
    "missing_symbol",
]


@dataclass(frozen=True)
class BingXL2Level:
    """One market-by-price level extracted from a BingX depth row."""

    price: float
    quantity: float


@dataclass(frozen=True)
class BingXL2Metrics:
    """Top-of-book metrics computed during adaptation.

    All values default to ``0.0`` so the result is JSON-safe even when the
    payload is unavailable. Consumers MUST also check ``ok`` before trusting
    these numbers.
    """

    spread: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    imbalance: float = 0.0


@dataclass(frozen=True)
class BingXL2AdapterResult:
    """Normalized BingX L2 depth snapshot with explicit availability semantics.

    Required fields per task spec: ``symbol``, ``source``, ``ok``, ``reason``,
    plus the parsed ``bids`` / ``asks`` and computed metrics. ``timestamp_ms``
    is the millisecond UTC timestamp at which the adapter ran (BingX REST does
    not return a server timestamp on the depth payload).
    """

    symbol: str
    source: str
    ok: bool
    reason: L2Reason
    timestamp_ms: int
    market_type: str | None = None
    bids: tuple[BingXL2Level, ...] = ()
    asks: tuple[BingXL2Level, ...] = ()
    metrics: BingXL2Metrics = field(default_factory=BingXL2Metrics)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for routers/UI."""
        return {
            "symbol": self.symbol,
            "source": self.source,
            "ok": self.ok,
            "reason": self.reason,
            "timestamp_ms": self.timestamp_ms,
            "market_type": self.market_type,
            "bids": [asdict(level) for level in self.bids],
            "asks": [asdict(level) for level in self.asks],
            "metrics": asdict(self.metrics),
        }


class _OrderBookFetcher(Protocol):
    """Minimal duck-typed protocol — matches ``BingXClient.fetch_order_book_perp``.

    Defined here (rather than importing BingXClient) to keep this module free of
    heavy network deps in import time and to make tests trivial to mock.
    """

    async def fetch_order_book_perp(self, symbol: str, *, limit: int = ...) -> dict[str, Any]: ...


def _parse_levels(raw_levels: object) -> tuple[BingXL2Level, ...]:
    """Parse BingX depth rows ``[["price", "qty"], ...]`` defensively.

    Skips malformed rows, non-positive prices, and negative quantities.
    Returns a tuple of valid ``BingXL2Level`` instances in input order.
    """
    if not isinstance(raw_levels, list):
        return ()
    parsed: list[BingXL2Level] = []
    for row in raw_levels:
        if isinstance(row, list | tuple) and len(row) >= 2:
            price_raw, qty_raw = row[0], row[1]
        elif isinstance(row, dict):
            price_raw = row.get("price") or row.get("p")
            qty_raw = row.get("quantity") or row.get("qty") or row.get("q")
        else:
            continue
        try:
            price = float(price_raw)

            qty = float(qty_raw)

        except (TypeError, ValueError):
            continue
        if price <= 0.0 or qty < 0.0:
            continue
        parsed.append(BingXL2Level(price=price, quantity=qty))
    return tuple(parsed)


def _compute_metrics(
    bids: tuple[BingXL2Level, ...],
    asks: tuple[BingXL2Level, ...],
) -> BingXL2Metrics:
    """Compute spread / depths / imbalance from parsed levels.

    - spread = best_ask.price - best_bid.price (clamped to ``>= 0``)
    - bid_depth / ask_depth = sum of quantities across all parsed levels
    - imbalance = bid_depth / (bid_depth + ask_depth); ``0.0`` if total is zero
    """
    if not bids or not asks:
        return BingXL2Metrics()
    best_bid = max(level.price for level in bids)
    best_ask = min(level.price for level in asks)
    spread = max(0.0, best_ask - best_bid)
    bid_depth = float(sum(level.quantity for level in bids))
    ask_depth = float(sum(level.quantity for level in asks))
    total = bid_depth + ask_depth
    imbalance = (bid_depth / total) if total > 0.0 else 0.0
    return BingXL2Metrics(
        spread=spread,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        imbalance=imbalance,
    )


def _unavailable(
    symbol: str,
    reason: L2Reason,
    market_type: str | None,
    timestamp_ms: int | None = None,
) -> BingXL2AdapterResult:
    """Build an explicit ``ok=False`` result. Never fabricates book state."""
    return BingXL2AdapterResult(
        symbol=symbol,
        source=L2_SOURCE_UNAVAILABLE,
        ok=False,
        reason=reason,
        timestamp_ms=timestamp_ms if timestamp_ms is not None else _now_ms(),
        market_type=market_type,
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_l2_snapshot_from_bingx_depth(
    symbol: str,
    depth_payload: dict[str, Any] | None,
    *,
    market_type: str | None = None,
    timestamp_ms: int | None = None,
) -> BingXL2AdapterResult:
    """Pure adapter — convert a raw BingX depth dict into a normalized result.

    Parameters
    ----------
    symbol:
        Display symbol (e.g. ``"GOOGL-USDT"``). Used as the result's ``symbol``.
    depth_payload:
        Unwrapped BingX depth dict — i.e. the value returned by
        ``BingXClient.fetch_order_book_perp`` (already passed through
        ``_unwrap_data``). Expected shape: ``{"bids": [["px","qty"], ...],
        "asks": [["px","qty"], ...]}``.
    market_type:
        Optional instrument classification (``stock_perp``,
        ``stock_index_perp``, ``crypto_standard``, or anything else). When the
        type is provided and is NOT in :data:`SUPPORTED_L2_MARKET_TYPES`, the
        adapter short-circuits with ``ok=False, reason="l2_unavailable"``.
    timestamp_ms:
        Optional override for the result's ``timestamp_ms``. Defaults to
        ``time.time()`` at adapter execution time.
    """
    ts = timestamp_ms if timestamp_ms is not None else _now_ms()
    if not symbol or not str(symbol).strip():
        return _unavailable("", "missing_symbol", market_type, ts)
    sym = str(symbol).strip()

    if market_type is not None and market_type not in SUPPORTED_L2_MARKET_TYPES:
        logger.debug(
            "bingx_l2_adapter.l2_unavailable symbol=%s market_type=%s",
            sym,
            market_type,
        )
        return _unavailable(sym, "l2_unavailable", market_type, ts)

    if not isinstance(depth_payload, dict):
        return _unavailable(sym, "invalid_payload", market_type, ts)

    bids = _parse_levels(depth_payload.get("bids"))
    asks = _parse_levels(depth_payload.get("asks"))

    if not bids or not asks:
        return BingXL2AdapterResult(
            symbol=sym,
            source=L2_SOURCE_PERP_REST,
            ok=False,
            reason="empty_book",
            timestamp_ms=ts,
            market_type=market_type,
            bids=bids,
            asks=asks,
            metrics=BingXL2Metrics(),
        )

    metrics = _compute_metrics(bids, asks)
    return BingXL2AdapterResult(
        symbol=sym,
        source=L2_SOURCE_PERP_REST,
        ok=True,
        reason="ok",
        timestamp_ms=ts,
        market_type=market_type,
        bids=bids,
        asks=asks,
        metrics=metrics,
    )


async def fetch_bingx_l2_snapshot(
    client: _OrderBookFetcher,
    symbol: str,
    *,
    market_type: str | None = None,
    limit: int = 20,
) -> BingXL2AdapterResult:
    """Fetch a BingX perp depth snapshot and adapt it. Never raises on network
    errors — failures surface as ``ok=False, reason="fetch_error"``.

    When ``market_type`` is supplied and unsupported, the network call is
    skipped entirely and an ``l2_unavailable`` result is returned. This keeps
    upstream behavior deterministic for instruments BingX does not provide
    depth for.
    """
    sym = (symbol or "").strip()
    if not sym:
        return _unavailable("", "missing_symbol", market_type)

    if market_type is not None and market_type not in SUPPORTED_L2_MARKET_TYPES:
        return _unavailable(sym, "l2_unavailable", market_type)

    try:
        payload = await client.fetch_order_book_perp(sym, limit=limit)
    except Exception as exc:
        logger.warning("bingx_l2_adapter.fetch_error symbol=%s error=%s", sym, exc)
        return _unavailable(sym, "fetch_error", market_type)

    return build_l2_snapshot_from_bingx_depth(
        sym,
        payload,
        market_type=market_type,
    )


__all__ = [
    "L2_SOURCE_PERP_REST",
    "L2_SOURCE_UNAVAILABLE",
    "SUPPORTED_L2_MARKET_TYPES",
    "BingXL2AdapterResult",
    "BingXL2Level",
    "BingXL2Metrics",
    "L2Reason",
    "build_l2_snapshot_from_bingx_depth",
    "fetch_bingx_l2_snapshot",
]
