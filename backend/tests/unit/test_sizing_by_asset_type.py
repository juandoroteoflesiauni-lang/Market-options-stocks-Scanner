from decimal import Decimal
import pytest
from backend.domain.builder_models import (
    BuilderAccountState,
    BuilderProfile,
    BuilderRuleEvaluation,
)
from backend.services.builder_sizing_overlay import BuilderSizingOverlay
from backend.services.linear_instrument_sizer import LinearInstrumentSizer
from backend.services.structured_options_sizer import StructuredOptionsSizer


@pytest.fixture
def base_account_state() -> BuilderAccountState:
    return BuilderAccountState(
        account_id="test-account",
        profile_id="MFFU_BUILDER_50K",
        phase="EVAL_ACTIVE",
        initial_capital=Decimal("50000"),
        current_equity=Decimal("50000"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
        realized_daily_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
    )


@pytest.fixture
def base_profile() -> BuilderProfile:
    return BuilderProfile(
        profile_id="MFFU_BUILDER_50K",
        starting_balance=Decimal("50000"),
        profit_target=Decimal("3000"),
        daily_loss_limit=Decimal("1000"),
        max_loss=Decimal("2000"),
        payout_buffer=Decimal("2100"),
        consistency_cap=Decimal("0.50"),
        max_minis=4,
        max_micros=40,
        base_risk_per_trade_pct=Decimal("0.50"),
    )


@pytest.fixture
def base_rules() -> BuilderRuleEvaluation:
    return BuilderRuleEvaluation(
        distance_to_trailing_dd=Decimal("2000"),
        distance_to_dll_soft_pause=Decimal("1000"),
        remaining_daily_risk=Decimal("1000"),
        remaining_cycle_risk=Decimal("1000"),
        available_contract_cap=4,
        trailing_dd_floor=Decimal("48000"),
        dll_soft_pause_floor=Decimal("49000"),
        daily_loss_used=Decimal("0"),
        is_breached=False,
        blocks_new_entries=False,
    )


def test_linear_instrument_sizer_calculates_correct_contracts(base_rules) -> None:
    # ARRANGE
    factors = {"drawdown": Decimal("1.0"), "daily_buffer": Decimal("1.0"), "payout_consistency": Decimal("1.0"), "phase": Decimal("1.0")}
    
    # ACT
    decision = LinearInstrumentSizer.compute(
        symbol="MNQ",
        stop_ticks=5,
        tick_value=Decimal("50.00"),
        risk_usd=Decimal("250.00"),
        allowed_risk_pct=Decimal("0.5"),
        rules=base_rules,
        factors=factors,
    )

    # ASSERT
    assert decision.contracts == 1
    assert decision.contract_symbol == "MNQ"
    assert decision.risk_used_usd == Decimal("250.00")
    assert decision.capped_by == "builder_budget"
    assert decision.asset_type == "future"


def test_structured_options_sizer_capped_by_risk(base_rules) -> None:
    # ARRANGE
    factors = {"drawdown": Decimal("1.0"), "daily_buffer": Decimal("1.0"), "payout_consistency": Decimal("1.0"), "phase": Decimal("1.0")}

    # ACT
    decision = StructuredOptionsSizer.compute(
        symbol="SPY",
        premium_per_contract=Decimal("200.00"),
        bid_ask_spread_pct=Decimal("0.0"),
        margin_required_per_contract=Decimal("200.00"),
        available_buying_power=Decimal("5000.00"),
        max_bid_ask_spread_pct=Decimal("8.0"),
        risk_usd=Decimal("500.00"),
        allowed_risk_pct=Decimal("1.0"),
        rules=base_rules,
        factors=factors,
    )

    # ASSERT
    assert decision.contracts == 2
    assert decision.risk_used_usd == Decimal("400.00")
    assert decision.margin_required_usd == Decimal("400.00")
    assert decision.slippage_penalty_pct == Decimal("0")
    assert not decision.buying_power_limit_triggered
    assert decision.capped_by == "builder_budget"
    assert decision.asset_type == "option"


def test_structured_options_sizer_capped_by_buying_power(base_rules) -> None:
    # ARRANGE
    factors = {"drawdown": Decimal("1.0"), "daily_buffer": Decimal("1.0"), "payout_consistency": Decimal("1.0"), "phase": Decimal("1.0")}

    # ACT
    decision = StructuredOptionsSizer.compute(
        symbol="AAPL",
        premium_per_contract=Decimal("100.00"),
        bid_ask_spread_pct=Decimal("0.0"),
        margin_required_per_contract=Decimal("100.00"),
        available_buying_power=Decimal("250.00"),
        max_bid_ask_spread_pct=Decimal("8.0"),
        risk_usd=Decimal("1000.00"),
        allowed_risk_pct=Decimal("2.0"),
        rules=base_rules,
        factors=factors,
    )

    # ASSERT
    assert decision.contracts == 2
    assert decision.buying_power_limit_triggered
    assert decision.capped_by == "buying_power"
    assert decision.margin_required_usd == Decimal("200.00")


def test_structured_options_sizer_applies_slippage_penalty(base_rules) -> None:
    # ARRANGE
    factors = {"drawdown": Decimal("1.0"), "daily_buffer": Decimal("1.0"), "payout_consistency": Decimal("1.0"), "phase": Decimal("1.0")}

    # ACT
    decision = StructuredOptionsSizer.compute(
        symbol="SPY",
        premium_per_contract=Decimal("100.00"),
        bid_ask_spread_pct=Decimal("4.0"),
        margin_required_per_contract=Decimal("100.00"),
        available_buying_power=Decimal("5000.00"),
        max_bid_ask_spread_pct=Decimal("8.0"),
        risk_usd=Decimal("500.00"),
        allowed_risk_pct=Decimal("1.0"),
        rules=base_rules,
        factors=factors,
    )

    # ASSERT
    assert decision.contracts == 2
    assert decision.slippage_penalty_pct == Decimal("50.0")
    assert decision.risk_used_usd == Decimal("200.00")
    assert decision.capped_by == "builder_budget"


def test_builder_sizing_overlay_routes_correctly(base_account_state, base_profile) -> None:
    # ARRANGE
    overlay = BuilderSizingOverlay()

    # ACT - test future routing
    decision_future = overlay.compute_contracts(
        base_account_state,
        base_profile,
        symbol="MNQ",
        stop_ticks=10,
        asset_type="future",
    )

    # ACT - test option routing
    decision_option = overlay.compute_contracts(
        base_account_state,
        base_profile,
        symbol="SPY",
        asset_type="option",
        premium_per_contract=Decimal("100.00"),
        bid_ask_spread_pct=Decimal("2.0"),
        margin_required_per_contract=Decimal("100.00"),
        available_buying_power=Decimal("2000.00"),
        max_bid_ask_spread_pct=Decimal("8.0"),
    )

    # ASSERT
    assert decision_future.asset_type == "future"
    assert decision_future.contracts > 0
    assert decision_option.asset_type == "option"
    assert decision_option.contracts > 0
    assert decision_option.slippage_penalty_pct == Decimal("25.0")
