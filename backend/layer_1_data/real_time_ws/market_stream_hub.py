"""Shared live market stream hub.

One upstream producer is kept per stream key and its payloads are fan-out to all
subscribers. This prevents each browser tab from opening its own provider WS.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

StreamPublisher = Callable[[object], Awaitable[None]]
StreamProducer = Callable[[StreamPublisher, asyncio.Event], Awaitable[None]]


@dataclass
class _HubStream:
    key: str
    producer: StreamProducer
    subscribers: set[asyncio.Queue[object]] = field(default_factory=set)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    dropped_messages: int = 0

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name=f"market-stream:{self.key}")

    async def publish(self, payload: object) -> None:
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                self.dropped_messages += 1
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(payload)

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is None or self.task.done():
            return
        try:
            await asyncio.wait_for(self.task, timeout=1.0)
        except TimeoutError:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def _run(self) -> None:
        try:
            await self.producer(self.publish, self.stop_event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("market_stream_hub: producer failed key=%s err=%s", self.key, exc)
        finally:
            self.stop_event.set()


class MarketStreamSubscription:
    """One client-side subscription queue for a shared market stream."""

    def __init__(
        self,
        hub: MarketStreamHub,
        key: str,
        queue: asyncio.Queue[object],
    ) -> None:
        self._hub = hub
        self.key = key
        self._queue = queue
        self._closed = False

    async def get(self, timeout: float | None = None) -> object:
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._hub._unsubscribe(self.key, self._queue)

    async def __aenter__(self) -> MarketStreamSubscription:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        await self.aclose()


class MarketStreamHub:
    """Async fan-out hub keyed by provider/feed/symbol/timeframe."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._streams: dict[str, _HubStream] = {}

    async def subscribe(
        self,
        key: str,
        producer: StreamProducer,
        *,
        max_queue: int = 2048,
    ) -> MarketStreamSubscription:
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=max(1, int(max_queue)))
        async with self._lock:
            stream = self._streams.get(key)
            if stream is None:
                stream = _HubStream(key=key, producer=producer)
                self._streams[key] = stream
                stream.start()
            stream.subscribers.add(queue)
        return MarketStreamSubscription(self, key, queue)

    def snapshot(self) -> dict[str, int]:
        streams = list(self._streams.values())
        return {
            "active_streams": len(streams),
            "subscribers": sum(len(stream.subscribers) for stream in streams),
            "dropped_messages": sum(stream.dropped_messages for stream in streams),
        }

    async def _unsubscribe(self, key: str, queue: asyncio.Queue[object]) -> None:
        stream_to_stop: _HubStream | None = None
        async with self._lock:
            stream = self._streams.get(key)
            if stream is None:
                return
            stream.subscribers.discard(queue)
            if not stream.subscribers:
                stream_to_stop = self._streams.pop(key, None)
        if stream_to_stop is not None:
            await stream_to_stop.stop()


_HUB = MarketStreamHub()


def get_market_stream_hub() -> MarketStreamHub:
    return _HUB
