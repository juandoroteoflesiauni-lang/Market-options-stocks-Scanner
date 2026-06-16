"""End-to-end Builder pipeline: EVAL -> EVAL_PASSED_PENDING -> SIM -> payout eligible."""

from decimal import Decimal
from pathlib import Path

from backend.domain.builder_models import mffu_builder_50k_profile
from backend.services.builder_dashboard_service import BuilderDashboardService
from backend.services.builder_state_machine import (
    advance_eval_to_sim,
    evaluate_builder_phase,
)
from backend.services.builder_state_store import BuilderStateStore

PROFILE = mffu_builder_50k_profile()


def test_builder_full_lifecycle_eval_to_sim_payout(tmp_path: Path) -> None:
    db_path = tmp_path / "predictions.db"
    store = BuilderStateStore(predictions_db=db_path)

    # 1. Fresh account starts in EVAL_ACTIVE.
    state = store.load_state()
    assert state.phase == "EVAL_ACTIVE"

    # 2. Reach the evaluation profit target ($3,000) on a valid trading day.
    state = state.model_copy(
        update={
            "current_equity": Decimal("53000"),
            "trading_days_count": 1,
        }
    )
    transition = evaluate_builder_phase(state, PROFILE)
    assert transition.new_phase == "EVAL_PASSED_PENDING"
    store.save_state(transition.state)

    # 3. Advance the approved evaluation into the sim-funded phase.
    sim_transition = advance_eval_to_sim(transition.state)
    assert sim_transition.new_phase == "SIM_ACTIVE"
    store.save_state(sim_transition.state)

    # 4. Build the payout buffer with two consistent qualified days.
    funded_state = sim_transition.state.model_copy(
        update={"current_equity": Decimal("52500")}
    )
    store.save_state(funded_state)
    store.record_daily_pnl("2026-01-02", Decimal("1250"))
    store.record_daily_pnl("2026-01-03", Decimal("1250"))

    # 5. The dashboard reflects payout eligibility from persisted state.
    service = BuilderDashboardService(predictions_db=db_path)
    metrics = service.get_metrics()

    assert metrics.phase == "SIM_ACTIVE"
    assert metrics.qualified_days_count == 2
    assert metrics.payout_eligibility_state == "eligible"
    assert Decimal(metrics.withdrawable_amount) == Decimal("400.00")
    assert metrics.qualified_days_remaining == 0


def test_builder_breach_stops_lifecycle(tmp_path: Path) -> None:
    db_path = tmp_path / "predictions.db"
    store = BuilderStateStore(predictions_db=db_path)
    state = store.load_state().model_copy(
        update={
            "current_equity": Decimal("47900"),
            "high_watermark_balance": Decimal("50000"),
        }
    )

    transition = evaluate_builder_phase(state, PROFILE)

    assert transition.new_phase == "BREACHED"
    store.save_state(transition.state)

    service = BuilderDashboardService(predictions_db=db_path)
    metrics = service.get_metrics()
    assert metrics.phase == "BREACHED"
    assert metrics.survival_status == "WOULD_BREACH"
