"""Captura institucional GEX en background — persiste snapshots R1 cada ~5 min. # [PD-3][TH]"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.config.alpaca_priority_route import resolve_route1_watchlist
from backend.config.logger_setup import get_logger
from backend.config.sqlite_db_paths import OPTIONS_GEX_SNAPSHOTS_DB
from backend.services.options_gex_institutional_scheduler import (
    OptionsInstitutionalScheduler,
    OptionsSchedulerJob,
    default_options_institutional_jobs,
)

logger = get_logger(__name__)

_DEFAULT_POLL_S = 60
_DEFAULT_RISK_FREE = 0.04
_DEFAULT_CONCURRENCY = 3

_service: OptionsGexInstitutionalCaptureService | None = None


def options_gex_capture_enabled() -> bool:
    raw = os.getenv("OPTIONS_GEX_INSTITUTIONAL_CAPTURE_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_options_gex_capture_service() -> OptionsGexInstitutionalCaptureService:
    global _service
    if _service is None:
        _service = OptionsGexInstitutionalCaptureService()
    return _service


def configure_options_gex_capture_service(
    service: OptionsGexInstitutionalCaptureService,
) -> None:
    global _service
    _service = service


@dataclass(frozen=True)
class OptionsGexCaptureStats:
    last_poll_at: str | None = None
    last_jobs: tuple[str, ...] = ()
    last_symbols_captured: int = 0
    last_errors: tuple[str, ...] = ()
    total_snapshots_persisted: int = 0
    total_polls: int = 0


@dataclass
class OptionsGexInstitutionalCaptureService:
    """Ejecuta el scheduler institucional y persiste snapshots vía options_snapshot_service."""

    symbols: tuple[str, ...] = field(default_factory=resolve_route1_watchlist)
    risk_free_rate: float = _DEFAULT_RISK_FREE
    poll_interval_s: int = _DEFAULT_POLL_S
    concurrency: int = _DEFAULT_CONCURRENCY
    cadence_minutes: int = 5
    _scheduler: OptionsInstitutionalScheduler | None = field(default=None, init=False, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _stats: OptionsGexCaptureStats = field(
        default_factory=OptionsGexCaptureStats, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self._scheduler is None:
            self._scheduler = OptionsInstitutionalScheduler(
                jobs=default_options_institutional_jobs(cadence_minutes=self.cadence_minutes),
                snapshot_runner=lambda _job, _local: None,
            )

    def stats(self) -> OptionsGexCaptureStats:
        return self._stats

    def start_background(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="options_gex_institutional_capture")
        logger.info(
            "options_gex_capture.started symbols=%d poll_s=%d db=%s",
            len(self.symbols),
            self.poll_interval_s,
            OPTIONS_GEX_SNAPSHOTS_DB,
        )
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("options_gex_capture.stopped")

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("options_gex_capture.poll_failed error=%s", str(exc)[:180])
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=float(self.poll_interval_s))
            except TimeoutError:
                continue

    async def _poll_once(self) -> None:
        ran_jobs = self._scheduler.run_due()
        if not ran_jobs:
            self._stats = OptionsGexCaptureStats(
                last_poll_at=datetime.now(tz=UTC).isoformat(),
                last_jobs=(),
                last_symbols_captured=0,
                last_errors=(),
                total_snapshots_persisted=self._stats.total_snapshots_persisted,
                total_polls=self._stats.total_polls + 1,
            )
            return

        errors: list[str] = []
        captured = 0
        sem = asyncio.Semaphore(max(1, self.concurrency))

        async def _one(symbol: str, job: OptionsSchedulerJob) -> None:
            nonlocal captured
            async with sem:
                try:
                    await self._capture_symbol(symbol)
                    captured += 1
                except Exception as exc:
                    errors.append(f"{job.name}:{symbol}:{exc}")

        await asyncio.gather(
            *[_one(sym, job) for job in ran_jobs for sym in self.symbols],
            return_exceptions=False,
        )
        self._stats = OptionsGexCaptureStats(
            last_poll_at=datetime.now(tz=UTC).isoformat(),
            last_jobs=tuple(job.name for job in ran_jobs),
            last_symbols_captured=captured,
            last_errors=tuple(errors),
            total_snapshots_persisted=self._stats.total_snapshots_persisted + captured,
            total_polls=self._stats.total_polls + 1,
        )
        logger.info(
            "options_gex_capture.tick jobs=%s captured=%d errors=%d",
            [j.name for j in ran_jobs],
            captured,
            len(errors),
        )

    async def _capture_symbol(self, symbol: str) -> None:
        from backend.api.routes.options_router import options_snapshot_service

        t0 = time.monotonic()
        await options_snapshot_service(symbol, None, self.risk_free_rate)
        logger.debug(
            "options_gex_capture.symbol_ok symbol=%s latency_ms=%.0f",
            symbol,
            (time.monotonic() - t0) * 1000.0,
        )


__all__ = [
    "OptionsGexCaptureStats",
    "OptionsGexInstitutionalCaptureService",
    "configure_options_gex_capture_service",
    "get_options_gex_capture_service",
    "options_gex_capture_enabled",
]
