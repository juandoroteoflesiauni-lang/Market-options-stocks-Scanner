"""Alpaca bot scheduler — ciclos recurrentes con auditoría. # [PD-3][TH]"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any

from backend.config.logger_setup import get_logger
from backend.tasks.bingx_bot_scheduler import SchedulerState, _et_now
from backend.tasks.dual_loop_policy import DualLoopConfig, DualLoopGate

logger = get_logger(__name__)

_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)


@dataclass
class AlpacaSchedulerConfig:
    """Política de timing para ``AlpacaBotScheduler``."""

    cycle_interval_s: int = 180
    fast_interval_s: int = 75
    slow_interval_s: int = 240
    dual_loop_enabled: bool = True
    respect_market_hours: bool = True
    dry_run: bool = False

    @classmethod
    def from_dual_loop(cls, dual: DualLoopConfig, **kwargs: object) -> AlpacaSchedulerConfig:
        """Build config from shared dual-loop policy."""
        return cls(
            cycle_interval_s=dual.slow_interval_s,
            fast_interval_s=dual.fast_interval_s,
            slow_interval_s=dual.slow_interval_s,
            dual_loop_enabled=dual.enabled,
            **kwargs,  # type: ignore[arg-type]
        )


class AlpacaBotScheduler:
    """Loop asyncio para ``AlpacaBotService.run_cycle()``."""

    def __init__(
        self,
        service: Any,
        config: AlpacaSchedulerConfig | None = None,
        *,
        audit_store: Any | None = None,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._service = service
        self._config = config or AlpacaSchedulerConfig()
        if hasattr(service, "dry_run"):
            self._config.dry_run = bool(service.dry_run)
        self._audit_store = audit_store
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._sleep = sleep_fn or asyncio.sleep
        self._state = SchedulerState.IDLE
        self._task: asyncio.Task[None] | None = None
        self._cycles_completed = 0
        self._cycles_skipped = 0
        self._last_skip_reason: str | None = None
        self._last_cycle_at: datetime | None = None
        self._started_at: str | None = None
        self._stopped_at: str | None = None
        self._dual_gate = DualLoopGate(
            DualLoopConfig(
                enabled=self._config.dual_loop_enabled,
                fast_interval_s=self._config.fast_interval_s,
                slow_interval_s=self._config.slow_interval_s,
            )
        )
        self._fast_cycles_completed = 0
        self._slow_cycles_completed = 0

    @property
    def state(self) -> SchedulerState:
        return self._state

    async def start(self) -> None:
        if self._state == SchedulerState.RUNNING:
            return
        self._state = SchedulerState.RUNNING
        self._started_at = self._now().isoformat()
        self._stopped_at = None
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run_loop(), name="alpaca_bot_scheduler")
        logger.info(
            "alpaca_scheduler.started dual_loop=%s fast_s=%d slow_s=%d market_hours=%s dry_run=%s",
            self._config.dual_loop_enabled,
            self._config.fast_interval_s,
            self._config.slow_interval_s,
            self._config.respect_market_hours,
            self._config.dry_run,
        )

    async def stop(self) -> None:
        if self._state not in {SchedulerState.RUNNING, SchedulerState.STOPPING}:
            return
        self._state = SchedulerState.STOPPING
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._state = SchedulerState.STOPPED
        self._stopped_at = self._now().isoformat()
        logger.info("alpaca_scheduler.stopped")

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "dry_run": self._config.dry_run,
            "cycle_interval_s": self._config.cycle_interval_s,
            "fast_interval_s": self._config.fast_interval_s,
            "slow_interval_s": self._config.slow_interval_s,
            "dual_loop_enabled": self._config.dual_loop_enabled,
            "fast_cycles_completed": self._fast_cycles_completed,
            "slow_cycles_completed": self._slow_cycles_completed,
            "cycle_in_flight": self._dual_gate.in_flight,
            "cycles_completed": self._cycles_completed,
            "cycles_skipped": self._cycles_skipped,
            "last_skip_reason": self._last_skip_reason,
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
            "started_at": self._started_at,
            "stopped_at": self._stopped_at,
        }

    async def _run_loop(self) -> None:
        sleep_s = (
            self._config.fast_interval_s
            if self._config.dual_loop_enabled
            else self._config.cycle_interval_s
        )
        try:
            while self._state == SchedulerState.RUNNING:
                await self._tick()
                await self._sleep(sleep_s)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("alpaca_scheduler.loop_crashed error=%s", exc)

    async def _tick(self) -> None:
        if not self._dual_gate.try_acquire():
            self._cycles_skipped += 1
            self._last_skip_reason = "previous_cycle_in_flight"
            logger.info("alpaca_scheduler.cycle_skipped reason=previous_cycle_in_flight")
            return
        try:
            await self._tick_inner()
        finally:
            self._dual_gate.release()

    async def _tick_inner(self) -> None:
        ok, reason = self._should_trade_now()
        if not ok:
            self._cycles_skipped += 1
            self._last_skip_reason = reason
            logger.info("alpaca_scheduler.cycle_skipped reason=%s", reason)
            return
        mode = (
            self._dual_gate.resolve_mode(self._now())
            if self._config.dual_loop_enabled
            else "slow"
        )
        try:
            result = await self._service.run_cycle(cycle_mode=mode)
            self._last_cycle_at = self._now()
            self._cycles_completed += 1
            if mode == "slow":
                self._slow_cycles_completed += 1
                self._dual_gate.mark_slow_completed(self._last_cycle_at)
            else:
                self._fast_cycles_completed += 1
            self._last_skip_reason = None
            logger.info(
                "alpaca_scheduler.cycle_completed mode=%s count=%d executions=%d",
                mode,
                self._cycles_completed,
                len(result.executions),
            )
            await self._audit_cycle(result)
        except Exception as exc:
            logger.exception("alpaca_scheduler.cycle_failed error=%s", exc)
            await self._audit_error(exc)

    def _should_trade_now(self) -> tuple[bool, str]:
        if not self._config.respect_market_hours:
            return True, ""
        now_et = _et_now(self._now())
        if now_et.weekday() >= 5:
            return False, "market_closed_weekend"
        now_time = now_et.time().replace(second=0, microsecond=0)
        if now_time < _MARKET_OPEN or now_time >= _MARKET_CLOSE:
            return False, "market_closed_hours"
        return True, ""

    async def _audit_cycle(self, result: Any) -> None:
        if self._audit_store is not None:
            try:
                cid = self._audit_store.persist_cycle(result)
                logger.info("alpaca_scheduler.cycle_audited cycle_id=%s", cid)
            except Exception as exc:
                logger.warning("alpaca_scheduler.audit_store_failed error=%s", exc)
        try:
            from backend.audit.hooks import audit_alpaca_cycle

            await audit_alpaca_cycle(result)
        except Exception as exc:
            logger.warning("alpaca_scheduler.audit_complex_failed error=%s", exc)

    async def _audit_error(self, exc: BaseException) -> None:
        try:
            from backend.audit.hooks import audit_error

            await audit_error(
                module="alpaca",
                error_type=exc.__class__.__name__,
                message=str(exc),
                exc=exc,
                context={"scheduler": "alpaca"},
            )
        except Exception:
            pass


__all__ = ["AlpacaBotScheduler", "AlpacaSchedulerConfig"]
