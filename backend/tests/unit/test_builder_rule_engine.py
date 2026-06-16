from decimal import Decimal

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.builder_models import (
    BUILDER_CONTRACT_CAP_EXCEEDED,
    BUILDER_DAILY_SOFT_PAUSE_THREAT,
    BUILDER_PHASE_MISMATCH,
    BUILDER_TRAILING_DD_CRITICAL,
    mffu_builder_50k_profile,
)
from backend.services.builder_rule_engine import BuilderRuleEngine
from backend.services.builder_state_machine import default_builder_account_state, trailing_dd_floor


def test_trailing_floor_moves_with_high_watermark() -> None:
    profile = mffu_builder_50k_profile()
    base = default_builder_account_state(profile)
    raised = base.model_copy(update={"high_watermark_balance": Decimal("52000")})

    base_floor = trailing_dd_floor(base, profile)
    raised_floor = trailing_dd_floor(raised, profile)

    assert raised_floor > base_floor
    assert raised_floor == Decimal("50000")


def test_distance_metrics_at_start_of_day() -> None:
    engine = BuilderRuleEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile)

    result = engine.evaluate(state, profile)

    assert result.distance_to_trailing_dd == Decimal("2000")
    assert result.distance_to_dll_soft_pause == Decimal("1000")
    assert result.remaining_daily_risk == Decimal("1000")
    assert result.remaining_cycle_risk == Decimal("1000")
    assert result.is_breached is False
    assert result.blocks_new_entries is False


def test_daily_soft_pause_triggers_at_one_thousand_loss() -> None:
    engine = BuilderRuleEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("49000"),
            "realized_daily_pnl": Decimal("-1000"),
        }
    )

    result = engine.evaluate(state, profile)

    assert result.is_daily_soft_pause is True
    assert result.blocks_new_entries is True
    assert result.distance_to_dll_soft_pause == Decimal("0")
    assert result.remaining_daily_risk == Decimal("0")
    assert BUILDER_DAILY_SOFT_PAUSE_THREAT in result.reason_codes
    assert result.is_breached is False


def test_breach_sets_trailing_distance_zero_and_critical_reason() -> None:
    engine = BuilderRuleEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("47900"),
            "high_watermark_balance": Decimal("50000"),
        }
    )

    result = engine.evaluate(state, profile)

    assert result.is_breached is True
    assert result.distance_to_trailing_dd == Decimal("0")
    assert result.blocks_new_entries is True
    assert BUILDER_TRAILING_DD_CRITICAL in result.reason_codes


def test_contract_cap_blocks_requested_excess() -> None:
    engine = BuilderRuleEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile)

    result = engine.evaluate(
        state,
        profile,
        requested_contracts=5,
        contract_is_micro=False,
        open_mini_contracts=0,
    )

    assert result.available_contract_cap == 4
    assert result.contract_cap_exceeded is True
    assert BUILDER_CONTRACT_CAP_EXCEEDED in result.reason_codes


def test_open_contracts_reduce_available_cap() -> None:
    engine = BuilderRuleEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile)

    result = engine.evaluate(
        state,
        profile,
        contract_is_micro=True,
        open_micro_contracts=10,
    )

    assert result.available_contract_cap == 30


def test_phase_mismatch_emits_reason_code() -> None:
    engine = BuilderRuleEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(update={"phase": "BREACHED"})

    result = engine.evaluate(state, profile)

    assert BUILDER_PHASE_MISMATCH in result.reason_codes


def test_dll_threat_before_hard_pause() -> None:
    thresholds = FundingThresholds(builder_dll_soft_pause_threat_usd=Decimal("250"))
    engine = BuilderRuleEngine(thresholds=thresholds)
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("49200"),
            "realized_daily_pnl": Decimal("-800"),
        }
    )

    result = engine.evaluate(state, profile)

    assert result.is_daily_soft_pause is False
    assert BUILDER_DAILY_SOFT_PAUSE_THREAT in result.reason_codes
