import asyncio
import logging

import numpy as np

from backend.config.phase_thresholds import get_active_weights
from backend.hub.market_data_hub import MarketDataHub
from backend.models.market_snapshot import MarketSnapshot
from backend.models.phase_a_filter import PhaseAFilterResult
from backend.models.strategy_weights import PhaseAWeights
from backend.phases.phase_a.filters import PhaseAGlobalFilter
from backend.phases.phase_a.hard_veto import HardVetoChecker
from backend.phases.phase_a.regime_proxy import RegimeOverride, RegimeProxy
from backend.phases.phase_a.worker_pool import ApiKeyPool, scan_ticker_batch
from backend.quant_engine.math.technical.technical import TechnicalMath
from backend.services.market_breadth_tracker import MarketBreadthTracker

logger = logging.getLogger(__name__)

_MIN_BARS_FOR_SUPERTREND = 20


class Scanner:
    """Orchestrates Phase A data ingestion, validation and global filtering.

    Pipeline:
      0. Fetch VIX regime proxy and adjust thresholds (antes del scan)
      1. Fetch universe data concurrently (ApiKeyPool + scan_ticker_batch)
         ├─ Por ticker: MarketSnapshot + daily_change_pct (FMP quote)
         ├─ Divergence check: 15m vs 1D → VETO_COMPLETE_CONTRADICTION
         └─ Si pasa: snapshot validado
      2. Validate Pydantic (MarketSnapshot)
      3. Run Hard Vetoes (cortocircuito: NO_DATA, ILLIQUID, EXTREME_EXHAUSTION)
      4. Run 6 classic indicator filters (PhaseAGlobalFilter)
      5. Return only tickers that pass all gates
    """

    def __init__(
        self,
        hub: MarketDataHub,
        api_keys: list[str],
        chunk_size: int = 50,
        breadth_tracker: MarketBreadthTracker | None = None,
    ) -> None:
        self._hub = hub
        self._key_pool = ApiKeyPool(api_keys=api_keys)
        self._chunk_size = chunk_size
        self._filter = PhaseAGlobalFilter()
        self._breadth = breadth_tracker

    def _record_breadth(self, snapshot: MarketSnapshot, cfg: PhaseAWeights) -> None:
        """Registra el breadth de mercado para un snapshot individual."""
        if self._breadth is None:
            return
        has_data, direction = _get_supertrend_regime(snapshot, cfg)
        if not has_data:
            self._breadth.record_no_data()
        elif direction == 1:
            self._breadth.record_bullish()
        else:
            self._breadth.record_bearish()

    async def scan_universe(self, universe: list[str]) -> list[MarketSnapshot]:
        """Scans the universe and returns only high-quality MarketSnapshots.

        Steps:
          - Fetches VIX regime proxy and adjusts thresholds if regime != NORMAL
          - Fetches data concurrently in chunks
          - Validates each ticker into a MarketSnapshot
          - Hard Vetoes first: NO_DATA, ILLIQUID, EXTREME_EXHAUSTION
          - Runs the 6-filter global gate on non-vetoed snapshots
          - Discards tickers that fail any gate
          - Logs detailed rejection reasons for transparency
        """
        logger.info(f"Phase A: Scanning universe of {len(universe)} tickers")

        # ── Breadth tracker: reset for this cycle ───────────────────────────
        if self._breadth is not None:
            self._breadth.reset()

        # ── Regime proxy: ajustar umbrales según VIX antes de escanear ──────
        regime: RegimeOverride | None = await RegimeProxy.fetch_override(self._hub)
        cfg: PhaseAWeights = regime.adjusted if regime is not None else get_active_weights().phase_a

        chunks = [
            universe[i : i + self._chunk_size] for i in range(0, len(universe), self._chunk_size)
        ]

        tasks = []
        async with asyncio.TaskGroup() as tg:
            for chunk in chunks:
                task = tg.create_task(
                    scan_ticker_batch(
                        ticker_batch=chunk,
                        hub=self._hub,
                        key_pool=self._key_pool,
                    )
                )
                tasks.append(task)

        # Aggregate + filter
        valid_snapshots: list[MarketSnapshot] = []
        total_attempted = 0
        accepted = 0
        rejected = 0

        for t in tasks:
            batch_results = t.result()
            for snap in batch_results:
                total_attempted += 1

                self._record_breadth(snap, cfg)

                veto = HardVetoChecker.check(snap, cfg)
                if veto.vetoed:
                    rejected += 1
                    logger.info(
                        "Phase A: %s VETOED [%s] — %s",
                        snap.ticker,
                        veto.veto_type,
                        veto.reason,
                    )
                    continue

                filter_result: PhaseAFilterResult = self._filter.evaluate(snap, cfg=cfg)
                if filter_result.accepted:
                    # ── Fast-Track: prioridad si quality_score > 90 + volumen anómalo ─
                    if _is_high_priority(snap, filter_result):
                        snap = snap.model_copy(update={"high_priority": True})
                        logger.info(
                            "Phase A: %s FAST-TRACK — score=%.1f vol_ratio=%.1f",
                            snap.ticker,
                            filter_result.quality_score,
                            snap.volume / snap.avg_volume if snap.avg_volume > 0 else 0,
                        )
                    valid_snapshots.append(snap)
                    accepted += 1
                else:
                    rejected += 1
                    logger.debug(
                        "Phase A: %s rejected — %s",
                        snap.ticker,
                        filter_result.rejection_reason,
                    )

        logger.info(
            "Phase A: %d accepted / %d rejected / %d total — yielding %d",
            accepted,
            rejected,
            total_attempted,
            len(valid_snapshots),
        )
        return valid_snapshots


_MIN_VOLUME_RATIO_FOR_PRIORITY = 1.5
_MIN_QUALITY_SCORE_FOR_PRIORITY = 90.0


def _is_high_priority(
    snapshot: MarketSnapshot,
    filter_result: PhaseAFilterResult,
) -> bool:
    """Determina si un snapshot merece prioridad (Fast-Track).

    Condiciones:
      1. quality_score >= 90 (filtros tecnicos sobresalientes)
      2. Volumen actual >= 1.5x volumen promedio diario
    """
    if filter_result.quality_score < _MIN_QUALITY_SCORE_FOR_PRIORITY:
        return False
    if snapshot.avg_volume <= 0:
        return False
    return snapshot.volume >= _MIN_VOLUME_RATIO_FOR_PRIORITY * snapshot.avg_volume


def _get_supertrend_regime(
    snapshot: MarketSnapshot,
    cfg: PhaseAWeights,
) -> tuple[bool, int]:
    """Retorna (has_data, direction) del SuperTrend.

    direction:
        1  → bullish (precio sobre la banda)
        -1 → bearish (precio bajo la banda)

    Retorna (False, 0) si no hay suficientes datos OHLCV.
    """
    if len(snapshot.ohlcv) < _MIN_BARS_FOR_SUPERTREND:
        return False, 0

    close = np.array([float(b.close) for b in snapshot.ohlcv], dtype=np.float64)
    high = np.array([float(b.high) for b in snapshot.ohlcv], dtype=np.float64)
    low = np.array([float(b.low) for b in snapshot.ohlcv], dtype=np.float64)

    _, direction = TechnicalMath.supertrend(
        close,
        high,
        low,
        n=cfg.supertrend_period,
        multiplier=cfg.supertrend_multiplier,
    )

    if len(direction) == 0:
        return False, 0

    return True, int(direction[-1])
