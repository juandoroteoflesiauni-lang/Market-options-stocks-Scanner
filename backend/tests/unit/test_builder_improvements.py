"""Unit tests for Builder Plan survival/payout improvements."""

from decimal import Decimal
from pathlib import Path

from backend.domain.builder_models import (
    BUILDER_FLOOR_DRIFT_WARNING,
    BUILDER_WOULD_BREACH_ON_STOP,
    BuilderAccountState,
    BuilderDailyPnl,
    mffu_builder_50k_profile,
)
from backend.services.builder_backtest_service import BuilderBacktestService
from backend.services.builder_payout_engine import BuilderPayoutEngine
from backend.services.builder_state_machine import (
    build_intraday_dd_snapshot,
    simulate_loss_scenario,
)
from backend.services.builder_state_store import BuilderStateStore

PROFILE = mffu_builder_50k_profile()


def test_intraday_tracker_flags_floor_drift_on_new_high() -> None:
    state = BuilderAccountState(
        current_equity=Decimal("50500"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
        realized_daily_pnl=Decimal("500"),
    )
    snapshot = build_intraday_dd_snapshot(state, PROFILE)

    assert snapshot.is_new_high_watermark is True
    assert snapshot.floor_drift_usd == Decimal("500")
    assert snapshot.current_eod_floor == Decimal("48000")
    assert snapshot.projected_eod_floor == Decimal("48500")
    assert snapshot.is_floor_drift_warning is True
    assert BUILDER_FLOOR_DRIFT_WARNING in snapshot.reason_codes


def test_intraday_tracker_no_warning_when_flat() -> None:
    state = BuilderAccountState(
        current_equity=Decimal("50000"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
    )
    snapshot = build_intraday_dd_snapshot(state, PROFILE)

    assert snapshot.is_new_high_watermark is False
    assert snapshot.floor_drift_usd == Decimal("0")
    assert snapshot.is_floor_drift_warning is False


def test_loss_scenario_safe_trade_keeps_runway() -> None:
    state = BuilderAccountState(
        current_equity=Decimal("50500"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50500"),
        realized_daily_pnl=Decimal("500"),
    )
    scenario = simulate_loss_scenario(
        state,
        PROFILE,
        contracts=2,
        risk_per_contract_usd=Decimal("300"),
    )

    assert scenario.loss_if_stopped_usd == Decimal("600")
    assert scenario.equity_after_loss == Decimal("49900")
    assert scenario.breaches_trailing_dd is False
    assert scenario.triggers_daily_soft_pause is False


def test_loss_scenario_detects_breach_on_stop() -> None:
    state = BuilderAccountState(
        current_equity=Decimal("48200"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
        realized_daily_pnl=Decimal("-1800"),
    )
    scenario = simulate_loss_scenario(
        state,
        PROFILE,
        contracts=1,
        risk_per_contract_usd=Decimal("300"),
    )

    assert scenario.equity_after_loss == Decimal("47900")
    assert scenario.breaches_trailing_dd is True
    assert BUILDER_WOULD_BREACH_ON_STOP in scenario.reason_codes


def test_consistency_guidance_caps_daily_profit() -> None:
    engine = BuilderPayoutEngine()
    daily = (
        BuilderDailyPnl(date="d1", pnl=Decimal("500")),
        BuilderDailyPnl(date="d2", pnl=Decimal("500")),
    )
    guidance = engine.consistency_guidance(PROFILE, daily)

    assert guidance.total_profit == Decimal("1000")
    assert guidance.best_day_profit == Decimal("500")
    assert guidance.consistency_ratio_live == Decimal("0.5")
    assert guidance.max_profit_today_usd == Decimal("1000.00")
    assert guidance.needs_more_days is False


def test_consistency_guidance_single_day_needs_more_days() -> None:
    engine = BuilderPayoutEngine()
    guidance = engine.consistency_guidance(
        PROFILE,
        (BuilderDailyPnl(date="d1", pnl=Decimal("800")),),
    )

    assert guidance.consistency_ratio_live == Decimal("1.0")
    assert guidance.needs_more_days is True
    assert guidance.is_consistency_at_risk is True


def test_payout_plan_eligible_when_buffer_and_days_met() -> None:
    engine = BuilderPayoutEngine()
    state = BuilderAccountState(
        phase="SIM_ACTIVE",
        current_equity=Decimal("52500"),
    )
    cycle = engine_cycle()
    daily = (
        BuilderDailyPnl(date="d1", pnl=Decimal("1250")),
        BuilderDailyPnl(date="d2", pnl=Decimal("1250")),
    )
    plan = engine.payout_plan(state, PROFILE, cycle, daily)

    assert plan.is_eligible is True
    assert plan.buffer_remaining == Decimal("0")
    assert plan.estimated_days_to_payout is None
    assert plan.projected_withdrawable == Decimal("400.00")


def test_payout_plan_projects_eta_when_buffer_pending() -> None:
    engine = BuilderPayoutEngine()
    state = BuilderAccountState(
        phase="SIM_ACTIVE",
        current_equity=Decimal("51000"),
    )
    cycle = engine_cycle()
    daily = (
        BuilderDailyPnl(date="d1", pnl=Decimal("500")),
        BuilderDailyPnl(date="d2", pnl=Decimal("500")),
    )
    plan = engine.payout_plan(state, PROFILE, cycle, daily)

    assert plan.is_eligible is False
    assert plan.buffer_remaining == Decimal("1100")
    assert plan.avg_daily_profit == Decimal("500.00")
    assert plan.estimated_days_to_payout == 3


def test_daily_pnl_persistence_round_trip(tmp_path: Path) -> None:
    store = BuilderStateStore(predictions_db=tmp_path / "predictions.db")
    store.record_daily_pnl("2026-01-02", Decimal("750"))
    store.record_daily_pnl("2026-01-03", Decimal("-200"))
    store.record_daily_pnl("2026-01-02", Decimal("800"))  # upsert same day

    rows = store.list_daily_pnls()

    assert len(rows) == 2
    assert rows[0].date == "2026-01-02"
    assert rows[0].pnl == Decimal("800")
    assert rows[1].pnl == Decimal("-200")


def test_backtest_survives_and_passes_eval() -> None:
    result = BuilderBacktestService().run([1000, 1000, 1000])

    assert result.survived is True
    assert result.eval_passed is True
    assert result.eval_passed_on_day == 3
    assert result.final_equity == Decimal("53000")
    assert result.breached_on_day is None


def test_backtest_detects_trailing_dd_breach() -> None:
    result = BuilderBacktestService().run([-2100])

    assert result.survived is False
    assert result.breached_on_day == 1
    assert result.days_simulated == 1


def engine_cycle():
    """Build an in-memory open payout cycle for payout tests."""
    from backend.domain.builder_models import BuilderPayoutCycleRecord

    return BuilderPayoutCycleRecord(
        cycle_id="test-cycle",
        cycle_number=1,
        buffer_target=PROFILE.payout_buffer,
    )
