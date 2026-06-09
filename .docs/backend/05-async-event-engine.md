# 📖 Rule Book: Async Event Engine
## `.docs/backend/05-async-event-engine.md` — v2.0

> **Agent Load Instruction:** Load this file when working on the Event Bus,
> asyncio concurrency, WebSocket handling, or task management.

---

## 1. MISSION: EVENT-DRIVEN CORE

The system must react, never poll. Engines (Phase B/C) never "wait" for data.
Data arrives at them through the Event Bus. They wake up, process, publish, sleep.

**Inspiration:** NautilusTrader's deterministic event-driven architecture —
same execution semantics, no blocking, no shared mutable state.

---

## 2. EVENT BUS ARCHITECTURE

```
                    MarketDataHub
                    (Phase A output)
                          │
                          │ publish(MarketSnapshot)
                          ▼
              ┌───────────────────────┐
              │     Standard Queue    │ ← asyncio.Queue(maxsize=10_000)
              │   (Phase B/C feed)    │
              └──────────┬────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         Phase B Worker        Phase C Worker
         (VPIN/OFI)            (Derivatives)
              │                     │
              └──────────┬──────────┘
                         │ publish(OptionContract)
                         ▼
              ┌───────────────────────┐
              │    Priority Queue     │ ← Phase D EXCLUSIVE channel
              │   (Phase D feed)      │
              └──────────┬────────────┘
                         ▼
                    Phase D Monitor
                    (WebSocket feed)
                          │
                          ▼
                  ExecutionSignal → Frontend
```

---

## 3. THE EVENT BUS IMPLEMENTATION

```python
# backend/bus/event_bus.py
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TypeVar

from backend.models.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)

T = TypeVar("T")

STANDARD_QUEUE_MAX_SIZE: int = 10_000
PRIORITY_QUEUE_MAX_SIZE: int = 1_000   # Smaller — must never back up


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
        self._priority_queue: asyncio.Queue = asyncio.Queue(
            maxsize=PRIORITY_QUEUE_MAX_SIZE
        )

    async def publish(self, snapshot: MarketSnapshot) -> None:
        """Publishes a MarketSnapshot to the standard queue.

        Applies Drop-Oldest backpressure if the queue is full.

        Args:
            snapshot: The validated, frozen MarketSnapshot to publish.
        """
        if self._standard_queue.full():
            dropped = self._standard_queue.get_nowait()
            logger.warning(
                "EventBus: BACKPRESSURE — dropping oldest snapshot [ARCH-3]",
                extra={"dropped_ticker": dropped.ticker, "queue_size": STANDARD_QUEUE_MAX_SIZE},
            )
        await self._standard_queue.put(snapshot)

    async def publish_priority(self, signal: object) -> None:
        """Publishes to the high-priority Phase D lane."""
        if self._priority_queue.full():
            dropped = self._priority_queue.get_nowait()
            logger.critical(
                "EventBus: PRIORITY QUEUE FULL — dropping signal. "
                "Investigate Phase D consumer latency immediately.",
                extra={"dropped": str(dropped)},
            )
        await self._priority_queue.put(signal)

    async def consume(self) -> AsyncGenerator[MarketSnapshot, None]:
        """Async generator for standard queue consumers (Phase B/C)."""
        while True:
            snapshot = await self._standard_queue.get()
            yield snapshot
            self._standard_queue.task_done()

    async def consume_priority(self) -> AsyncGenerator:
        """Async generator for the Phase D priority consumer."""
        while True:
            signal = await self._priority_queue.get()
            yield signal
            self._priority_queue.task_done()
```

---

## 4. WORKER PATTERN (Per Engine)

Each engine runs as an isolated `asyncio.Task`. If it crashes, the bus
continues. The crash is logged; other workers are not affected.

```python
# backend/phases/phase_b/microstructure_engine.py
import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor

from backend.bus.event_bus import EventBus
from backend.models.market_snapshot import MarketSnapshot, EnrichedSnapshot
from backend.phases.phase_b.matrix_processor import calculate_vpin_ofi_sync

logger = logging.getLogger(__name__)


class MicrostructureEngine:
    """Phase B: Consumes MarketSnapshots, computes VPIN/OFI, publishes top 20.

    Isolation contract: This class has ZERO network imports.
    All data arrives via the EventBus.
    CPU-bound work is offloaded to ProcessPoolExecutor.
    """

    def __init__(
        self,
        event_bus: EventBus,
        executor: ProcessPoolExecutor,
    ) -> None:
        self._bus = event_bus
        self._executor = executor

    async def run(self) -> None:
        """Main consumption loop. Runs until cancelled.

        Graceful error handling: a single processing failure logs an error
        and continues with the next snapshot. The bus never stops.
        """
        logger.info("MicrostructureEngine: Starting Phase B worker.")
        async for snapshot in self._bus.consume():
            try:
                await self._process_snapshot(snapshot)
            except Exception:
                logger.error(
                    "Phase B: Snapshot processing failed — continuing [PD-6]",
                    extra={"ticker": snapshot.ticker},
                    exc_info=True,
                )

    async def _process_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Offloads CPU-bound VPIN/OFI calculation to process pool.

        Args:
            snapshot: The validated, frozen MarketSnapshot to process.
        """
        loop = asyncio.get_running_loop()
        enriched: EnrichedSnapshot = await loop.run_in_executor(
            self._executor,
            calculate_vpin_ofi_sync,
            snapshot,
        )
        logger.debug(
            "Phase B: Enriched snapshot",
            extra={
                "ticker": enriched.ticker,
                "vpin": enriched.vpin_score,
                "ofi": enriched.ofi_score,
            },
        )
        # Publish enriched result for Phase C (if top-ranked)
        # Ranking logic lives in the orchestrator, not here
```

---

## 5. TASK LIFECYCLE MANAGEMENT

```python
# backend/main.py (orchestrator)
import asyncio
import signal
from concurrent.futures import ProcessPoolExecutor

from backend.bus.event_bus import EventBus
from backend.hub.market_data_hub import MarketDataHub
from backend.phases.phase_b.microstructure_engine import MicrostructureEngine


async def main() -> None:
    """System entrypoint. Wires components and manages lifecycle."""
    bus = EventBus()
    executor = ProcessPoolExecutor(max_workers=4)
    hub = MarketDataHub(settings=load_settings())

    phase_b_engine = MicrostructureEngine(event_bus=bus, executor=executor)

    # Each worker runs in its own isolated task
    tasks = [
        asyncio.create_task(phase_b_engine.run(), name="phase_b_worker"),
        # ... phase_c, phase_d, etc.
    ]

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s, tasks, executor)))

    await asyncio.gather(*tasks, return_exceptions=True)


async def shutdown(
    signal_received: signal.Signals,
    tasks: list[asyncio.Task],
    executor: ProcessPoolExecutor,
) -> None:
    """Graceful shutdown: cancel tasks, shutdown executor, log completion."""
    logger.info("Shutdown: %s received. Stopping all workers.", signal_received.name)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    executor.shutdown(wait=True)
    logger.info("All workers stopped. Clean shutdown complete.")
```

---

## 6. BACKPRESSURE THRESHOLDS

| Queue | Max Size | Policy | Alert |
|-------|----------|--------|-------|
| Standard (Phase B/C) | 10,000 | Drop-Oldest | `WARNING` log |
| Priority (Phase D) | 1,000 | Drop-Oldest | `CRITICAL` log |

If `CRITICAL` alerts fire frequently, it means Phase D cannot keep up with
Phase C output. The solution is to reduce Phase C output rate — NOT to
increase queue size.

---

## 7. FORBIDDEN PATTERNS

```python
# ❌ FORBIDDEN: Synchronous sleep in async context
async def worker_loop():
    time.sleep(1)   # Blocks entire event loop thread

# ✅ CORRECT
async def worker_loop():
    await asyncio.sleep(1)

# ❌ FORBIDDEN: Blocking CPU work in event loop
async def process(snapshot):
    result = heavy_matrix_computation(snapshot)  # Blocks event loop

# ✅ CORRECT
async def process(snapshot):
    result = await loop.run_in_executor(executor, heavy_matrix_computation, snapshot)

# ❌ FORBIDDEN: Sharing mutable state between tasks
shared_list = []  # Accessed by multiple tasks without lock → race condition

# ✅ CORRECT: Use asyncio.Queue for inter-task communication
# ✅ CORRECT: Use asyncio.Lock if shared state is unavoidable

# ❌ FORBIDDEN: Stopping the bus on a single task failure
async def worker():
    snapshot = await queue.get()
    process(snapshot)   # Unhandled exception kills the entire bus

# ✅ CORRECT: Wrap in try/except, log and continue
async def worker():
    async for snapshot in bus.consume():
        try:
            await process(snapshot)
        except Exception:
            logger.error("Processing failed", exc_info=True)
            # Bus continues
```

---

## 8. MONITORING CHECKLIST

Log these metrics in production for visibility:

```python
# At each queue consumption point:
logger.info("EventBus queue size", extra={
    "standard_queue_size": bus._standard_queue.qsize(),
    "priority_queue_size": bus._priority_queue.qsize(),
})

# At Phase transitions:
logger.info("Phase transition", extra={
    "from_phase": "A",
    "to_phase": "B",
    "candidate_count": len(candidates),
    "duration_ms": elapsed_ms,
})
```
