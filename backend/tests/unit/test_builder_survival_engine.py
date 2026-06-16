from decimal import Decimal

from backend.domain.builder_models import (
    BUILDER_PAYOUT_CONSISTENCY_RISK,
    BUILDER_TRAILING_DD_CRITICAL,
    BuilderDailyPnl,
    BuilderPayoutCycleRecord,
    mffu_builder_50k_profile,
)
from backend.services.builder_payout_engine import BuilderPayoutEngine
from backend.services.builder_rule_engine import BuilderRuleEngine
from backend.services.builder_state_machine import default_builder_account_state
from backend.services.builder_survival_engine import BuilderSurvivalEngine


def test_trailing_dd_critical_forces_zero_recommended_risk() -> None:
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("47900"),
            "high_watermark_balance": Decimal("50000"),
        }
    )
    rules = BuilderRuleEngine().evaluate(state, profile)
    survival = BuilderSurvivalEngine().evaluate(state, profile, rules)

    assert survival.status == "WOULD_BREACH"
    assert survival.score == Decimal("0")
    assert survival.recommended_risk_pct == Decimal("0")
    assert BUILDER_TRAILING_DD_CRITICAL in survival.reason_codes


def test_healthy_eval_account_scores_high() -> None:
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile)
    rules = BuilderRuleEngine().evaluate(state, profile)
    survival = BuilderSurvivalEngine().evaluate(state, profile, rules)

    assert survival.status == "SAFE"
    assert survival.score >= Decimal("70")
    assert survival.recommended_risk_pct > Decimal("0")
    assert survival.eval_progress_pct == Decimal("0")


def test_hero_day_penalizes_survival_via_payout_consistency() -> None:
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={"phase": "SIM_ACTIVE", "current_equity": Decimal("57500")}
    )
    rules = BuilderRuleEngine().evaluate(state, profile)
    payout = BuilderPayoutEngine().evaluate(
        state,
        profile,
        BuilderPayoutCycleRecord(
            cycle_id="cycle-1",
            cycle_number=1,
            buffer_target=profile.payout_buffer,
        ),
        [
            BuilderDailyPnl(date="2026-06-10", pnl=Decimal("2200")),
            BuilderDailyPnl(date="2026-06-11", pnl=Decimal("300")),
        ],
    )
    survival = BuilderSurvivalEngine().evaluate(state, profile, rules, payout)

    assert BUILDER_PAYOUT_CONSISTENCY_RISK in survival.reason_codes
    assert survival.score_components["consistency_runway"] < Decimal("1")


def test_survival_includes_payout_eligibility_state() -> None:
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={"phase": "SIM_ACTIVE", "current_equity": Decimal("52500")}
    )
    rules = BuilderRuleEngine().evaluate(state, profile)
    payout = BuilderPayoutEngine().evaluate(
        state,
        profile,
        BuilderPayoutCycleRecord(
            cycle_id="cycle-1",
            cycle_number=1,
            buffer_target=profile.payout_buffer,
        ),
        [
            BuilderDailyPnl(date="2026-06-10", pnl=Decimal("1250")),
            BuilderDailyPnl(date="2026-06-11", pnl=Decimal("1250")),
        ],
    )
    survival = BuilderSurvivalEngine().evaluate(state, profile, rules, payout)

    assert survival.payout_eligibility_state == "eligible"
    assert survival.buffer_progress_pct >= Decimal("100")
