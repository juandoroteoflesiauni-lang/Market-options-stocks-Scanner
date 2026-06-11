from datetime import datetime, timedelta

import pytest

from backend.quant_engine.engines.predictive.catalyst_nlp import (
    CalendarInput,
    CatalystNLPEngine,
    NewsInput,
    SurpriseInput,
    TranscriptInput,
)


def test_catalyst_nlp_bullish():
    engine = CatalystNLPEngine()
    transcript = TranscriptInput(
        content="We had record performance, exceeding all goals. Outperform with robust growth."
    )
    news = [NewsInput(title="Strong results", text="Confident about momentum and new highs.")]

    res = engine.analyze(
        symbol="AAPL",
        transcript=transcript,
        news=news,
        calendar=[],
        surprises=[],
    )
    assert res.is_success
    profile = res.unwrap()
    assert profile.symbol == "AAPL"
    assert profile.tone == "BULLISH"
    assert profile.bullish_hits > 0
    assert profile.bearish_hits == 0
    assert profile.alarming_hits == 0
    assert profile.jump_intensity_adj == 1.0


def test_catalyst_nlp_bearish():
    engine = CatalystNLPEngine()
    transcript = TranscriptInput(
        content="Disappointing quarters due to headwind, margin compression, and macro concerns."
    )
    news = [NewsInput(title="Restructuring plan", text="Softening demand, reduce guidance.")]

    res = engine.analyze(
        symbol="AAPL",
        transcript=transcript,
        news=news,
        calendar=[],
        surprises=[],
    )
    assert res.is_success
    profile = res.unwrap()
    assert profile.tone == "BEARISH"
    assert profile.bearish_hits > 0
    assert profile.bullish_hits == 0


def test_catalyst_nlp_alarming():
    engine = CatalystNLPEngine()
    # Multiple alarming keyword hits
    transcript = TranscriptInput(
        content="Liquidity crisis. Debt covenant default risk. Restatement necessary."
    )
    news = [NewsInput(title="Audit fail", text="Material weakness in reports, going concern risk.")]

    res = engine.analyze(
        symbol="AAPL",
        transcript=transcript,
        news=news,
        calendar=[],
        surprises=[],
    )
    assert res.is_success
    profile = res.unwrap()
    assert profile.tone == "ALARMING"
    assert profile.event_risk_score >= 0.75
    assert profile.jump_intensity_adj == 1.6


def test_catalyst_nlp_proximity_risk():
    engine = CatalystNLPEngine()
    transcript = TranscriptInput(content="Neutral transcript content here.")

    # Earnings calendar scheduled in 5 days
    target_date = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    calendar = [CalendarInput(date=target_date)]

    res = engine.analyze(
        symbol="AAPL",
        transcript=transcript,
        news=[],
        calendar=calendar,
        surprises=[],
    )
    assert res.is_success
    profile = res.unwrap()
    assert len(profile.upcoming_catalysts) == 1
    assert profile.upcoming_catalysts[0].event_type == "EARNINGS"
    assert profile.upcoming_catalysts[0].days_until == 5
    # Proximity multiplier adjusts neutral jump intensity to 1.15
    assert profile.jump_intensity_adj == 1.15


def test_catalyst_nlp_eps_surprise():
    engine = CatalystNLPEngine()

    # actual=1.2, estimated=1.0 -> 20% beat
    # actual=0.8, estimated=1.0 -> -20% miss
    surprises = [
        SurpriseInput(actual=1.2, estimated=1.0),
        SurpriseInput(actual=0.8, estimated=1.0),
    ]

    res = engine.analyze(
        symbol="AAPL",
        transcript=None,
        news=[],
        calendar=[],
        surprises=surprises,
    )
    assert res.is_success
    profile = res.unwrap()
    assert profile.last_eps_surprise == pytest.approx(20.0)
    assert profile.avg_eps_surprise == pytest.approx(0.0)


def test_catalyst_nlp_empty_symbol():
    engine = CatalystNLPEngine()
    res = engine.analyze(
        symbol="",
        transcript=None,
        news=[],
        calendar=[],
        surprises=[],
    )
    assert res.is_failure
    assert "symbol" in res.reason
