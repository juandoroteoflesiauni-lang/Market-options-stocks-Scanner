from decimal import Decimal

from backend.services.global_context_engine import GlobalContextEngine


class MockSnapshot:
    def __init__(self, daily_change_pct: float):
        self.daily_change_pct = daily_change_pct


def test_global_context_engine_invalid() -> None:
    engine = GlobalContextEngine()
    snapshot = engine.evaluate({"vix": 0.0})
    assert not snapshot.is_valid
    assert snapshot.market_regime == "NORMAL"
    assert snapshot.regime_factor == Decimal("1.0")


def test_global_context_engine_meltdown() -> None:
    engine = GlobalContextEngine()
    context = {
        "vix": 40.0,
        "spy": MockSnapshot(-2.0),
        "qqq": MockSnapshot(-3.0),
    }
    snapshot = engine.evaluate(context)
    assert snapshot.is_valid
    assert snapshot.market_regime == "MELTDOWN"
    assert snapshot.regime_factor == Decimal("0.0")


def test_global_context_engine_risk_off() -> None:
    engine = GlobalContextEngine()
    context = {
        "vix": 26.0,
        "spy": MockSnapshot(-1.0),
        "qqq": MockSnapshot(-1.5),
    }
    snapshot = engine.evaluate(context)
    assert snapshot.is_valid
    assert snapshot.market_regime == "RISK_OFF"
    assert snapshot.regime_factor == Decimal("0.5")


def test_global_context_engine_bull() -> None:
    engine = GlobalContextEngine()
    context = {
        "vix": 15.0,
        "spy": MockSnapshot(1.0),
        "qqq": MockSnapshot(1.5),
    }
    snapshot = engine.evaluate(context)
    assert snapshot.is_valid
    assert snapshot.market_regime == "BULL"
    assert snapshot.regime_factor == Decimal("1.2")


def test_global_context_engine_fear_greed_and_ratios() -> None:
    engine = GlobalContextEngine()
    context = {
        "vix": 15.0,
        "spy": MockSnapshot(1.0),
        "qqq": MockSnapshot(1.5),
        "eem": MockSnapshot(0.0),
        "iwm": MockSnapshot(2.5),
        "fear_greed_index": 20,  # Extreme fear
    }
    snapshot = engine.evaluate(context)
    assert snapshot.is_valid
    assert snapshot.market_regime == "BULL"

    # Base bull is 1.2, extreme fear multiplies by 0.8 => 0.96
    assert snapshot.regime_factor == Decimal("0.96")
    assert snapshot.macro_conflict_score >= Decimal("0.6")

    # SPY vs EEM (1.0 vs 0.0) -> SPY_OUTPERFORM
    assert snapshot.spy_eem_trend == "SPY_OUTPERFORM"

    # QQQ vs IWM (1.5 vs 2.5) -> IWM_OUTPERFORM
    assert snapshot.qqq_iwm_trend == "IWM_OUTPERFORM"
