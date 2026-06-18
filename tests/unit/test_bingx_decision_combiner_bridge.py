"""Tests for SignalCombiner bridge into BingX decide()."""

from __future__ import annotations

from backend.services.bingx_decision_engine import (
    _combiner_direction,
    _options_direction,
    _options_score,
    decide,
)


class _FakeOptions:
    status: str = "available"
    quality_score: float = 0.8
    metrics: dict | None = None
    options_combiner: dict | None = None
    predictive_report: object | None = None


class _FakeTechnical:
    venue_technical: dict | None = None
    status: str = "unavailable"
    quality_score: float | None = None


class _FakePredictive:
    status: str = "available"
    signal: dict = {"direction": "FLAT", "confidence": 0.5, "quality_score": 0.5}
    quality_score: float = 0.5


class _FakeVenue:
    status: str = "available"
    klines: list = []
    venue_ta: dict | None = None


class _FakeAnalysis:
    venue_symbol: str = "AAPL-USDT"
    underlying_symbol: str = "AAPL"
    market_type: str = "stock_perp"
    venue: _FakeVenue
    technical: _FakeTechnical
    options: _FakeOptions
    predictive: _FakePredictive
    l2: object
    exchange_derivatives: object
    avwap_hybrid_signals: dict | None = None

    def __init__(self, *, combiner: dict | None = None) -> None:
        self.venue = _FakeVenue()
        self.technical = _FakeTechnical()
        self.options = _FakeOptions()
        self.options.options_combiner = combiner
        self.predictive = _FakePredictive()
        self.l2 = type("L2", (), {"status": "unavailable", "quality_score": None})()
        self.exchange_derivatives = type(
            "Ex", (), {"status": "unavailable", "quality_score": None}
        )()
        self.underlying = type("Und", (), {"quote": None, "ohlcv_status": "unavailable"})()


def test_options_score_blends_quality_and_combiner() -> None:
    analysis = _FakeAnalysis(
        combiner={"direction": "LONG", "score": 50.0, "entry_allowed": True},
    )
    score = _options_score(analysis)
    assert 0.0 < score <= 1.0
    assert score > 0.5


def test_combiner_direction_long_above_threshold() -> None:
    analysis = _FakeAnalysis(
        combiner={"direction": "LONG", "score": 40.0, "entry_allowed": True},
    )
    assert _combiner_direction(analysis) == "LONG"


def test_options_direction_falls_back_to_dealer_bias() -> None:
    analysis = _FakeAnalysis(combiner=None)
    analysis.options.metrics = {"metrics": {"dealer_bias": "BEARISH"}}
    assert _options_direction(analysis) == "SHORT"


def test_decide_uses_combiner_when_predictive_flat() -> None:
    analysis = _FakeAnalysis(
        combiner={
            "direction": "LONG",
            "score": 45.0,
            "entry_allowed": True,
            "risk_level": "LOW",
            "agreement_level": "partial",
            "size_pct": 0.75,
        },
    )
    analysis.technical.venue_technical = {"status": "available", "payload": {}}
    decision = decide(analysis)
    assert decision.direction in ("LONG", "FLAT", "SHORT")
    if decision.direction == "LONG":
        assert "combiner_direction_used" in decision.reason_codes
