from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TypeVar

from backend.models.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)

T = TypeVar("T")

STANDARD_QUEUE_MAX_SIZE: int = 10_000
PRIORITY_QUEUE_MAX_SIZE: int = 1_000


class EventBus:
    """Decoupled pub/sub bus using asyncio.Queue primitives.

    Producers publish events without knowing who consumes them.
    Consumers subscribe without knowing who produces.

    Backpressure policy: Drop-Oldest when queue exceeds max size.
    This prevents memory overflow at the cost of losing stale events.
    """

    def __init__(self) -> None:
        self._standard_queue: asyncio.Queue[MarketSnapshot] = asyncio.Queue(
            maxsize=STANDARD_QUEUE_MAX_SIZE
        )
        self._priority_queue: asyncio.Queue[object] = asyncio.Queue(maxsize=PRIORITY_QUEUE_MAX_SIZE)

    async def publish(self, snapshot: MarketSnapshot) -> None:
        """Publishes a MarketSnapshot.

        High-priority snapshots (high_priority=True) go to the priority
        queue for fast-track processing by Phase B/C. Regular snapshots
        go to the standard queue.

        Applies Drop-Oldest backpressure on both queues.

        Args:
            snapshot: The validated, frozen MarketSnapshot to publish.
        """
        if snapshot.high_priority:
            await self._publish_priority_snapshot(snapshot)
        else:
            await self._publish_standard(snapshot)

    async def _publish_standard(self, snapshot: MarketSnapshot) -> None:
        """Publishes a MarketSnapshot to the standard queue."""
        if self._standard_queue.full():
            try:
                dropped = self._standard_queue.get_nowait()
                logger.warning(
                    "EventBus: BACKPRESSURE — dropping oldest snapshot [ARCH-3]",
                    extra={"dropped_ticker": dropped.ticker, "queue_size": STANDARD_QUEUE_MAX_SIZE},
                )
            except asyncio.QueueEmpty:
                pass
        await self._standard_queue.put(snapshot)

    async def _publish_priority_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Publishes a high-priority MarketSnapshot to the priority queue."""
        if self._priority_queue.full():
            try:
                dropped = self._priority_queue.get_nowait()
                dropped_ticker = getattr(dropped, "ticker", str(dropped))
                logger.warning(
                    "EventBus: PRIORITY BACKPRESSURE — dropping oldest high-priority snapshot",
                    extra={"dropped_ticker": dropped_ticker},
                )
            except asyncio.QueueEmpty:
                pass
        await self._priority_queue.put(snapshot)

    async def publish_priority(self, signal: object) -> None:
        """Publishes to the high-priority Phase D lane."""
        if self._priority_queue.full():
            try:
                dropped = self._priority_queue.get_nowait()
                logger.critical(
                    "EventBus: PRIORITY QUEUE FULL — dropping signal. "
                    "Investigate Phase D consumer latency immediately.",
                    extra={"dropped": str(dropped)},
                )
            except asyncio.QueueEmpty:
                pass
        await self._priority_queue.put(signal)

    async def consume(self) -> AsyncGenerator[MarketSnapshot, None]:
        """Async generator for standard queue consumers (Phase B/C)."""
        while True:
            snapshot = await self._standard_queue.get()
            try:
                yield snapshot
            finally:
                self._standard_queue.task_done()

    async def consume_priority(self) -> AsyncGenerator[object, None]:
        """Async generator for the Phase D priority consumer."""
        while True:
            signal = await self._priority_queue.get()
            try:
                yield signal
            finally:
                self._priority_queue.task_done()
