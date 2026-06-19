"""Integration tests for Motor ⑬ — Bayesian Kelly surfaced by decide()."""

from __future__ import annotations

from typing import Any

import backend.services.bingx_decision_engine as engine
from backend.services.bingx_decision_engine import decide
from backend.services.calibration.bayesian_kelly_sizer import BayesianKellyDecideResult


class _FakeOptions:
    def __init__(self, metrics: dict[str, Any] | None) -> None:
        self.status = "available"
        self.quality_score = 0.8
        self.metrics = metrics
        self.options_combiner: dict[str, Any] | None = None
        self.predictive_report: object | None = None


class _FakeTechnical:
    def __init__(self) -> None:
        self.venue_technical: dict[str, Any] | None = {"status": "available", "payload": {}}
        self.status = "unavailable"
        self.quality_score: float | None = None


class _FakePredictive:
    def __init__(self, bias: str) -> None:
        self.status = "available"
        self.signal = {"directional_bias": bias, "confidence": 0.7, "quality_score": 0.7}
        self.quality_score = 0.7


class _FakeVenue:
    def __init__(self) -> None:
        self.status = "available"
        self.klines = [{"close": 100.0}]
        self.venue_ta = {"bars_count": 50}


class _FakeAnalysis:
    def __init__(self, *, bias: str = "LONG") -> None:
        self.venue_symbol = "AAPL-USDT"
        self.underlying_symbol = "AAPL"
        self.market_type = "stock_perp"
        self.venue = _FakeVenue()
        self.technical = _FakeTechnical()
        # No directional wall → GEX wall stop neutral, isolates Bayesian path.
        self.options = _FakeOptions({"metrics": {"spot": 100.0, "net_gex_total": 1.0}})
        self.predictive = _FakePredictive(bias)
        self.l2 = type("L2", (), {"status": "unavailable", "quality_score": None})()
        self.exchange_derivatives = type(
            "Ex", (), {"status": "unavailable", "quality_score": None}
        )()
        self.underlying = type("Und", (), {"quote": None, "ohlcv_status": "unavailable"})()
        self.avwap_hybrid_signals: dict[str, Any] | None = None


def test_decide_exposes_bayesian_kelly_pct_when_active(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        engine,
        "bayesian_kelly_for_decide",
        lambda **_: BayesianKellyDecideResult(multiplier=0.675, fraction=0.5, active=True),
    )
    decision = decide(_FakeAnalysis(), mode="dry_run")  # type: ignore[arg-type]
    assert decision.bayesian_kelly_pct == 0.5
    assert "bayesian_kelly_size_down" in decision.reason_codes


def test_decide_bayesian_pct_none_when_inactive(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        engine,
        "bayesian_kelly_for_decide",
        lambda **_: BayesianKellyDecideResult(multiplier=1.0, fraction=None, active=False),
    )
    decision = decide(_FakeAnalysis(), mode="dry_run")  # type: ignore[arg-type]
    assert decision.bayesian_kelly_pct is None
    assert "bayesian_kelly_size_down" not in decision.reason_codes
