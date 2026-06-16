from __future__ import annotations
from typing import Any
"""Limit-order-book dynamics engine for the technical specialist.

This module intentionally requires real L2 snapshots/events. It does not infer
book imbalance or spoofing from OHLCV bars.
"""


from collections import deque
from enum import IntEnum
from math import inf, isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


class LOBSide(IntEnum):
    """Book side for L2 events and levels."""

    BID = 0
    ASK = 1


class LOBEventType(IntEnum):
    """Supported L2 event types."""

    ADD = 0
    CANCEL = 1
    TRADE = 2


class SpoofingState(IntEnum):
    """High-level LOB manipulation classification."""

    NORMAL = 0
    BID_SPOOFING = 1
    ASK_SPOOFING = 2


class LOBConfig(BaseModel):
    """Runtime knobs for LOB dynamics."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    depth_levels: int = Field(default=5, ge=1, le=100)
    ctr_window_ms: int = Field(default=30_000, ge=1_000, le=600_000)
    ctr_spoofing_multiplier: float = Field(default=4.0, gt=1.0)
    rho_spoofing_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    max_levels: int = Field(default=20, ge=1, le=250)


class LOBLevel(BaseModel):
    """One market-by-price level."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    price: float
    quantity: float = Field(ge=0.0)
    order_count: int | None = Field(default=None, ge=0)

    @field_validator("price", "quantity")
    @classmethod
    def _finite_float(cls: Any, value: float) -> float:
        if not isfinite(float(value)):
            raise ValueError("LOB numeric fields must be finite")
        return float(value)


class LOBSnapshot(BaseModel):
    """Full market-by-price snapshot."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: int
    bids: tuple[LOBLevel, ...] = ()
    asks: tuple[LOBLevel, ...] = ()


class LOBEvent(BaseModel):
    """Granular L2 delta event."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: int
    type: LOBEventType
    side: LOBSide
    price: float
    quantity: float = Field(ge=0.0)

    @field_validator("price", "quantity")
    @classmethod
    def _finite_float(cls: Any, value: float) -> float:
        if not isfinite(float(value)):
            raise ValueError("LOB numeric fields must be finite")
        return float(value)


class LOBDynamicsResult(BaseModel):
    """One computed LOB metrics frame."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: int
    imbalance_rho: float
    ctr_bid: float
    ctr_ask: float
    spoofing_state: SpoofingState


class LOBDynamicsAnalysis(BaseModel):
    """JSON-safe LOB engine response for routers/UI.

    ``data_quality_score`` is an optional 0.0-1.0 indicator wired by the
    upstream bridge (e.g. :mod:`backend.services.bingx_l2_integration`) from the
    raw L2 metrics — it is intentionally not computed here because doing so
    would force layer_3 to import layer_1 contracts. Consumers (Risk Desk,
    funding gate) treat ``None`` as ``insufficient_data`` for L2 quality.

    ``spread``, ``bid_depth``, ``ask_depth`` and ``mid_price`` are optional
    book-quality metrics bridged from the L1 adapter by
    :mod:`backend.services.bingx_l2_integration`. They are *not* computed by
    layer_3 (which would force a layer_1 import) — the bridge service is the
    only writer. The bot's :class:`ExecutionQualityPolicy` reads them to gate
    order entry on synthetic stock perps; ``None`` means ``insufficient_data``
    for that metric and the execution gate degrades accordingly.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = True
    ok: bool = True
    error: str | None = None
    source: str = "l2_order_book"
    result: LOBDynamicsResult | None = None
    config: LOBConfig = Field(default_factory=LOBConfig)
    data_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    spread: float | None = Field(default=None, ge=0.0)
    bid_depth: float | None = Field(default=None, ge=0.0)
    ask_depth: float | None = Field(default=None, ge=0.0)
    mid_price: float | None = Field(default=None, gt=0.0)
    hhi_concentration: float | None = Field(default=None, ge=0.0, le=1.0)


class _RollingAccumulator:
    """Time-windowed sum with lazy expiry."""

    def __init__(self, window_ms: int) -> None:
        self.window_ms = window_ms
        self._items: deque[tuple[int, float]] = deque()
        self._sum = 0.0

    def add(self, value: float, timestamp: int) -> None:
        self._expire(timestamp)
        self._items.append((timestamp, value))
        self._sum += value

    def sum(self, timestamp: int | None = None) -> float:
        if timestamp is not None:
            self._expire(timestamp)
        return self._sum

    def reset(self) -> None:
        self._items.clear()
        self._sum = 0.0

    def _expire(self, timestamp: int) -> None:
        cutoff = timestamp - self.window_ms
        while self._items and self._items[0][0] < cutoff:
            _ts, value = self._items.popleft()
            self._sum -= value


class _BookSide:
    """Small sorted price ladder with deterministic trimming."""

    def __init__(self, side: LOBSide, max_levels: int) -> None:
        self.side = side
        self.max_levels = max_levels
        self.levels: dict[float, float] = {}

    def load_snapshot(self, levels: tuple[LOBLevel, ...]) -> None:
        self.levels.clear()
        for level in levels:
            self.upsert(level.price, level.quantity)

    def upsert(self, price: float, quantity: float) -> None:
        if quantity <= 0:
            self.levels.pop(price, None)
            return
        self.levels[price] = quantity
        self._trim()

    def remove_or_reduce(self, price: float, quantity: float) -> None:
        current = self.levels.get(price, 0.0)
        next_quantity = current - quantity
        if next_quantity <= 0:
            self.levels.pop(price, None)
        else:
            self.levels[price] = next_quantity

    def quantity_at(self, price: float) -> float:
        return self.levels.get(price, 0.0)

    def sum_qty(self, depth: int) -> float:
        return sum(quantity for _price, quantity in self._sorted_levels()[:depth])

    def _trim(self) -> None:
        if len(self.levels) <= self.max_levels:
            return
        keep = dict(self._sorted_levels()[: self.max_levels])
        self.levels = keep

    def _sorted_levels(self) -> list[tuple[float, float]]:
        reverse = self.side is LOBSide.BID
        return sorted(self.levels.items(), key=lambda item: item[0], reverse=reverse)


class LOBDynamicsEngine:
    """Computes depth imbalance, cancel-to-trade ratios and spoofing state."""

    def __init__(self, config: LOBConfig | None = None) -> None:
        self.config = config or LOBConfig()
        self._bids = _BookSide(LOBSide.BID, self.config.max_levels)
        self._asks = _BookSide(LOBSide.ASK, self.config.max_levels)
        self._cancelled_bid = _RollingAccumulator(self.config.ctr_window_ms)
        self._traded_bid = _RollingAccumulator(self.config.ctr_window_ms)
        self._cancelled_ask = _RollingAccumulator(self.config.ctr_window_ms)
        self._traded_ask = _RollingAccumulator(self.config.ctr_window_ms)

    def process_snapshot(self, snapshot: LOBSnapshot) -> LOBDynamicsResult:
        """Load a full book snapshot and return a metrics frame."""
        self._bids.load_snapshot(snapshot.bids)
        self._asks.load_snapshot(snapshot.asks)
        return self._compute(snapshot.timestamp)

    def process_event(self, event: LOBEvent) -> LOBDynamicsResult:
        """Apply one L2 event and return a metrics frame."""
        book = self._bids if event.side is LOBSide.BID else self._asks
        if event.type is LOBEventType.ADD:
            book.upsert(event.price, book.quantity_at(event.price) + event.quantity)
        elif event.type is LOBEventType.CANCEL:
            book.remove_or_reduce(event.price, event.quantity)
            target = self._cancelled_bid if event.side is LOBSide.BID else self._cancelled_ask
            target.add(event.quantity, event.timestamp)
        elif event.type is LOBEventType.TRADE:
            book.remove_or_reduce(event.price, event.quantity)
            target = self._traded_bid if event.side is LOBSide.BID else self._traded_ask
            target.add(event.quantity, event.timestamp)
        return self._compute(event.timestamp)

    def reset(self) -> None:
        """Reset book and CTR state."""
        self._bids.levels.clear()
        self._asks.levels.clear()
        self._cancelled_bid.reset()
        self._traded_bid.reset()
        self._cancelled_ask.reset()
        self._traded_ask.reset()

    def _compute(self, timestamp: int) -> LOBDynamicsResult:
        bid_sum = self._bids.sum_qty(self.config.depth_levels)
        ask_sum = self._asks.sum_qty(self.config.depth_levels)
        total = bid_sum + ask_sum
        rho = 0.0 if total <= 0 else (bid_sum - ask_sum) / total

        traded_bid = self._traded_bid.sum(timestamp)
        traded_ask = self._traded_ask.sum(timestamp)
        ctr_bid = self._cancelled_bid.sum(timestamp) / traded_bid if traded_bid > 0 else inf
        ctr_ask = self._cancelled_ask.sum(timestamp) / traded_ask if traded_ask > 0 else inf
        spoofing = self._classify_spoofing(rho, ctr_bid, ctr_ask)
        return LOBDynamicsResult(
            timestamp=timestamp,
            imbalance_rho=rho,
            ctr_bid=ctr_bid,
            ctr_ask=ctr_ask,
            spoofing_state=spoofing,
        )

    def _classify_spoofing(
        self,
        rho: float,
        ctr_bid: float,
        ctr_ask: float,
    ) -> SpoofingState:
        if abs(rho) < self.config.rho_spoofing_threshold:
            return SpoofingState.NORMAL
        bid_base = ctr_bid if isfinite(ctr_bid) and ctr_bid > 0 else 1.0
        ask_base = ctr_ask if isfinite(ctr_ask) and ctr_ask > 0 else 1.0
        if rho > 0 and ctr_bid / ask_base >= self.config.ctr_spoofing_multiplier:
            return SpoofingState.BID_SPOOFING
        if rho < 0 and ctr_ask / bid_base >= self.config.ctr_spoofing_multiplier:
            return SpoofingState.ASK_SPOOFING
        return SpoofingState.NORMAL


def analyze_lob_dynamics(
    snapshot: LOBSnapshot | None = None,
    events: tuple[LOBEvent, ...] = (),
    config: LOBConfig | None = None,
) -> LOBDynamicsAnalysis:
    """Run LOB dynamics over a snapshot plus optional event stream."""
    if snapshot is None and not events:
        return LOBDynamicsAnalysis(
            ok=False,
            error="L2 order-book snapshot or events are required.",
            config=config or LOBConfig(),
        )
    engine = LOBDynamicsEngine(config)
    result: LOBDynamicsResult | None = None
    if snapshot is not None:
        result = engine.process_snapshot(snapshot)
    for event in sorted(events, key=lambda item: item.timestamp):
        result = engine.process_event(event)
    return LOBDynamicsAnalysis(result=result, config=engine.config)


def unavailable_lob_dynamics_payload(config: LOBConfig | None = None) -> dict[str, object]:
    """Return an explicit unavailable contract when no L2 feed is wired."""
    analysis = LOBDynamicsAnalysis(
        ok=False,
        error="L2 order-book feed not configured",
        source="l2_feed_required",
        config=config or LOBConfig(),
    )
    return analysis.model_dump(mode="json")
