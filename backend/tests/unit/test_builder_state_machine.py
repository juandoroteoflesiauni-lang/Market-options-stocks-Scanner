from decimal import Decimal

from backend.domain.builder_models import (
    BUILDER_PHASE_MISMATCH,
    BUILDER_TRAILING_DD_CRITICAL,
    BuilderAccountState,
    mffu_builder_50k_profile,
)
from backend.services.builder_state_machine import (
    advance_eval_to_sim,
    default_builder_account_state,
    evaluate_builder_phase,
    is_breached,
)


def test_default_state_starts_in_eval_active() -> None:
    state = default_builder_account_state()

    assert state.phase == "EVAL_ACTIVE"
    assert state.current_equity == Decimal("50000")
    assert state.high_watermark_balance == Decimal("50000")


def test_eval_target_moves_account_to_eval_passed_pending() -> None:
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("53000"),
            "trading_days_count": 1,
        }
    )

    result = evaluate_builder_phase(state, profile)

    assert result.transitioned is True
    assert result.new_phase == "EVAL_PASSED_PENDING"
    assert result.state.phase == "EVAL_PASSED_PENDING"


def test_eval_target_requires_minimum_trading_days() -> None:
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("53000"),
            "trading_days_count": 0,
        }
    )

    result = evaluate_builder_phase(state, profile)

    assert result.new_phase == "EVAL_ACTIVE"
    assert result.transitioned is False


def test_trailing_dd_breach_forces_breached_phase() -> None:
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("47900"),
            "high_watermark_balance": Decimal("50000"),
        }
    )

    assert is_breached(state, profile) is True

    result = evaluate_builder_phase(state, profile)

    assert result.new_phase == "BREACHED"
    assert BUILDER_TRAILING_DD_CRITICAL in result.reason_codes


def test_advance_eval_to_sim_only_from_pending() -> None:
    pending = default_builder_account_state().model_copy(update={"phase": "EVAL_PASSED_PENDING"})

    result = advance_eval_to_sim(pending)

    assert result.transitioned is True
    assert result.new_phase == "SIM_ACTIVE"


def test_advance_eval_to_sim_rejects_wrong_phase() -> None:
    active = default_builder_account_state()

    result = advance_eval_to_sim(active)

    assert result.transitioned is False
    assert BUILDER_PHASE_MISMATCH in result.reason_codes


def test_watermark_updates_before_eval_transition() -> None:
    profile = mffu_builder_50k_profile()
    state = BuilderAccountState(
        account_id="demo",
        initial_capital=Decimal("50000"),
        current_equity=Decimal("52800"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
        trading_days_count=1,
    )

    result = evaluate_builder_phase(state, profile)

    assert result.state.high_watermark_balance == Decimal("52800")
    assert result.new_phase == "EVAL_ACTIVE"
