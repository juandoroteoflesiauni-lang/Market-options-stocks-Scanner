"""Tests for dual-loop scheduler policy. # [TH]"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.tasks.dual_loop_policy import DualLoopConfig, DualLoopGate


def test_first_tick_is_slow() -> None:
    gate = DualLoopGate(DualLoopConfig(enabled=True, fast_interval_s=75, slow_interval_s=240))
    now = datetime(2026, 6, 16, 15, 0, tzinfo=UTC)
    assert gate.resolve_mode(now) == "slow"


def test_fast_between_slow_intervals() -> None:
    cfg = DualLoopConfig(enabled=True, fast_interval_s=75, slow_interval_s=240)
    gate = DualLoopGate(cfg)
    t0 = datetime(2026, 6, 16, 15, 0, tzinfo=UTC)
    gate.mark_slow_completed(t0)
    assert gate.resolve_mode(t0 + timedelta(seconds=90)) == "fast"
    assert gate.resolve_mode(t0 + timedelta(seconds=240)) == "slow"


def test_mutex_blocks_overlap() -> None:
    gate = DualLoopGate(DualLoopConfig())
    assert gate.try_acquire() is True
    assert gate.try_acquire() is False
    gate.release()
    assert gate.try_acquire() is True
    gate.release()


def test_disabled_always_slow() -> None:
    gate = DualLoopGate(DualLoopConfig(enabled=False, fast_interval_s=75, slow_interval_s=240))
    t0 = datetime(2026, 6, 16, 15, 0, tzinfo=UTC)
    gate.mark_slow_completed(t0)
    assert gate.resolve_mode(t0 + timedelta(seconds=90)) == "slow"
