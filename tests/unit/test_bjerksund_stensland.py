"""Unit tests for Bjerksund-Stensland American pricer."""

from __future__ import annotations

import math

import pytest

from backend.quant_engine.math.options.bjerksund_stensland import BjerksundStenslandPricer
from backend.quant_engine.math.options.bjerksund_stensland_greeks import american_greeks
from backend.quant_engine.math.options.bsm import BlackScholesPricer, OptionType


def _european_call_with_div(
    S: float, K: float, T: float, r: float, q: float, sigma: float
) -> float:
    """European call with continuous dividend yield (not in legacy BSM scalar API)."""
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * math.exp(-q * T) * (0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))) - K * math.exp(
        -r * T
    ) * (0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0))))


def test_american_call_ge_european_with_dividends() -> None:
    S, K, T, r, q, sigma = 100.0, 100.0, 0.5, 0.05, 0.02, 0.25
    american = BjerksundStenslandPricer.price(S, K, T, r, q, sigma, "CALL")
    european = _european_call_with_div(S, K, T, r, q, sigma)
    assert american >= european - 1e-2


def test_q_zero_call_matches_bsm() -> None:
    S, K, T, r, sigma = 100.0, 100.0, 0.5, 0.05, 0.30
    american = BjerksundStenslandPricer.price(S, K, T, r, 0.0, sigma, "CALL")
    european = BlackScholesPricer.price(S, K, T, r, sigma, OptionType.CALL)
    assert american == pytest.approx(european, rel=1e-4)


def test_zero_tte_returns_intrinsic() -> None:
    assert BjerksundStenslandPricer.price(105.0, 100.0, 0.0, 0.05, 0.0, 0.2, "CALL") == 5.0
    assert BjerksundStenslandPricer.price(95.0, 100.0, 0.0, 0.05, 0.0, 0.2, "PUT") == 5.0


def test_nan_inputs_return_nan() -> None:
    assert math.isnan(
        BjerksundStenslandPricer.price(float("nan"), 100.0, 0.5, 0.05, 0.0, 0.2, "CALL")
    )


def test_american_greeks_signs() -> None:
    g = american_greeks(100.0, 100.0, 0.5, 0.05, 0.02, 0.25, "CALL")
    assert g["gamma"] >= 0.0
    assert g["vega"] >= 0.0
    assert -1.0 <= g["delta"] <= 1.0
    assert g["theoretical_price"] > 0.0


def test_american_greeks_near_bsm_when_q_zero() -> None:
    S, K, T, r, sigma = 100.0, 100.0, 0.5, 0.05, 0.25
    am = american_greeks(S, K, T, r, 0.0, sigma, "CALL")
    eu_delta = BlackScholesPricer.delta(S, K, T, r, sigma, OptionType.CALL)
    assert am["delta"] == pytest.approx(eu_delta, rel=0.05)
