from backend.models.result import Result
from backend.quant_engine.engines.predictive.fear_greed import (
    FearGreedEngine,
    FearGreedResult,
    MarketSentimentInput,
)


def test_fear_greed_engine_normal_state():
    engine = FearGreedEngine()

    # Neutral/balanced inputs
    sentiment_input = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,  # 0% above -> score 50
        nyse_highs_pct=8.5,  # (8.5 - 2) / 13 * 100 = 50
        vix_current=20.0,
        vix_ma50=20.0,  # ratio 1.0 -> 100 - (1.0 - 0.5) / 1.5 * 100 = 66.67
        put_call_ratio=1.0,  # 100 - (1.0 - 0.5) * 100 = 50
        credit_spread=500.0,  # 100 - (500 - 200) / 600 * 100 = 50
        gold_price=1900.0,
        gold_ma50=1900.0,  # ratio 1.0 -> 100 - (1.0 - 0.9) * 200 = 80
        usd_index=100.0,
        usd_ma50=100.0,  # ratio 1.0 -> 100 - (1.0 - 0.9) * 200 = 80
        event_risk_score=0.5,  # 100 * (1 - 0.5) = 50
    )

    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert isinstance(res, Result)
    assert res.is_success

    result = res.unwrap()
    assert isinstance(result, FearGreedResult)
    assert result.data_quality == "excellent"
    assert 50.0 <= result.score <= 65.0
    assert result.label in ["Neutral", "Greed"]
    assert "momentum" in result.factors
    assert "safe_haven" in result.factors
    assert "event_risk" in result.factors


def test_fear_greed_engine_missing_optionals():
    engine = FearGreedEngine()

    # No gold, no usd, no event_risk_score
    sentiment_input = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
    )

    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_success
    result = res.unwrap()

    # Missing 3 factors from inputs, so quality ratio is 5/7 = 0.71 (good)
    assert result.data_quality == "good"
    assert result.factors["safe_haven"] == 50.0
    assert result.factors["event_risk"] == 50.0


def test_fear_greed_engine_nan_values():
    engine = FearGreedEngine()

    sentiment_input = MarketSentimentInput(
        spx_price=float("nan"),
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_failure
    assert "contains NaN values" in res.reason


def test_fear_greed_engine_invalid_spx():
    engine = FearGreedEngine()

    sentiment_input = MarketSentimentInput(
        spx_price=-100.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_failure
    assert "S&P 500 price and 125-day MA must be positive" in res.reason


def test_fear_greed_engine_invalid_nyse():
    engine = FearGreedEngine()

    sentiment_input = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=-1.0,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_failure
    assert "NYSE highs percentage cannot be negative" in res.reason


def test_fear_greed_engine_invalid_vix():
    engine = FearGreedEngine()

    sentiment_input = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=0.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_failure
    assert "VIX price and 50-day MA must be positive" in res.reason


def test_fear_greed_engine_invalid_put_call():
    engine = FearGreedEngine()

    sentiment_input = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=-0.1,
        credit_spread=500.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_failure
    assert "Put/Call ratio must be positive" in res.reason


def test_fear_greed_engine_invalid_credit_spread():
    engine = FearGreedEngine()

    sentiment_input = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=-5.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_failure
    assert "Credit spread cannot be negative" in res.reason


def test_fear_greed_engine_invalid_gold_combo():
    engine = FearGreedEngine()

    # Gold price provided but no MA
    sentiment_input = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
        gold_price=1900.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_failure
    assert "Both gold_price and gold_ma50 must be provided" in res.reason


def test_fear_greed_engine_invalid_usd_combo():
    engine = FearGreedEngine()

    # USD MA provided but no price
    sentiment_input = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
        usd_ma50=100.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_failure
    assert "Both usd_index and usd_ma50 must be provided" in res.reason


def test_fear_greed_engine_invalid_event_risk():
    engine = FearGreedEngine()

    sentiment_input = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
        event_risk_score=1.5,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input)
    assert res.is_failure
    assert "event_risk_score must be between 0.0 and 1.0" in res.reason


def test_fear_greed_engine_safe_haven_options():
    engine = FearGreedEngine()

    # Case 1: Gold only
    sentiment_input_gold = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
        gold_price=1900.0,
        gold_ma50=1900.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input_gold)
    assert res.is_success
    assert "safe_haven" in res.unwrap().factors
    # quality is 6/7 = 0.857 -> excellent
    assert res.unwrap().data_quality == "excellent"

    # Case 2: USD only
    sentiment_input_usd = MarketSentimentInput(
        spx_price=4500.0,
        spx_ma125=4500.0,
        nyse_highs_pct=8.5,
        vix_current=20.0,
        vix_ma50=20.0,
        put_call_ratio=1.0,
        credit_spread=500.0,
        usd_index=100.0,
        usd_ma50=100.0,
    )
    res = engine.analyze(symbol="SPY", sentiment_input=sentiment_input_usd)
    assert res.is_success
    assert "safe_haven" in res.unwrap().factors
    assert res.unwrap().data_quality == "excellent"


def test_fear_greed_extreme_labels():
    engine = FearGreedEngine()

    # Extreme Greed setup
    greed_input = MarketSentimentInput(
        spx_price=5500.0,
        spx_ma125=4500.0,  # Momentum score high
        nyse_highs_pct=20.0,  # Strength score high (above 15%)
        vix_current=10.0,
        vix_ma50=30.0,  # Volatility ratio 0.33 -> score 100
        put_call_ratio=0.3,  # Put/Call score high
        credit_spread=100.0,  # Credit spread score high
        gold_price=1500.0,
        gold_ma50=1900.0,  # Gold ratio low (safe haven demand low)
        usd_index=80.0,
        usd_ma50=100.0,  # USD ratio low (safe haven demand low)
        event_risk_score=0.0,  # Event risk low -> score 100
    )
    res = engine.analyze(symbol="SPY", sentiment_input=greed_input)
    assert res.is_success
    assert res.unwrap().label == "Extreme Greed"
    assert res.unwrap().score >= 80.0

    # Extreme Fear setup
    fear_input = MarketSentimentInput(
        spx_price=3500.0,
        spx_ma125=4500.0,  # Momentum score low
        nyse_highs_pct=0.0,  # Strength score low
        vix_current=50.0,
        vix_ma50=20.0,  # Volatility ratio 2.5 -> score 0
        put_call_ratio=2.0,  # Put/Call score low
        credit_spread=1000.0,  # Credit spread score low
        gold_price=2300.0,
        gold_ma50=1900.0,  # Gold ratio high (safe haven demand high)
        usd_index=120.0,
        usd_ma50=100.0,  # USD ratio high (safe haven demand high)
        event_risk_score=1.0,  # Event risk high -> score 0
    )
    res = engine.analyze(symbol="SPY", sentiment_input=fear_input)
    assert res.is_success
    assert res.unwrap().label == "Extreme Fear"
    assert res.unwrap().score <= 20.0
