import pytest

from backend.engine.metrics.regime_weights import (
    MarketRegime,
    RegimeWeightingEngine,
    blend_meta_with_engines,
    get_optimal_weights_for_regime,
    update_weights_from_performance,
)


def test_regime_weights_classification():
    engine = RegimeWeightingEngine()

    # 1. Bull Quiet: Uptrend (price > ma50 & ma200) + low vol (vix < 20)
    res = engine.classify_regime(vix=15.0, spy_ma50=100.0, spy_ma200=90.0, spy_price=105.0)
    assert res.is_success
    assert res.unwrap() == MarketRegime.BULL_QUIET

    # 2. Bear Volatile: Downtrend (price < ma50 & ma200) + high vol (vix > 25)
    res = engine.classify_regime(vix=30.0, spy_ma50=100.0, spy_ma200=110.0, spy_price=95.0)
    assert res.is_success
    assert res.unwrap() == MarketRegime.BEAR_VOLATILE

    # 3. Transition: Within 2% of spy_ma50
    res = engine.classify_regime(vix=15.0, spy_ma50=100.0, spy_ma200=120.0, spy_price=101.0)
    assert res.is_success
    assert res.unwrap() == MarketRegime.TRANSITION

    # 4. Validations
    res = engine.classify_regime(vix=-5.0, spy_ma50=100.0, spy_ma200=110.0, spy_price=95.0)
    assert res.is_failure
    assert "vix" in res.reason


def test_regime_weights_adaptive():
    engine = RegimeWeightingEngine()

    # Get weights under Bull Quiet
    res = engine.get_adaptive_weights(vix=15.0, spy_ma50=100.0, spy_ma200=90.0, spy_price=105.0)
    assert res.is_success
    weights = res.unwrap()
    assert weights["momentum"] == 0.25
    assert weights["safe_haven"] == 0.05


def test_optimal_weights_by_probs():
    # Test hard classification lookup
    res = get_optimal_weights_for_regime("bull_quiet")
    assert res.is_success
    weights = res.unwrap()
    # Check that tail_risk has its default normalized weight
    assert weights["tail_risk"] > 0.0

    # Test soft probability lookup: uniform mix
    probs = {"bull_quiet": 0.5, "bear_volatile": 0.5}
    res = get_optimal_weights_for_regime("bull_quiet", regime_probs=probs)
    assert res.is_success
    mixed_weights = res.unwrap()
    # Sum should equal 1.0
    assert sum(mixed_weights.values()) == pytest.approx(1.0)


def test_blend_meta_with_engines():
    meta_signal = {"signal": 0.8, "confidence": 0.9}
    engine_signal = {"signal": -0.2, "confidence": 0.6}

    # Test blending under bull_quiet regime with high certainty
    probs = {"bull_quiet": 1.0}
    res = blend_meta_with_engines(meta_signal, engine_signal, "bull_quiet", regime_probs=probs)
    assert res.is_success
    blended = res.unwrap()

    assert blended["regime_certainty"] == 1.0
    assert blended["meta_weight"] == 0.60
    assert blended["engine_weight"] == 0.40

    # Expected signal: 0.60 * 0.8 + 0.40 * -0.2 = 0.48 - 0.08 = 0.40
    assert blended["signal"] == pytest.approx(0.40)
    # Expected confidence: 0.60 * 0.9 + 0.40 * 0.6 = 0.54 + 0.24 = 0.78
    assert blended["confidence"] == pytest.approx(0.78)


def test_update_weights_from_performance_pure():
    reputation = {
        "tail_risk": {"bull_quiet": 1.0, "bear_volatile": 1.0},
        "gamma_flip": {"bull_quiet": 1.0},
    }

    # Pure update: acc = 0.50 -> target = 1.0 -> no shift in mult (since old mult was 1.0)
    res = update_weights_from_performance("tail_risk", 0.50, "bull_quiet", reputation)
    assert res.is_success
    new_rep = res.unwrap()

    # Verify new dict was created and original was not mutated
    assert new_rep is not reputation
    assert new_rep["tail_risk"]["bull_quiet"] == 1.0

    # acc = 0.80 -> target = 1.30 -> shift: 0.8 * 1.0 + 0.2 * 1.3 = 1.06
    res2 = update_weights_from_performance("tail_risk", 0.80, "bull_quiet", reputation)
    assert res2.is_success
    new_rep2 = res2.unwrap()
    assert new_rep2["tail_risk"]["bull_quiet"] == pytest.approx(1.06)

    # acc = 0.00 -> target = 0.50 -> shift: 0.8 * 1.0 + 0.2 * 0.5 = 0.90
    res3 = update_weights_from_performance("tail_risk", 0.00, "bull_quiet", reputation)
    assert res3.is_success
    assert res3.unwrap()["tail_risk"]["bull_quiet"] == pytest.approx(0.90)
