import pytest
from pydantic import ValidationError

from backend.quant_engine.engines.predictive.sentiment import (
    SentimentAnalysisEngine,
    SocialMetricsInput,
)


def test_sentiment_analysis_news_bullish():
    engine = SentimentAnalysisEngine()

    news = ["Company upgrade by major analysts", "Easing inflation and buyback program announced"]
    res = engine.analyze(news, {"fear_greed": 75})

    assert res.is_success
    result = res.unwrap()
    assert result.consensus == "BULLISH"
    assert result.score > 0.0
    assert result.news_count == 2
    assert "upgrade" in result.top_themes
    assert "buyback" in result.top_themes


def test_sentiment_analysis_news_bearish():
    engine = SentimentAnalysisEngine()

    news = ["Company downgrade due to risks", "Recession fears tightening margins"]
    res = engine.analyze(news, {"fear_greed": 20})

    assert res.is_success
    result = res.unwrap()
    assert result.consensus == "BEARISH"
    assert result.score < 0.0
    assert result.news_count == 2
    assert "downgrade" in result.top_themes
    assert "recession" in result.top_themes


def test_sentiment_analysis_news_empty_failure():
    engine = SentimentAnalysisEngine()
    res = engine.analyze([])
    assert res.is_failure
    assert "empty" in res.reason


def test_sentiment_analysis_news_invalid_indicators():
    engine = SentimentAnalysisEngine()
    news = ["Some basic neutral market text"]

    res_invalid_type = engine.analyze(news, {"fear_greed": "very_greedy"})
    assert res_invalid_type.is_failure

    res_out_of_bounds = engine.analyze(news, {"fear_greed": 150})
    assert res_out_of_bounds.is_failure


def test_sentiment_analysis_social_valid():
    engine = SentimentAnalysisEngine()
    social_data = SocialMetricsInput(
        twitter_posts=600,
        stocktwits_posts=400,
        twitter_sentiment=0.8,
        stocktwits_sentiment=0.6,
    )

    res = engine.analyze_social(social_data)
    assert res.is_success

    result = res.unwrap()
    assert result.news_count == 1000  # total posts
    assert result.sentiment_score == 0.7  # avg sentiment
    # score = 0.7 * 2.0 - 1.0 = 0.4
    assert result.score == pytest.approx(0.4)
    assert result.consensus == "BULLISH"
    assert result.confidence == 0.8  # total_buzz > 500
    assert result.is_hot is False  # total_buzz not > 1000


def test_sentiment_analysis_social_invalid():
    # Negative posts
    with pytest.raises(ValidationError):
        SocialMetricsInput(
            twitter_posts=-10,
            stocktwits_posts=10,
            twitter_sentiment=0.5,
            stocktwits_sentiment=0.5,
        )

    # Out of range sentiment
    with pytest.raises(ValidationError):
        SocialMetricsInput(
            twitter_posts=10,
            stocktwits_posts=10,
            twitter_sentiment=1.5,
            stocktwits_sentiment=0.5,
        )


def test_sentiment_analysis_reputation_normal():
    engine = SentimentAnalysisEngine()

    texts = [
        "This is an amazing and excellent trading day!",
        "Very poor execution, terribly disappointed with the software.",
    ]
    res = engine.analyze_reputation(texts)
    assert res.is_success

    result = res.unwrap()
    assert result.news_count == 2
    assert len(result.top_themes) == 0  # No sarcasm or crisis keyword detected


def test_sentiment_analysis_reputation_sarcasm_and_crisis():
    engine = SentimentAnalysisEngine()

    texts = [
        "Claro si, es genial la caida del mercado jajaja",
        "Esto es una verguenza y un fraude total boicot",
    ]
    res = engine.analyze_reputation(texts)
    assert res.is_success

    result = res.unwrap()
    assert "sarcastic_buzz" in result.top_themes
    assert "crisis_keyword" in result.top_themes


def test_sentiment_analysis_reputation_empty_failure():
    engine = SentimentAnalysisEngine()
    res = engine.analyze_reputation([])
    assert res.is_failure
    assert "empty" in res.reason
