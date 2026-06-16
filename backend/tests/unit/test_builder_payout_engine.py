from decimal import Decimal

from backend.domain.builder_models import (
    BUILDER_BUFFER_NOT_REACHED,
    BUILDER_PAYOUT_CAP_REACHED,
    BUILDER_PAYOUT_CONSISTENCY_RISK,
    BUILDER_QUALIFYING_DAYS_MISSING,
    BuilderDailyPnl,
    BuilderPayoutCycleRecord,
    mffu_builder_50k_profile,
)
from backend.services.builder_payout_engine import BuilderPayoutEngine
from backend.services.builder_state_machine import default_builder_account_state


def _cycle() -> BuilderPayoutCycleRecord:
    profile = mffu_builder_50k_profile()
    return BuilderPayoutCycleRecord(
        cycle_id="cycle-1",
        cycle_number=1,
        buffer_target=profile.payout_buffer,
    )


def test_payout_not_eligible_when_buffer_missing() -> None:
    engine = BuilderPayoutEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={"phase": "SIM_ACTIVE", "current_equity": Decimal("52000")}
    )

    result = engine.evaluate(state, profile, _cycle())

    assert result.eligible is False
    assert BUILDER_BUFFER_NOT_REACHED in result.reason_codes
    assert result.suggested_phase == "SIM_BUFFER_BUILDING"


def test_payout_not_eligible_when_qualified_days_missing() -> None:
    engine = BuilderPayoutEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={"phase": "SIM_ACTIVE", "current_equity": Decimal("52500")}
    )
    daily_pnls = [BuilderDailyPnl(date="2026-06-10", pnl=Decimal("2500"))]

    result = engine.evaluate(state, profile, _cycle(), daily_pnls)

    assert result.eligible is False
    assert BUILDER_QUALIFYING_DAYS_MISSING in result.reason_codes


def test_consistency_concentration_blocks_payout() -> None:
    engine = BuilderPayoutEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={"phase": "SIM_ACTIVE", "current_equity": Decimal("57500")}
    )
    daily_pnls = [
        BuilderDailyPnl(date="2026-06-10", pnl=Decimal("2000")),
        BuilderDailyPnl(date="2026-06-11", pnl=Decimal("500")),
    ]

    result = engine.evaluate(state, profile, _cycle(), daily_pnls)

    assert result.eligible is False
    assert result.consistency_ratio_live > profile.consistency_cap
    assert BUILDER_PAYOUT_CONSISTENCY_RISK in result.reason_codes


def test_eligible_sim_state_with_sufficient_buffer_and_days() -> None:
    engine = BuilderPayoutEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={"phase": "SIM_ACTIVE", "current_equity": Decimal("52500")}
    )
    daily_pnls = [
        BuilderDailyPnl(date="2026-06-10", pnl=Decimal("1250")),
        BuilderDailyPnl(date="2026-06-11", pnl=Decimal("1250")),
    ]

    result = engine.evaluate(state, profile, _cycle(), daily_pnls)

    assert result.eligible is True
    assert result.suggested_phase == "SIM_PAYOUT_ELIGIBLE"
    assert result.withdrawable_amount == Decimal("400.00")


def test_withdrawable_amount_capped_per_cycle() -> None:
    engine = BuilderPayoutEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={"phase": "SIM_ACTIVE", "current_equity": Decimal("60000")}
    )
    daily_pnls = [
        BuilderDailyPnl(date="2026-06-10", pnl=Decimal("5000")),
        BuilderDailyPnl(date="2026-06-11", pnl=Decimal("5000")),
    ]

    result = engine.evaluate(state, profile, _cycle(), daily_pnls)

    assert result.eligible is True
    assert result.withdrawable_amount == profile.payout_cap


def test_sync_cycle_updates_buffer_progress() -> None:
    engine = BuilderPayoutEngine()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={"current_equity": Decimal("53000")}
    )

    synced = engine.sync_cycle(state, _cycle())

    assert synced.buffer_progress == Decimal("3000")
