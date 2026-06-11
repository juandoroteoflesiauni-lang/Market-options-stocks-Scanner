"""Scanner scheduler — periodic Phase A scan cycle manager.

Executes the full Phase A pipeline on a configurable interval:
  1. Fetches VIX regime proxy
  2. Scans universe with Hard Vetoes + 6-filter global gate
  3. Fast-Tracks high-priority snapshots
  4. Publishes all valid snapshots to EventBus

Follows the same injectable ``now_fn``/``sleep_fn`` pattern as
``BingXBotScheduler`` for testability.
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
except ImportError:
    _ET = None

from backend.bus.event_bus import EventBus
from backend.config.logger_setup import get_logger
from backend.hub.market_data_hub import MarketDataHub
from backend.phases.phase_a.scanner import Scanner
from backend.services.market_breadth_tracker import MarketBreadthTracker

logger = get_logger(__name__)

_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)
_EDT_FALLBACK = timezone(timedelta(hours=-4))


class SchedulerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class ScannerSchedulerConfig:
    """Timing and behaviour policy for ``ScannerScheduler``.

    Parameters
    ----------
    scan_interval_s:
        Seconds between full Phase A scan cycles. Minimum 60.
    respect_market_hours:
        When True, skip cycles outside 09:30-16:00 ET Mon-Fri.
    publish_to_bus:
        When True, publish valid snapshots to the EventBus after each scan.
    """

    scan_interval_s: int = 300
    respect_market_hours: bool = True
    publish_to_bus: bool = True


def _et_now(utc_now: datetime) -> datetime:
    if _ET is not None:
        return utc_now.astimezone(_ET)
    return utc_now.astimezone(_EDT_FALLBACK)


class ScannerScheduler:
    """Periodic Phase A scanner cycle manager.

    Parameters
    ----------
    hub:
        MarketDataHub for data fetching (FMP quotes, VIX, intraday candles).
    api_keys:
        List of FMP API keys for rate-limit rotation.
    event_bus:
        EventBus to publish valid snapshots to downstream phases.
    universe:
        Ticker symbols to scan each cycle.
    config:
        Timing and policy configuration.
    now_fn:
        Returns the current UTC datetime. Override in tests.
    sleep_fn:
        Async callable that sleeps N seconds. Override in tests.
    """

    def __init__(
        self,
        hub: MarketDataHub,
        api_keys: list[str],
        event_bus: EventBus,
        universe: list[str],
        breadth_tracker: MarketBreadthTracker | None = None,
        config: ScannerSchedulerConfig | None = None,
        *,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._hub = hub
        self._api_keys = api_keys
        self._event_bus = event_bus
        self._universe = universe
        self._breadth_tracker = breadth_tracker
        self._config = config or ScannerSchedulerConfig()
        self._now: Callable[[], datetime] = now_fn or (lambda: datetime.now(UTC))
        self._sleep: Callable[[float], Awaitable[None]] = sleep_fn or asyncio.sleep

        self._state: SchedulerState = SchedulerState.IDLE
        self._task: asyncio.Task[None] | None = None

        self._cycles_completed: int = 0
        self._cycles_skipped: int = 0
        self._last_scan_at: datetime | None = None
        self._last_skip_reason: str | None = None
        self._last_snapshot_count: int = 0
        self._started_at: str | None = None
        self._stopped_at: str | None = None

    @property
    def state(self) -> SchedulerState:
        return self._state

    async def start(self) -> None:
        """Start the recurring scan loop as a background asyncio task."""
        if self._state == SchedulerState.RUNNING:
            logger.warning("scanner_scheduler.already_running")
            return
        self._state = SchedulerState.RUNNING
        self._started_at = self._now().isoformat()
        self._stopped_at = None
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run_loop(), name="scanner_scheduler")
        logger.info(
            "scanner_scheduler.started scan_interval_s=%d market_hours=%s universe_size=%d",
            self._config.scan_interval_s,
            self._config.respect_market_hours,
            len(self._universe),
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
        logger.info("scanner_scheduler.stopped")

    def status(self) -> dict[str, object]:
        """Return a structured status snapshot for API or monitoring."""
        now = self._now()

        def _age(ts: datetime | None) -> float | None:
            if ts is None:
                return None
            return round((now - ts).total_seconds(), 1)

        return {
            "state": self._state.value,
            "scan_interval_s": self._config.scan_interval_s,
            "respect_market_hours": self._config.respect_market_hours,
            "publish_to_bus": self._config.publish_to_bus,
            "universe_size": len(self._universe),
            "cycles_completed": self._cycles_completed,
            "cycles_skipped": self._cycles_skipped,
            "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None,
            "last_scan_age_s": _age(self._last_scan_at),
            "last_snapshot_count": self._last_snapshot_count,
            "last_skip_reason": self._last_skip_reason,
            "started_at": self._started_at,
            "stopped_at": self._stopped_at,
        }

    async def _run_loop(self) -> None:
        """Main scheduler loop — runs until cancelled or state changes."""
        try:
            while self._state == SchedulerState.RUNNING:
                await self._tick()
                await self._sleep(self._config.scan_interval_s)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("scanner_scheduler.loop_crashed")

    async def _tick(self) -> None:
        """One scheduler tick: optionally gate on market hours, then scan."""
        if self._config.respect_market_hours:
            ok, reason = self._should_scan_now()
            if not ok:
                self._cycles_skipped += 1
                self._last_skip_reason = reason
                return

        try:
            scanner = Scanner(
                hub=self._hub,
                api_keys=self._api_keys,
                breadth_tracker=self._breadth_tracker,
            )
            snapshots = await scanner.scan_universe(self._universe)
            now = self._now()
            self._last_scan_at = now
            self._cycles_completed += 1
            self._last_snapshot_count = len(snapshots)
            self._last_skip_reason = None

            logger.info(
                "scanner_scheduler.cycle_completed snapshots=%d cycles=%d",
                len(snapshots),
                self._cycles_completed,
            )

            if self._config.publish_to_bus:
                for snap in snapshots:
                    await self._event_bus.publish(snap)
                logger.debug(
                    "scanner_scheduler.published count=%d",
                    len(snapshots),
                )
        except Exception:
            logger.exception("scanner_scheduler.cycle_failed")

    def _should_scan_now(self) -> tuple[bool, str]:
        """Return ``(True, "")`` when market hours permit a scan cycle."""
        now_et = _et_now(self._now())

        if now_et.weekday() >= 5:
            return False, "market_closed_weekend"

        now_time = now_et.time().replace(second=0, microsecond=0)
        if now_time < _MARKET_OPEN or now_time >= _MARKET_CLOSE:
            return False, "market_closed_hours"

        return True, ""
