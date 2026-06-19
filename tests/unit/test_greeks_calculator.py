"""Tests for GreeksCalculator european/american switch."""

from __future__ import annotations

from backend.phases.phase_c.greeks_calculator import GreeksCalculator


def test_european_path_regression() -> None:
    calc = GreeksCalculator()
    result = calc.calculate(
        spot=100.0,
        strike=100.0,
        tte_years=0.5,
        risk_free_rate=0.05,
        iv=0.25,
        option_type="CALL",
        model="european",
    )
    assert result.is_success
    g = result.unwrap()
    assert 0.0 < g.delta < 1.0
    assert g.gamma >= 0.0


def test_american_path_returns_values() -> None:
    calc = GreeksCalculator()
    result = calc.calculate(
        spot=100.0,
        strike=100.0,
        tte_years=0.5,
        risk_free_rate=0.05,
        iv=0.25,
        option_type="CALL",
        model="american",
        dividend_yield=0.02,
    )
    assert result.is_success
    g = result.unwrap()
    assert g.theoretical_price > 0.0


def test_invalid_inputs_fail() -> None:
    calc = GreeksCalculator()
    result = calc.calculate(
        spot=-1.0,
        strike=100.0,
        tte_years=0.5,
        risk_free_rate=0.05,
        iv=0.25,
        option_type="CALL",
    )
    assert result.is_failure
