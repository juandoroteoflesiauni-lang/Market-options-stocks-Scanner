from decimal import Decimal

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.builder_models import mffu_builder_50k_profile
from backend.services.builder_rule_engine import BuilderRuleEngine
from backend.services.builder_sizing_overlay import BuilderSizingOverlay
from backend.services.builder_state_machine import default_builder_account_state


def test_usd_to_contracts_uses_floor_and_tick_value() -> None:
    overlay = BuilderSizingOverlay()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile)

    # MNQ: tick_value 0.50, 10 ticks stop -> $5 risk per contract
    # 0.5% of 50k = $250 budget -> floor(250/5) = 50, capped to 40 micros
    decision = overlay.compute_contracts(
        state,
        profile,
        symbol="MNQ",
        stop_ticks=10,
    )

    assert decision.contract_symbol == "MNQ"
    assert decision.contracts == 40
    assert decision.risk_used_usd == Decimal("200")
    assert decision.capped_by == "contract_cap"


def test_invalid_stop_returns_zero_contracts() -> None:
    overlay = BuilderSizingOverlay()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile)

    decision = overlay.compute_contracts(
        state,
        profile,
        symbol="NQ",
        stop_ticks=0,
    )

    assert decision.contracts == 0
    assert decision.capped_by == "blocked"


def test_sim_phase_reduces_size_vs_eval() -> None:
    thresholds = FundingThresholds(
        builder_phase_factor_eval=Decimal("1.0"),
        builder_phase_factor_sim=Decimal("0.50"),
    )
    overlay = BuilderSizingOverlay(thresholds=thresholds)
    profile = mffu_builder_50k_profile()
    eval_state = default_builder_account_state(profile)
    sim_state = eval_state.model_copy(update={"phase": "SIM_ACTIVE"})

    eval_decision = overlay.compute_contracts(
        eval_state,
        profile,
        symbol="MNQ",
        stop_ticks=20,
    )
    sim_decision = overlay.compute_contracts(
        sim_state,
        profile,
        symbol="MNQ",
        stop_ticks=20,
    )

    assert sim_decision.contracts < eval_decision.contracts
    assert sim_decision.builder_factors["phase"] == Decimal("0.50")


def test_soft_pause_blocks_new_contracts() -> None:
    overlay = BuilderSizingOverlay()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("49000"),
            "realized_daily_pnl": Decimal("-1000"),
        }
    )

    decision = overlay.compute_contracts(
        state,
        profile,
        symbol="MES",
        stop_ticks=8,
    )

    assert decision.contracts == 0
    assert decision.capped_by == "blocked"


def test_mini_nq_sizing_respects_contract_cap() -> None:
    overlay = BuilderSizingOverlay()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile)

    decision = overlay.compute_contracts(
        state,
        profile,
        symbol="US100.CASH",
        stop_ticks=10,
        open_mini_contracts=2,
    )

    assert decision.contract_symbol == "NQ"
    assert decision.contracts <= 2


def test_remaining_daily_risk_caps_contract_count() -> None:
    overlay = BuilderSizingOverlay()
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("49600"),
            "realized_daily_pnl": Decimal("-400"),
        }
    )

    decision = overlay.compute_contracts(
        state,
        profile,
        symbol="MES",
        stop_ticks=10,
    )

    # MES: 10 ticks * 1.25 = 12.50 risk per contract; ~600 USD daily remaining
    assert decision.contracts <= 48
    assert decision.risk_used_usd <= Decimal("600")
