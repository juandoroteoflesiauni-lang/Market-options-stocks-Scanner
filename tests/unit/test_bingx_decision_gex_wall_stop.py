"""Integration tests for Motor ④ — GEX Wall Stop wired into decide()."""

from __future__ import annotations

from typing import Any

from backend.services.bingx_decision_engine import (
    REASON_GEX_WALL_INVALIDATION,
    REASON_GEX_WALL_STOP_ACTIVE,
    decide,
)


class _FakeOptions:
    def __init__(self, metrics: dict[str, Any] | None) -> None:
        self.status: str = "available"
        self.quality_score: float = 0.8
        self.metrics: dict[str, Any] | None = metrics
        self.options_combiner: dict[str, Any] | None = None
        self.predictive_report: object | None = None


class _FakeTechnical:
    def __init__(self) -> None:
        # Empty-but-available payload → consensus runs, votes neutral.
        self.venue_technical: dict[str, Any] | None = {"status": "available", "payload": {}}
        self.status: str = "unavailable"
        self.quality_score: float | None = None


class _FakePredictive:
    def __init__(self, bias: str) -> None:
        self.status: str = "available"
        self.signal: dict[str, Any] = {
            "directional_bias": bias,
            "confidence": 0.7,
            "quality_score": 0.7,
        }
        self.quality_score: float = 0.7


class _FakeVenue:
    def __init__(self) -> None:
        self.status: str = "available"
        self.klines: list[dict[str, Any]] = [{"close": 100.0}]
        self.venue_ta: dict[str, Any] | None = {"bars_count": 50}


class _FakeAnalysis:
    def __init__(self, *, bias: str, metrics: dict[str, Any] | None) -> None:
        self.venue_symbol = "AAPL-USDT"
        self.underlying_symbol = "AAPL"
        self.market_type = "stock_perp"
        self.venue = _FakeVenue()
        self.technical = _FakeTechnical()
        self.options = _FakeOptions(metrics)
        self.predictive = _FakePredictive(bias)
        self.l2 = type("L2", (), {"status": "unavailable", "quality_score": None})()
        self.exchange_derivatives = type(
            "Ex", (), {"status": "unavailable", "quality_score": None}
        )()
        self.underlying = type("Und", (), {"quote": None, "ohlcv_status": "unavailable"})()
        self.avwap_hybrid_signals: dict[str, Any] | None = None


def _metrics(**inner: Any) -> dict[str, Any]:
    return {"metrics": dict(inner)}


def test_decide_long_near_call_wall_sizes_down() -> None:
    # ARRANGE — LONG with call wall 1% above spot (proximity hit, positive GEX).
    analysis = _FakeAnalysis(
        bias="LONG",
        metrics=_metrics(spot=100.0, call_wall=101.0, net_gex_total=500_000.0),
    )
    # ACT
    decision = decide(analysis, mode="dry_run")  # type: ignore[arg-type]
    # ASSERT
    assert decision.decision == "SIZE_DOWN"
    assert decision.direction == "LONG"
    assert REASON_GEX_WALL_STOP_ACTIVE in decision.reason_codes
    assert decision.gex_wall_stop_price is not None
    assert decision.sizing_multiplier < 1.0


def test_decide_long_breached_call_wall_blocks() -> None:
    # ARRANGE — spot already above the call wall → no upside room.
    analysis = _FakeAnalysis(
        bias="LONG",
        metrics=_metrics(spot=102.0, call_wall=101.0, net_gex_total=100.0),
    )
    # ACT
    decision = decide(analysis, mode="dry_run")  # type: ignore[arg-type]
    # ASSERT
    assert decision.decision == "BLOCK"
    assert decision.direction == "FLAT"
    assert REASON_GEX_WALL_INVALIDATION in decision.reason_codes


def test_decide_flat_direction_wall_does_not_invalidate() -> None:
    # ARRANGE — same breached geometry but no directional conviction.
    analysis = _FakeAnalysis(
        bias="NEUTRAL",
        metrics=_metrics(spot=102.0, call_wall=101.0, net_gex_total=100.0),
    )
    # ACT
    decision = decide(analysis, mode="dry_run")  # type: ignore[arg-type]
    # ASSERT — wall stop never invalidates a non-directional candidate.
    assert REASON_GEX_WALL_INVALIDATION not in decision.reason_codes
    assert REASON_GEX_WALL_STOP_ACTIVE not in decision.reason_codes


def test_decide_long_far_wall_no_gex_reason() -> None:
    # ARRANGE — call wall far away → wall stop inactive.
    analysis = _FakeAnalysis(
        bias="LONG",
        metrics=_metrics(spot=100.0, call_wall=110.0, net_gex_total=500_000.0),
    )
    # ACT
    decision = decide(analysis, mode="dry_run")  # type: ignore[arg-type]
    # ASSERT
    assert REASON_GEX_WALL_STOP_ACTIVE not in decision.reason_codes
    assert REASON_GEX_WALL_INVALIDATION not in decision.reason_codes
    assert decision.gex_wall_stop_price is None
