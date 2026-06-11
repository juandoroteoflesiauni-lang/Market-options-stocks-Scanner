"""Legacy QuantitativeEngine — delegates to backend.phases.phase_b.

Kept for backward compatibility. New code should use
MicrostructureEngine directly.
"""

from __future__ import annotations

import logging

from backend.bus.event_bus import EventBus
from backend.models.enriched_snapshot import EnrichedSnapshot
from backend.models.market_snapshot import MarketSnapshot
from backend.models.result import Result
from backend.phases.phase_b.microstructure_engine import MicrostructureEngine

logger = logging.getLogger(__name__)


class QuantitativeEngine:
    """Wrapper that delegates to MicrostructureEngine.

    Preserves the original public API (process_snapshot) for
    existing callers. Internally uses ProcessPoolExecutor via
    the Phase B engine.

    Args:
        event_bus: Unused, kept for backward compat.
        max_workers: Worker count for the process pool.
    """

    def __init__(self, event_bus: EventBus, max_workers: int = 4) -> None:
        _ = event_bus
        self._engine = MicrostructureEngine(max_workers=max_workers)
        logger.info(
            "QuantitativeEngine delegating to MicrostructureEngine with %d workers", max_workers
        )

    async def start_processing(self) -> None:
        pass

    async def process_snapshot(
        self,
        snapshot: MarketSnapshot,
    ) -> Result[EnrichedSnapshot]:
        try:
            enriched = await self._engine.enrich_single(snapshot)
            return Result.success(enriched)
        except Exception as exc:
            logger.error("Error enriching snapshot for %s: %s", snapshot.ticker, exc)
            return Result.failure(reason=str(exc))

    def shutdown(self) -> None:
        self._engine.shutdown()
