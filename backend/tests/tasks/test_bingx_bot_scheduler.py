from __future__ import annotations
from typing import Any
"""Tests for BingXBotScheduler — fake clock, no real sleeps."""


from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backend.tasks.bingx_bot_scheduler import BingXBotScheduler, SchedulerConfig, SchedulerState

# ── Helpers ────────────────────────────────────────────────────────────────────


class _MockService:
    """Minimal async-compatible BingXBotService substitute."""

    def __init__(self) -> None:
        self.run_cycle_calls: int = 0
        self.refresh_universe_calls: int = 0
        self.dry_run: bool = True
        self._result = MagicMock()
        self._result.to_dict.return_value = {
            "started_at": "2026-05-21T10:00:00Z",
            "finished_at": "2026-05-21T10:00:05Z",
            "dry_run": True,
            "universe": ["BTC-USDT"],
            "snapshots": [],
            "signals": [],
            "decisions": [],
            "plans": [],
            "executions": [],
        }

    async def run_cycle(self, *_: Any, **__: Any) -> Any:
        self.run_cycle_calls += 1
        return self._result

    async def refresh_universe(self, *_: Any, **__: Any) -> list[Any]:
        self.refresh_universe_calls += 1
        return []


def _noop_sleep(slept: list[float]) -> Any:
    async def _sleep(s: float) -> None:
        slept.append(s)

    return _sleep


def _make_scheduler(
    *,
    now_fn: Any = None,
    sleep_fn: Any = None,
    config: SchedulerConfig | None = None,
    hc_ok_fn: Any = None,
    service: _MockService | None = None,
) -> tuple[BingXBotScheduler, _MockService, list[float]]:
    svc = service or _MockService()
    slept: list[float] = []
    return (
        BingXBotScheduler(
            service=svc,
            config=config
            or SchedulerConfig(
                cycle_interval_s=300,
                universe_refresh_interval_s=1800,
                respect_market_hours=False,
                require_healthcheck=False,
            ),
            hc_ok_fn=hc_ok_fn,
            now_fn=now_fn,
            sleep_fn=sleep_fn or _noop_sleep(slept),
        ),
        svc,
        slept,
    )


# Known UTC times mapped to Eastern Daylight Time (UTC-4, May = EDT)
_MON_MARKET_OPEN_UTC = datetime(2026, 5, 18, 14, 0, tzinfo=UTC)  # Mon 10:00 EDT
_MON_AFTER_HOURS_UTC = datetime(2026, 5, 18, 22, 0, tzinfo=UTC)  # Mon 18:00 EDT
_MON_PREMARKET_UTC = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)  # Mon 08:00 EDT
_SAT_UTC = datetime(2026, 5, 23, 14, 0, tzinfo=UTC)  # Sat 10:00 EDT


# ── SchedulerConfig ────────────────────────────────────────────────────────────


def test_scheduler_config_defaults() -> None:
    cfg = SchedulerConfig()
    assert cfg.cycle_interval_s == 300
    assert cfg.universe_refresh_interval_s == 1800
    assert cfg.respect_market_hours is True
    assert cfg.require_healthcheck is True
    assert cfg.dry_run is True


# ── Initial state ──────────────────────────────────────────────────────────────


def test_scheduler_initial_state_is_idle() -> None:
    sched, _, _ = _make_scheduler()
    assert sched.state == SchedulerState.IDLE


def test_scheduler_status_includes_expected_keys() -> None:
    sched, _, _ = _make_scheduler()
    status = sched.status()
    for key in (
        "state",
        "dry_run",
        "cycle_interval_s",
        "universe_refresh_interval_s",
        "respect_market_hours",
        "require_healthcheck",
        "cycles_completed",
        "cycles_skipped",
        "last_cycle_at",
        "last_cycle_age_s",
        "last_universe_refresh_at",
        "last_universe_age_s",
        "last_skip_reason",
        "started_at",
        "stopped_at",
    ):
        assert key in status, f"Missing key: {key}"


def test_scheduler_status_initial_values() -> None:
    sched, _, _ = _make_scheduler()
    status = sched.status()
    assert status["state"] == "idle"
    assert status["cycles_completed"] == 0
    assert status["cycles_skipped"] == 0
    assert status["last_cycle_at"] is None
    assert status["started_at"] is None
    assert status["stopped_at"] is None


# ── _should_trade_now: market hours gate ──────────────────────────────────────


def test_should_trade_when_market_hours_disabled() -> None:
    sched, _, _ = _make_scheduler(now_fn=lambda: _MON_AFTER_HOURS_UTC)
    # Default config: respect_market_hours=False
    ok, reason = sched._should_trade_now()
    assert ok is True
    assert reason == ""


def test_should_trade_during_market_hours() -> None:
    sched, _, _ = _make_scheduler(
        now_fn=lambda: _MON_MARKET_OPEN_UTC,
        config=SchedulerConfig(respect_market_hours=True, require_healthcheck=False),
    )
    ok, reason = sched._should_trade_now()
    assert ok is True
    assert reason == ""


def test_should_not_trade_after_market_close() -> None:
    sched, _, _ = _make_scheduler(
        now_fn=lambda: _MON_AFTER_HOURS_UTC,
        config=SchedulerConfig(respect_market_hours=True, require_healthcheck=False),
    )
    ok, reason = sched._should_trade_now()
    assert ok is False
    assert "hours" in reason


def test_should_not_trade_premarket() -> None:
    sched, _, _ = _make_scheduler(
        now_fn=lambda: _MON_PREMARKET_UTC,
        config=SchedulerConfig(respect_market_hours=True, require_healthcheck=False),
    )
    ok, reason = sched._should_trade_now()
    assert ok is False


def test_should_not_trade_on_weekend() -> None:
    sched, _, _ = _make_scheduler(
        now_fn=lambda: _SAT_UTC,
        config=SchedulerConfig(respect_market_hours=True, require_healthcheck=False),
    )
    ok, reason = sched._should_trade_now()
    assert ok is False
    assert "weekend" in reason


# ── _tick: core decision logic ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_refreshes_universe_on_first_call() -> None:
    sched, svc, _ = _make_scheduler()
    await sched._tick()
    assert svc.refresh_universe_calls == 1


@pytest.mark.asyncio
async def test_tick_runs_cycle_when_all_gates_open() -> None:
    sched, svc, _ = _make_scheduler()
    await sched._tick()
    assert svc.run_cycle_calls == 1
    assert sched.status()["cycles_completed"] == 1


@pytest.mark.asyncio
async def test_tick_increments_cycles_completed_on_each_tick() -> None:
    sched, _, _ = _make_scheduler()
    await sched._tick()
    await sched._tick()
    await sched._tick()
    assert sched.status()["cycles_completed"] == 3


@pytest.mark.asyncio
async def test_tick_records_last_cycle_at() -> None:
    sched, _, _ = _make_scheduler(now_fn=lambda: _MON_MARKET_OPEN_UTC)
    await sched._tick()
    assert sched.status()["last_cycle_at"] is not None


@pytest.mark.asyncio
async def test_tick_skips_cycle_outside_market_hours() -> None:
    sched, svc, _ = _make_scheduler(
        now_fn=lambda: _MON_AFTER_HOURS_UTC,
        config=SchedulerConfig(respect_market_hours=True, require_healthcheck=False),
    )
    await sched._tick()
    assert svc.run_cycle_calls == 0
    assert sched.status()["cycles_skipped"] == 1
    assert "hours" in (sched.status()["last_skip_reason"] or "")


@pytest.mark.asyncio
async def test_tick_skips_cycle_on_weekend() -> None:
    sched, svc, _ = _make_scheduler(
        now_fn=lambda: _SAT_UTC,
        config=SchedulerConfig(respect_market_hours=True, require_healthcheck=False),
    )
    await sched._tick()
    assert svc.run_cycle_calls == 0
    assert "weekend" in (sched.status()["last_skip_reason"] or "")


@pytest.mark.asyncio
async def test_tick_skips_cycle_when_healthcheck_stale() -> None:
    sched, svc, _ = _make_scheduler(
        hc_ok_fn=lambda: False,
        config=SchedulerConfig(respect_market_hours=False, require_healthcheck=True),
    )
    await sched._tick()
    assert svc.run_cycle_calls == 0
    assert sched.status()["last_skip_reason"] == "healthcheck_stale"
    assert sched.status()["cycles_skipped"] == 1


@pytest.mark.asyncio
async def test_tick_runs_cycle_when_healthcheck_green() -> None:
    sched, svc, _ = _make_scheduler(
        hc_ok_fn=lambda: True,
        config=SchedulerConfig(respect_market_hours=False, require_healthcheck=True),
    )
    await sched._tick()
    assert svc.run_cycle_calls == 1


@pytest.mark.asyncio
async def test_tick_tolerates_run_cycle_exception() -> None:
    """A cycle crash must not crash the scheduler."""
    svc = _MockService()

    async def _fail(*_: Any, **__: Any) -> Any:
        raise RuntimeError("upstream_error")

    svc.run_cycle = _fail  # type: ignore[method-assign]
    sched, _, _ = _make_scheduler(service=svc)
    await sched._tick()  # must not raise
    assert sched.status()["cycles_completed"] == 0


@pytest.mark.asyncio
async def test_tick_tolerates_universe_refresh_exception() -> None:
    """A refresh crash must not block the cycle."""
    svc = _MockService()

    async def _fail_refresh(*_: Any, **__: Any) -> list[Any]:
        raise RuntimeError("refresh_error")

    svc.refresh_universe = _fail_refresh  # type: ignore[method-assign]
    sched, _, _ = _make_scheduler(service=svc)
    await sched._tick()  # cycle still runs
    assert svc.run_cycle_calls == 1


# ── Universe refresh interval ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_universe_does_not_refresh_too_soon() -> None:
    sched, svc, _ = _make_scheduler(now_fn=lambda: _MON_MARKET_OPEN_UTC)
    await sched._tick()  # first tick → refresh
    await sched._tick()  # same time → interval not elapsed, no refresh
    assert svc.refresh_universe_calls == 1


@pytest.mark.asyncio
async def test_universe_refreshes_after_interval_elapses() -> None:
    tick_time: list[datetime] = [_MON_MARKET_OPEN_UTC]

    def clock() -> datetime:
        return tick_time[0]

    sched, svc, _ = _make_scheduler(now_fn=clock)
    await sched._tick()  # refresh at t=0
    assert svc.refresh_universe_calls == 1

    tick_time[0] = _MON_MARKET_OPEN_UTC + timedelta(seconds=1801)
    await sched._tick()  # interval elapsed → refresh again
    assert svc.refresh_universe_calls == 2


# ── start / stop ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_changes_state_to_running() -> None:
    sched, _, _ = _make_scheduler()
    await sched.start()
    assert sched.state == SchedulerState.RUNNING
    await sched.stop()


@pytest.mark.asyncio
async def test_stop_changes_state_to_stopped() -> None:
    sched, _, _ = _make_scheduler()
    await sched.start()
    await sched.stop()
    assert sched.state == SchedulerState.STOPPED


@pytest.mark.asyncio
async def test_start_sets_started_at() -> None:
    sched, _, _ = _make_scheduler(now_fn=lambda: _MON_MARKET_OPEN_UTC)
    await sched.start()
    assert sched.status()["started_at"] is not None
    await sched.stop()


@pytest.mark.asyncio
async def test_stop_sets_stopped_at() -> None:
    sched, _, _ = _make_scheduler(now_fn=lambda: _MON_MARKET_OPEN_UTC)
    await sched.start()
    await sched.stop()
    assert sched.status()["stopped_at"] is not None


@pytest.mark.asyncio
async def test_start_is_idempotent_when_already_running() -> None:
    sched, _, _ = _make_scheduler()
    await sched.start()
    task_before = sched._task
    await sched.start()  # second call is a no-op
    assert sched._task is task_before
    await sched.stop()


@pytest.mark.asyncio
async def test_stop_is_idempotent_when_idle() -> None:
    sched, _, _ = _make_scheduler()
    await sched.stop()  # must not raise
    assert sched.state == SchedulerState.IDLE


@pytest.mark.asyncio
async def test_stop_is_idempotent_when_already_stopped() -> None:
    sched, _, _ = _make_scheduler()
    await sched.start()
    await sched.stop()
    await sched.stop()  # second stop must not raise
    assert sched.state == SchedulerState.STOPPED


# ── Status after cycles ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_last_cycle_age_s_is_computed() -> None:
    base = _MON_MARKET_OPEN_UTC
    call_count: list[int] = [0]

    def clock() -> datetime:
        call_count[0] += 1
        # After tick sets _last_cycle_at=base, advance by 60s for status()
        return base if call_count[0] <= 6 else base + timedelta(seconds=60)

    sched, _, _ = _make_scheduler(now_fn=clock)
    await sched._tick()
    status = sched.status()
    assert status["last_cycle_at"] is not None
    # age_s is non-negative
    assert status["last_cycle_age_s"] is not None
    assert status["last_cycle_age_s"] >= 0.0


@pytest.mark.asyncio
async def test_skip_reason_cleared_after_successful_cycle() -> None:
    tick_time: list[datetime] = [_SAT_UTC]

    sched, svc, _ = _make_scheduler(
        now_fn=lambda: tick_time[0],
        config=SchedulerConfig(respect_market_hours=True, require_healthcheck=False),
    )

    await sched._tick()
    assert sched.status()["last_skip_reason"] == "market_closed_weekend"

    # Advance to weekday market hours
    tick_time[0] = _MON_MARKET_OPEN_UTC
    await sched._tick()
    # Universe refresh happens first (last_refresh was set during Saturday tick at that time)
    # The skip reason should be cleared after a successful cycle
    assert sched.status()["last_skip_reason"] is None


# ── Audit store integration ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_persists_to_audit_store_when_provided() -> None:
    from backend.services.bingx_audit_store import BingXAuditStore

    store = BingXAuditStore(":memory:")
    sched, _, _ = _make_scheduler()
    sched._audit_store = store

    await sched._tick()
    assert store.count() == 1


@pytest.mark.asyncio
async def test_tick_does_not_require_audit_store() -> None:
    sched, _, _ = _make_scheduler()
    assert sched._audit_store is None
    await sched._tick()  # must not raise
    assert sched.status()["cycles_completed"] == 1
