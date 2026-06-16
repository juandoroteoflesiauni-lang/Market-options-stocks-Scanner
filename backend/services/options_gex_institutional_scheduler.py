from __future__ import annotations
"""Scheduling primitives for institutional Options/GEX snapshots.

This module is intentionally framework-agnostic: cron, APScheduler, Celery or a
FastAPI lifespan task can call ``run_due`` and provide the actual snapshot
runner. The scheduler owns timing, idempotency per slot and the canonical job
set; data fetching and persistence stay outside.
"""


from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

SnapshotRunner = Callable[["OptionsSchedulerJob", datetime], object]
AnalyticsLoader = Callable[[str, str | None, float], object]


@dataclass(frozen=True)
class OptionsSchedulerJob:
    name: str
    purpose: str
    hour: int | None = None
    minute: int | None = None
    every_minutes: int | None = None
    start_hour: int | None = None
    start_minute: int | None = None
    end_hour: int | None = None
    end_minute: int | None = None
    expiration_only: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class OptionsSchedulerRun:
    ran_jobs: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    persisted_count: int = 0
    errors: list[str] = field(default_factory=list)


def default_options_institutional_jobs(cadence_minutes: int = 5) -> list[OptionsSchedulerJob]:
    return [
        OptionsSchedulerJob(
            "premarket_snapshot", "warm chain, surface and prior OI context", hour=8, minute=45
        ),
        OptionsSchedulerJob(
            "opening_snapshot", "capture opening flow and initial dealer state", hour=9, minute=30
        ),
        OptionsSchedulerJob(
            "intraday_refresh",
            "persist institutional snapshots independent of UI requests",
            every_minutes=cadence_minutes,
            start_hour=9,
            start_minute=35,
            end_hour=15,
            end_minute=55,
        ),
        OptionsSchedulerJob(
            "close_snapshot", "lock closing chain state and risk overlay", hour=16, minute=0
        ),
        OptionsSchedulerJob(
            "post_close_oi_update",
            "refresh OCC/open-interest updates when vendor publishes",
            hour=18,
            minute=30,
        ),
        OptionsSchedulerJob(
            "expiry_rollover",
            "roll active expiry scope and pin-risk history",
            hour=16,
            minute=15,
            expiration_only=True,
        ),
    ]


class OptionsInstitutionalScheduler:
    def __init__(
        self,
        *,
        jobs: list[OptionsSchedulerJob] | None = None,
        snapshot_runner: SnapshotRunner | None = None,
        timezone: str = "America/New_York",
    ) -> None:
        self.jobs = jobs or default_options_institutional_jobs()
        self.snapshot_runner = snapshot_runner
        self.timezone = ZoneInfo(timezone)
        self._executed_slots: set[tuple[str, str]] = set()

    def due_jobs(self, now: datetime | None = None) -> list[OptionsSchedulerJob]:
        local = self._local_now(now)
        if local.weekday() >= 5:
            return []
        return [job for job in self.jobs if job.enabled and self._is_due(job, local)]

    def run_due(self, now: datetime | None = None) -> list[OptionsSchedulerJob]:
        local = self._local_now(now)
        due: list[OptionsSchedulerJob] = []
        for job in self.due_jobs(local):
            slot = (job.name, local.strftime("%Y-%m-%dT%H:%M"))
            if slot in self._executed_slots:
                continue
            self._executed_slots.add(slot)
            due.append(job)
            if self.snapshot_runner is not None:
                self.snapshot_runner(job, local)
        return due

    def _local_now(self, now: datetime | None) -> datetime:
        dt = now or datetime.now(tz=UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(self.timezone)

    def _is_due(self, job: OptionsSchedulerJob, local: datetime) -> bool:
        if job.expiration_only and local.weekday() != 2:
            return False
        minutes = local.hour * 60 + local.minute
        if job.every_minutes:
            start = (job.start_hour or 0) * 60 + (job.start_minute or 0)
            end = (job.end_hour or 23) * 60 + (job.end_minute or 59)
            return start <= minutes <= end and (minutes - start) % job.every_minutes == 0
        if job.hour is None or job.minute is None:
            return False
        return local.hour == job.hour and local.minute == job.minute


class OptionsInstitutionalSnapshotOrchestrator:
    """Runs due institutional snapshots for a configured universe.

    The injected ``analytics_loader`` should call the production analytics
    service that already persists history. Keeping it injected avoids importing
    routers here and keeps scheduler execution testable.
    """

    def __init__(
        self,
        *,
        scheduler: OptionsInstitutionalScheduler,
        symbols: list[str],
        analytics_loader: AnalyticsLoader,
        risk_free_rate: float = 0.04,
        expiry: str | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.symbols = [symbol.upper().strip() for symbol in symbols if symbol.strip()]
        self.analytics_loader = analytics_loader
        self.risk_free_rate = risk_free_rate
        self.expiry = expiry

    def run_due(self, now: datetime | None = None) -> OptionsSchedulerRun:
        jobs = self.scheduler.run_due(now)
        if not jobs:
            return OptionsSchedulerRun(symbols=self.symbols)
        persisted = 0
        errors: list[str] = []
        for job in jobs:
            for symbol in self.symbols:
                try:
                    self.analytics_loader(symbol, self.expiry, self.risk_free_rate)
                    persisted += 1
                except Exception as exc:  # pragma: no cover - defensive batch isolation
                    errors.append(f"{job.name}:{symbol}:{exc}")
        return OptionsSchedulerRun(
            ran_jobs=[job.name for job in jobs],
            symbols=self.symbols,
            persisted_count=persisted,
            errors=errors,
        )
