from decimal import Decimal

from backend.services.sizing_engine import MultiFactorInputs, SizingEngine, SizingRequest


def test_sizing_engine_kelly_multi_factor() -> None:
    engine = SizingEngine()
    req = SizingRequest(
        kelly_base=Decimal("0.5"),  # 0.5% base
        global_factor=Decimal("0.5"),  # Halves the risk due to macro
        multi_factors=MultiFactorInputs(f_vol=Decimal("1.0")),
        survival_recommended_risk_pct=Decimal("1.0"),
        remaining_daily_risk_pct=Decimal("3.0"),
        remaining_max_risk_pct=Decimal("5.0"),
        equity=Decimal("100000"),
        stop_distance_pct=Decimal("1.0"),
    )

    decision = engine.compute_position_size(req)
    assert decision.base_risk_pct == Decimal("0.25")
    assert decision.allowed_risk_pct == Decimal("0.25")
    assert decision.capped_by == "kelly_multi_factor"
    # Notional = 100k * (0.25 / 1.0) = 25000
    assert decision.position_notional == Decimal("25000.0")


def test_sizing_engine_capped_by_daily() -> None:
    engine = SizingEngine()
    req = SizingRequest(
        kelly_base=Decimal("1.0"),
        global_factor=Decimal("1.0"),
        multi_factors=MultiFactorInputs(),
        survival_recommended_risk_pct=Decimal("1.0"),
        remaining_daily_risk_pct=Decimal("0.2"),  # Tight daily loss remaining
        remaining_max_risk_pct=Decimal("5.0"),
        equity=Decimal("100000"),
        stop_distance_pct=Decimal("2.0"),
    )

    decision = engine.compute_position_size(req)
    assert decision.capped_by == "remaining_daily"
    assert decision.allowed_risk_pct == Decimal("0.2")
    # Notional = 100k * (0.2 / 2.0) = 10000
    assert decision.position_notional == Decimal("10000.0")


def test_sizing_engine_invalid_stop() -> None:
    engine = SizingEngine()
    req = SizingRequest(
        kelly_base=Decimal("1.0"),
        global_factor=Decimal("1.0"),
        multi_factors=MultiFactorInputs(),
        survival_recommended_risk_pct=Decimal("1.0"),
        remaining_daily_risk_pct=Decimal("1.0"),
        remaining_max_risk_pct=Decimal("1.0"),
        equity=Decimal("100000"),
        stop_distance_pct=Decimal("-1.0"),  # Invalid
    )

    decision = engine.compute_position_size(req)
    assert decision.capped_by == "invalid_stop"
    assert decision.allowed_risk_pct == Decimal("0.0")
    assert decision.position_notional == Decimal("0.0")
