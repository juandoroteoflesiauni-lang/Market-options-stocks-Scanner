"""Integration tests for Motor ⑭ — dark pool wired into analysis and decide()."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from backend.models.dark_pool_snapshot import DarkPoolSnapshot
from backend.services.bingx_dark_pool_bridge import build_dark_pool_block
from backend.services.bingx_decision_engine import (
    REASON_DARK_POOL_CONFIRMS,
    REASON_DARK_POOL_CONTRADICTS,
    decide,
)
from backend.services.bingx_risk_sizing_v2 import compute_risk_sizing_v2


def _snapshot(bias: str, *, confidence: float = 0.8, notional: str = "2000000") -> DarkPoolSnapshot:
    return DarkPoolSnapshot(
        symbol="AAPL",
        print_count_1h=16,
        net_notional_usd=Decimal(notional),
        bias=bias,  # type: ignore[arg-type]
        confidence=confidence,
        fetched_at=datetime.now(UTC),
        source="unusual_whales",
    )


# ── Bridge ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_dark_pool_block_available() -> None:
    async def fn(_symbol: str) -> DarkPoolSnapshot:
        return _snapshot("BULLISH")

    block = await build_dark_pool_block("AAPL", dark_pool_fn=fn)
    assert block.status == "available"
    assert block.bias == "BULLISH"
    assert block.net_notional_usd == "2000000"
    assert block.snapshot is not None


@pytest.mark.asyncio
async def test_build_dark_pool_block_no_fn() -> None:
    block = await build_dark_pool_block("AAPL", dark_pool_fn=None)
    assert block.status == "unavailable"
    assert block.reason == "no_dark_pool_fn"


@pytest.mark.asyncio
async def test_build_dark_pool_block_fetch_failure() -> None:
    async def fn(_symbol: str) -> DarkPoolSnapshot:
        raise RuntimeError("boom")

    block = await build_dark_pool_block("AAPL", dark_pool_fn=fn)
    assert block.status == "unavailable"
    assert block.reason == "dark_pool_fetch_failed"


# ── risk_sizing_v2 dark pool multiplier ──────────────────────────────────────


class _DP:
    def __init__(self, status: str, bias: str, confidence: float) -> None:
        self.status = status
        self.bias = bias
        self.confidence = confidence


class _Opts:
    def __init__(self, metrics: dict[str, Any] | None) -> None:
        self.metrics = metrics


class _Analysis:
    def __init__(self, dp: _DP) -> None:
        self.venue_symbol = "AAPL-USDT"
        self.options = _Opts(
            {"metrics": {"iv_rank_hv_rolling": 0.25, "vrp": 0.08, "net_gex_total": 1.0}}
        )
        self.dark_pool = dp


def test_risk_sizing_dark_pool_bearish_penalty_on_long() -> None:
    analysis = _Analysis(_DP("available", "BEARISH", 0.8))
    result = compute_risk_sizing_v2(analysis, direction="LONG")  # type: ignore[arg-type]
    assert result["dark_pool_mult"] == 0.75


def test_risk_sizing_dark_pool_bullish_bonus_on_long() -> None:
    analysis = _Analysis(_DP("available", "BULLISH", 0.8))
    result = compute_risk_sizing_v2(analysis, direction="LONG")  # type: ignore[arg-type]
    assert result["dark_pool_mult"] == pytest.approx(1.12)  # 1 + 0.8*0.15


def test_risk_sizing_dark_pool_low_confidence_ignored() -> None:
    analysis = _Analysis(_DP("available", "BEARISH", 0.10))
    result = compute_risk_sizing_v2(analysis, direction="LONG")  # type: ignore[arg-type]
    assert result["dark_pool_mult"] == 1.0


def test_risk_sizing_dark_pool_unavailable_neutral() -> None:
    analysis = _Analysis(_DP("unavailable", "BULLISH", 0.9))
    result = compute_risk_sizing_v2(analysis, direction="LONG")  # type: ignore[arg-type]
    assert result["dark_pool_mult"] == 1.0


# ── decide() reason codes ─────────────────────────────────────────────────────


class _FakeOptions:
    def __init__(self) -> None:
        self.status = "available"
        self.quality_score = 0.8
        self.metrics = {"metrics": {"spot": 100.0, "net_gex_total": 1.0}}
        self.options_combiner: dict[str, Any] | None = None
        self.predictive_report: object | None = None


class _FakeTechnical:
    def __init__(self) -> None:
        self.venue_technical: dict[str, Any] | None = {"status": "available", "payload": {}}
        self.status = "unavailable"
        self.quality_score: float | None = None


class _FakePredictive:
    def __init__(self) -> None:
        self.status = "available"
        self.signal = {"directional_bias": "LONG", "confidence": 0.7, "quality_score": 0.7}
        self.quality_score = 0.7


class _FakeVenue:
    def __init__(self) -> None:
        self.status = "available"
        self.klines = [{"close": 100.0}]
        self.venue_ta = {"bars_count": 50}


class _FakeAnalysis:
    def __init__(self, dark_pool: Any) -> None:
        self.venue_symbol = "AAPL-USDT"
        self.underlying_symbol = "AAPL"
        self.market_type = "stock_perp"
        self.venue = _FakeVenue()
        self.technical = _FakeTechnical()
        self.options = _FakeOptions()
        self.predictive = _FakePredictive()
        self.l2 = type("L2", (), {"status": "unavailable", "quality_score": None})()
        self.exchange_derivatives = type(
            "Ex", (), {"status": "unavailable", "quality_score": None}
        )()
        self.underlying = type("Und", (), {"quote": None, "ohlcv_status": "unavailable"})()
        self.avwap_hybrid_signals: dict[str, Any] | None = None
        self.dark_pool = dark_pool


def test_decide_dark_pool_contradicts_long() -> None:
    decision = decide(_FakeAnalysis(_DP("available", "BEARISH", 0.8)), mode="dry_run")  # type: ignore[arg-type]
    assert REASON_DARK_POOL_CONTRADICTS in decision.reason_codes
    assert decision.sizing_multiplier < 1.0


def test_decide_dark_pool_confirms_long() -> None:
    decision = decide(_FakeAnalysis(_DP("available", "BULLISH", 0.8)), mode="dry_run")  # type: ignore[arg-type]
    assert REASON_DARK_POOL_CONFIRMS in decision.reason_codes


def test_decide_no_dark_pool_no_reason() -> None:
    decision = decide(_FakeAnalysis(_DP("unavailable", "NEUTRAL", 0.0)), mode="dry_run")  # type: ignore[arg-type]
    assert REASON_DARK_POOL_CONFIRMS not in decision.reason_codes
    assert REASON_DARK_POOL_CONTRADICTS not in decision.reason_codes
