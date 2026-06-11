"""BingX bot scheduler — recurring paper/dry-run cycle manager.

Executes the full Scan → Filter → Risk → Execute pipeline on a configurable
interval. Always runs in dry-run (paper-trading) mode. Live scheduling is not
supported — use the API ``/trade`` endpoint with an explicit ``allow_live``
gate for live execution.

Key design decisions
--------------------
* ``now_fn`` and ``sleep_fn`` are injectable so every timing decision is
  unit-testable without real sleeps or wall-clock dependency.
* Market-hours gate converts UTC via pytz (with a rough EDT fallback when
  pytz is absent) and skips cycles outside 09:30–16:00 ET Mon–Fri.
* Healthcheck gate delegates to an injectable ``hc_ok_fn``; when the router
  is the host it wires ``_hc_cache_fresh`` here.
* Universe refresh is driven by elapsed time, not a separate ticker, so the
  loop stays single-threaded with no locking.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any

try:
    import pytz as _pytz

    _ET: Any = _pytz.timezone("America/New_York")
except ImportError:  # pragma: no cover
    _ET = None

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# US equity market session (New York)
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)

# Rough EDT fallback when pytz is not installed (UTC-4).
# Off by 1 hour in winter (EST = UTC-5) — acceptable for a pre-prod daemon.
_EDT_FALLBACK = timezone(timedelta(hours=-4))


class SchedulerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class SchedulerConfig:
    """Timing and behaviour policy for ``BingXBotScheduler``.

    Parameters
    ----------
    cycle_interval_s:
        Seconds between full Scan → Execute cycles. Minimum 30.
    universe_refresh_interval_s:
        Seconds between liquidity-filtered universe rebuilds. Minimum 60.
    respect_market_hours:
        When True, skip cycles outside 09:30–16:00 ET Mon–Fri (stock perps).
    require_healthcheck:
        When True, skip cycles when the provider healthcheck is stale.
    refresh_universe:
        When True, periodically rebuild the service universe from the venue.
        Disable this for bounded VST/demo experiments with an explicit symbol list.
    dry_run:
        Informational flag — always True; the underlying client enforces this.
    """

    cycle_interval_s: int = 300
    universe_refresh_interval_s: int = 1800
    respect_market_hours: bool = True
    require_healthcheck: bool = True
    refresh_universe: bool = True
    dry_run: bool = True


def _et_now(utc_now: datetime) -> datetime:
    """Convert *utc_now* to Eastern Time, with a rough fallback."""
    if _ET is not None:
        return utc_now.astimezone(_ET)
    return utc_now.astimezone(_EDT_FALLBACK)


class BingXBotScheduler:
    """Recurring paper-trading cycle manager.

    Parameters
    ----------
    service:
        Any object with ``async run_cycle()`` and ``async refresh_universe()``
        methods plus a ``dry_run: bool`` property (e.g. ``BingXBotService``).
    config:
        Timing and policy configuration.
    audit_store:
        Optional ``BingXAuditStore`` — cycles are persisted when provided.
    hc_ok_fn:
        Returns True when a recent healthcheck is green. Defaults to
        ``lambda: True`` (no gate). Wire to ``_hc_cache_fresh`` when the
        scheduler is embedded in the API server.
    now_fn:
        Returns the current UTC datetime. Override in tests with a fixed or
        advancing clock — no real wall-clock calls escape this callable.
    sleep_fn:
        Async callable that sleeps N seconds. Override in tests with a no-op
        to avoid real sleeps in the loop.
    """

    def __init__(
        self,
        service: Any,
        config: SchedulerConfig | None = None,
        *,
        audit_store: Any | None = None,
        hc_ok_fn: Callable[[], bool] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._service = service
        self._config = config or SchedulerConfig()
        self._audit_store = audit_store
        self._hc_ok: Callable[[], bool] = hc_ok_fn or (lambda: True)
        self._now: Callable[[], datetime] = now_fn or (lambda: datetime.now(UTC))
        self._sleep: Callable[[float], Awaitable[None]] = sleep_fn or asyncio.sleep

        self._state: SchedulerState = SchedulerState.IDLE
        self._task: asyncio.Task[None] | None = None

        self._last_universe_refresh_at: datetime | None = None
        self._last_cycle_at: datetime | None = None

        self._cycles_completed: int = 0
        self._cycles_skipped: int = 0
        self._last_skip_reason: str | None = None
        self._started_at: str | None = None
        self._stopped_at: str | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def state(self) -> SchedulerState:
        return self._state

    async def start(self) -> None:
        """Start the recurring loop as a background asyncio task."""
        if self._state == SchedulerState.RUNNING:
            logger.warning("bingx_scheduler.already_running")
            return
        if not self._service.dry_run:
            logger.warning(
                "bingx_scheduler.service_not_dry_run — scheduler is designed for paper trading only"
            )
        self._state = SchedulerState.RUNNING
        self._started_at = self._now().isoformat()
        self._stopped_at = None
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run_loop(), name="bingx_bot_scheduler")
        logger.info(
            "bingx_scheduler.started cycle_interval_s=%d universe_interval_s=%d "
            "market_hours=%s healthcheck_gate=%s",
            self._config.cycle_interval_s,
            self._config.universe_refresh_interval_s,
            self._config.respect_market_hours,
            self._config.require_healthcheck,
        )

    async def stop(self) -> None:
        """Signal the loop to stop and wait for it to exit cleanly."""
        if self._state not in {SchedulerState.RUNNING, SchedulerState.STOPPING}:
            return
        self._state = SchedulerState.STOPPING
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._state = SchedulerState.STOPPED
        self._stopped_at = self._now().isoformat()
        logger.info("bingx_scheduler.stopped")

    def status(self) -> dict[str, Any]:
        """Return a structured status snapshot suitable for the API response."""
        now = self._now()

        def _age(ts: datetime | None) -> float | None:
            if ts is None:
                return None
            return round((now - ts).total_seconds(), 1)

        return {
            "state": self._state.value,
            "dry_run": self._config.dry_run,
            "cycle_interval_s": self._config.cycle_interval_s,
            "universe_refresh_interval_s": self._config.universe_refresh_interval_s,
            "respect_market_hours": self._config.respect_market_hours,
            "require_healthcheck": self._config.require_healthcheck,
            "refresh_universe": self._config.refresh_universe,
            "cycles_completed": self._cycles_completed,
            "cycles_skipped": self._cycles_skipped,
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
            "last_cycle_age_s": _age(self._last_cycle_at),
            "last_universe_refresh_at": (
                self._last_universe_refresh_at.isoformat()
                if self._last_universe_refresh_at
                else None
            ),
            "last_universe_age_s": _age(self._last_universe_refresh_at),
            "last_skip_reason": self._last_skip_reason,
            "started_at": self._started_at,
            "stopped_at": self._stopped_at,
        }

    # ── Internal loop ──────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Main scheduler loop — runs until cancelled or state changes."""
        try:
            while self._state == SchedulerState.RUNNING:
                await self._tick()
                await self._sleep(self._config.cycle_interval_s)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("bingx_scheduler.loop_crashed error=%s", exc)

    async def _tick(self) -> None:
        """One scheduler tick: conditionally refresh universe, then run cycle."""
        await self._maybe_refresh_universe()

        ok, reason = self._should_trade_now()
        if not ok:
            self._cycles_skipped += 1
            self._last_skip_reason = reason
            logger.info("bingx_scheduler.cycle_skipped reason=%s", reason)
            return

        if self._config.require_healthcheck and not self._hc_ok():
            self._cycles_skipped += 1
            self._last_skip_reason = "healthcheck_stale"
            logger.info("bingx_scheduler.cycle_skipped reason=healthcheck_stale")
            return

        try:
            result = await self._service.run_cycle()
            now = self._now()
            self._last_cycle_at = now
            self._cycles_completed += 1
            self._last_skip_reason = None
            logger.info(
                "bingx_scheduler.cycle_completed count=%d",
                self._cycles_completed,
            )
        except Exception as exc:
            logger.warning("bingx_scheduler.cycle_failed error=%s", exc)
            return

        if self._audit_store is not None:
            try:
                from backend.services.bingx_audit_store import BingXAuditEntry

                entry = BingXAuditEntry.from_cycle_result(result)
                cid = self._audit_store.persist(entry)
                logger.info("bingx_scheduler.cycle_audited cycle_id=%s", cid)
            except Exception as exc:
                logger.warning("bingx_scheduler.audit_failed error=%s", exc)

    async def _maybe_refresh_universe(self) -> None:
        """Refresh the universe when the configured interval has elapsed."""
        if not self._config.refresh_universe:
            return
        now = self._now()
        if self._last_universe_refresh_at is not None:
            elapsed = (now - self._last_universe_refresh_at).total_seconds()
            if elapsed < self._config.universe_refresh_interval_s:
                return
        try:
            await self._service.refresh_universe()
            self._last_universe_refresh_at = now
            logger.info("bingx_scheduler.universe_refreshed")
        except Exception as exc:
            logger.warning("bingx_scheduler.universe_refresh_failed error=%s", exc)

    def _should_trade_now(self) -> tuple[bool, str]:
        """Return ``(True, "")`` when a cycle should execute now.

        When ``respect_market_hours=False`` always returns True.
        Otherwise skips outside 09:30–16:00 ET Mon–Fri.
        """
        if not self._config.respect_market_hours:
            return True, ""

        now_et = _et_now(self._now())

        if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
            return False, "market_closed_weekend"

        now_time = now_et.time().replace(second=0, microsecond=0)
        if now_time < _MARKET_OPEN or now_time >= _MARKET_CLOSE:
            return False, "market_closed_hours"

        return True, ""
