from __future__ import annotations
"""Phase B — Microstructure Engine.

Enriches MarketSnapshots from Phase A with microstructure metrics:
  - OFI (Order Flow Imbalance) via OHLCV proxy
  - SMC (Smart Money Concepts) directional bias + confidence
  - VPIN (stub — pending full implementation)

Architecture:
  - Stateless orchestrator. All heavy math runs in ProcessPoolExecutor.
  - Each snapshot processed independently (embarrassingly parallel).
  - Uses quant engines from backend.quant_engine, never calls external APIs.
"""


import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from backend.models.enriched_snapshot import EnrichedSnapshot
from backend.models.market_snapshot import MarketSnapshot
from backend.quant_engine.domain.technical.smc_models import DirectionalBias
from backend.quant_engine.engines.technical.ofi_engine import (
    OFIEngineConfig,
    analyze_ofi_from_ohlcv,
)
from backend.quant_engine.engines.technical.smc_engine import SMCEngine

logger = logging.getLogger(__name__)

_MIN_BARS_FOR_MICROSTRUCTURE = 10


def _snapshot_to_df(snapshot: MarketSnapshot) -> pd.DataFrame:
    """Converts OHLCVBar tuple to a pandas DataFrame for engine consumption."""
    records = [
        {
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
        }
        for b in snapshot.ohlcv
    ]
    return pd.DataFrame(records)


def _ofi_score_from_df(df: pd.DataFrame) -> float:
    """Runs OFI analysis on OHLCV data and returns the accumulated score."""
    try:
        config = OFIEngineConfig()
        result = analyze_ofi_from_ohlcv(df, config=config)
        if result.ok:
            return float(result.latest_accumulated_ofi)
    except Exception as exc:
        logger.debug("OFI analysis failed: %s", exc)
    return 0.0


def _smc_analysis_from_df(
    df: pd.DataFrame,
    ticker: str,
) -> tuple[str | None, float]:
    """Runs SMC analysis and returns (direction, weight).

    Direction: "BULLISH", "BEARISH", or None if neutral.
    Weight: composite_score normalized to [0, 1].
    """
    try:
        engine = SMCEngine()
        result = engine.analyze(df, ticker=ticker, timeframe="1D")
        bias_str: str | None
        if result.sesgo in (DirectionalBias.BULLISH, DirectionalBias.BULLISH_WATCH):
            bias_str = "BULLISH"
        elif result.sesgo in (DirectionalBias.BEARISH, DirectionalBias.BEARISH_WATCH):
            bias_str = "BEARISH"
        else:
            bias_str = None
        weight = min(max(float(result.composite_score) / 100.0, 0.0), 1.0)
        return bias_str, weight
    except Exception as exc:
        logger.debug("SMC analysis failed for %s: %s", ticker, exc)
        return None, 0.0


def _enrich_single(snapshot: MarketSnapshot) -> EnrichedSnapshot | None:
    """CPU-bound enrichment for a single MarketSnapshot.

    Runs inside ProcessPoolExecutor — must be a module-level function
    for pickle serialisation.
    """
    try:
        has_bars = len(snapshot.ohlcv) >= _MIN_BARS_FOR_MICROSTRUCTURE

        ofi_score = 0.0
        smc_direction: str | None = None
        smc_weight = 0.0

        if has_bars:
            df = _snapshot_to_df(snapshot)
            ofi_score = _ofi_score_from_df(df)
            smc_direction, smc_weight = _smc_analysis_from_df(df, snapshot.ticker)

        return EnrichedSnapshot(
            ticker=snapshot.ticker,
            exchange=snapshot.exchange,
            price=snapshot.price,
            volume=snapshot.volume,
            exchange_timestamp=snapshot.exchange_timestamp,
            data_lineage=snapshot.data_lineage,
            ohlcv=snapshot.ohlcv,
            daily_change_pct=snapshot.daily_change_pct,
            avg_volume=snapshot.avg_volume,
            high_priority=snapshot.high_priority,
            universe_type=snapshot.universe_type,
            ofi_score=ofi_score,
            smc_direction=smc_direction,
            smc_weight=smc_weight,
        )
    except Exception as exc:
        logger.exception("Phase B enrichment failed for %s: %s", snapshot.ticker, exc)
        return None


class MicrostructureEngine:
    """Phase B orchestrator — enriches MarketSnapshots with microstructure metrics.

    Pipeline:
      1. Receives list[MarketSnapshot] from Phase A
      2. For each snapshot with OHLCV data:
         a. Converts OHLCV → pandas DataFrame
         b. Runs OFI analysis → ofi_score
         c. Runs SMC analysis → smc_direction + smc_weight
      3. Returns list[EnrichedSnapshot]
         (snapshots without OHLCV pass through with zeroed microstructure fields)

    Uses ProcessPoolExecutor for CPU-bound calculations so the event loop
    is never blocked.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ProcessPoolExecutor(max_workers=max_workers)
        logger.info("MicrostructureEngine initialised with %d workers", max_workers)

    async def enrich_batch(
        self,
        snapshots: list[MarketSnapshot],
    ) -> list[EnrichedSnapshot]:
        """Enrich a batch of snapshots concurrently.

        Args:
            snapshots: List of validated MarketSnapshots from Phase A.

        Returns:
            List of EnrichedSnapshots (only successful enrichments).
            Failed snapshots are silently skipped and logged.
        """
        if not snapshots:
            return []

        loop = asyncio.get_running_loop()
        tasks = [loop.run_in_executor(self._executor, _enrich_single, snap) for snap in snapshots]

        results: list[EnrichedSnapshot] = []
        for coro in asyncio.as_completed(tasks):
            try:
                enriched = await coro
                if enriched is not None:
                    results.append(enriched)
            except Exception as exc:
                logger.warning("Phase B task failed: %s", exc)

        logger.info(
            "Phase B: %d enriched / %d total",
            len(results),
            len(snapshots),
        )
        return results

    async def enrich_single(
        self,
        snapshot: MarketSnapshot,
    ) -> EnrichedSnapshot:
        """Enrich a single snapshot (convenience wrapper).

        Returns a snapshot with zeroed microstructure fields on failure
        rather than raising, so the pipeline can continue.
        """
        enriched = await self.enrich_batch([snapshot])
        if enriched:
            return enriched[0]
        return EnrichedSnapshot(
            ticker=snapshot.ticker,
            exchange=snapshot.exchange,
            price=snapshot.price,
            volume=snapshot.volume,
            exchange_timestamp=snapshot.exchange_timestamp,
            data_lineage=snapshot.data_lineage,
            universe_type=snapshot.universe_type,
        )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
