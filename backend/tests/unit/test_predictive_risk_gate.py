from decimal import Decimal
from typing import Any

from backend.services.predictive_risk_gate import PredictiveRiskGate


def test_predictive_risk_gate_empty_context() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context: dict[str, Any] = {}

    # ACT
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context)

    # ASSERT
    assert res.is_allowed
    assert res.size_multiplier == Decimal("1.0")
    assert not res.reasons
    assert not res.warnings


def test_predictive_risk_gate_gamma_exposure_long_block() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "predictive_signals": {
            "gamma_exposure": {
                "flip_signal": -0.85,
                "regime_context": "SHORT_GAMMA",
            }
        }
    }

    # ACT
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context)

    # ASSERT
    assert not res.is_allowed
    assert res.size_multiplier == Decimal("0.0")
    assert any("Gamma Flip" in r for r in res.reasons)


def test_predictive_risk_gate_gamma_exposure_long_size_down() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "gamma_exposure": {
            "flip_signal": -0.4,
            "regime_context": "SHORT_GAMMA",
        }
    }

    # ACT
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context)

    # ASSERT
    assert res.is_allowed
    # 0.5 (below flip) * 0.6 (SHORT_GAMMA) = 0.30
    assert res.size_multiplier == Decimal("0.3")
    assert len(res.warnings) == 2


def test_predictive_risk_gate_gamma_exposure_short_size_down() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "gamma_exposure": {
            "flip_signal": 0.8,
            "regime_context": "LONG_GAMMA",
        }
    }

    # ACT
    res = gate.evaluate(direction="SHORT", symbol="AAPL", context_data=context)

    # ASSERT
    assert res.is_allowed
    assert res.size_multiplier == Decimal("0.75")


def test_predictive_risk_gate_options_toxicity_extreme_block() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "options_toxicity": {
            "vpin_percentile": 0.98,
            "flow_regime": "STRESS",
            "net_options_flow": -0.7,
        }
    }

    # ACT
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context)

    # ASSERT
    assert not res.is_allowed
    assert res.size_multiplier == Decimal("0.0")
    assert any("EXTREME_TOXICITY_BLOCK" in r for r in res.reasons)


def test_predictive_risk_gate_options_toxicity_caution() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "options_toxicity": {
            "vpin_percentile": 0.75,
            "flow_regime": "STRESS",
            "net_options_flow": -0.7,
        }
    }

    # ACT
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context)

    # ASSERT
    assert res.is_allowed
    # 0.6 (high vpin in stress) * 0.7 (bearish net flow for LONG) = 0.42
    assert abs(res.size_multiplier - Decimal("0.42")) < Decimal("0.0001")


def test_predictive_risk_gate_markov_regime_bear_volatile_long() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "markov_regime": {
            "current_state": "BEAR_VOLATILE",
            "transition_risk": 0.85,
            "regime_signal": "CRITICAL",
        }
    }

    # ACT
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context)

    # ASSERT
    assert not res.is_allowed
    assert res.size_multiplier == Decimal("0.0")
    assert any("BEAR_VOLATILE state with high transition risk" in r for r in res.reasons)


def test_predictive_risk_gate_markov_regime_chaotic() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "markov_regime": {
            "current_state": "CHAOTIC",
            "transition_risk": 0.5,
            "regime_signal": "STABLE",
        }
    }

    # ACT
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context)

    # ASSERT
    assert res.is_allowed
    assert res.size_multiplier == Decimal("0.5")


def test_predictive_risk_gate_vsa_long_climax() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "vsa": {
            "composite_score": 50.0,
            "buy_absorption": True,
            "sell_absorption": False,
            "is_buying_climax_active": True,
        }
    }

    # ACT
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context)

    # ASSERT
    assert not res.is_allowed
    assert res.size_multiplier == Decimal("0.0")
    assert any("Buying Climax active" in r for r in res.reasons)


def test_predictive_risk_gate_vsa_divergence_and_absorption() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "vsa": {
            "composite_score": -40.0,
            "buy_absorption": False,
            "sell_absorption": True,
            "is_buying_climax_active": False,
        }
    }

    # ACT
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context)

    # ASSERT
    assert res.is_allowed
    # 0.6 (score < -35) * 0.5 (sell_abs active) = 0.30
    assert res.size_multiplier == Decimal("0.3")


def test_predictive_risk_gate_call_wall_proximity() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "gamma_exposure": {
            "flip_signal": 0.5,
            "regime_context": "LONG_GAMMA",
            "gamma_wall_up": 151.0,
            "gamma_wall_down": 140.0,
        }
    }

    # ACT
    # entry 150.0 is close to call wall 151.0 (dist: 0.67% < 0.75%)
    res = gate.evaluate(direction="LONG", symbol="AAPL", context_data=context, entry=150.0)

    # ASSERT
    assert res.is_allowed
    assert res.size_multiplier == Decimal("0.5")
    assert any("too close to Call Wall" in w for w in res.warnings)


def test_predictive_risk_gate_put_wall_proximity() -> None:
    # ARRANGE
    gate = PredictiveRiskGate()
    context = {
        "gamma_exposure": {
            "flip_signal": -0.2,
            "regime_context": "LONG_GAMMA",
            "gamma_wall_up": 160.0,
            "gamma_wall_down": 149.0,
        }
    }

    # ACT
    # entry 150.0 is close to put wall 149.0 (dist: 0.67% < 0.75%)
    res = gate.evaluate(direction="SHORT", symbol="AAPL", context_data=context, entry=150.0)

    # ASSERT
    assert res.is_allowed
    assert res.size_multiplier == Decimal("0.5")
    assert any("too close to Put Wall" in w for w in res.warnings)
