"""Tests for audit hook functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.audit.audit_complex_store import AuditComplexStore
from backend.audit.hooks import (
    audit_api_call,
    audit_bingx_decision,
    audit_decision_snapshot,
    audit_decision_snapshot_sync,
    audit_error,
    audit_scanner_result,
    extract_bingx_decision_data,
    extract_bingx_indicators,
    extract_bingx_market_data,
    extract_bingx_signals,
    extract_scanner_indicators,
)

pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def store() -> AuditComplexStore:
    return AuditComplexStore(":memory:")


@pytest.fixture(autouse=True)
def _reset_hooks_store() -> None:
    """Reset the hooks module singleton store before each test."""
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = None


# ═══════════════════════════════════════════════════════════════════════════════
# audit_decision_snapshot
# ═══════════════════════════════════════════════════════════════════════════════


async def test_audit_decision_snapshot_returns_id(store: AuditComplexStore) -> None:

    # Point hooks at our store
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    result = await audit_decision_snapshot(
        module="test_mod",
        symbol="BTC-USDT",
        indicators={"rsi": 55.0},
        signals={"direction": "LONG"},
    )
    assert result is not None
    assert result.startswith("snap_")


async def test_audit_decision_snapshot_persists(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    await audit_decision_snapshot(
        module="test_mod",
        symbol="ETH-USDT",
        indicators={"macd": 1.2},
    )
    snaps = store.list_process_snapshots(module="test_mod")
    assert len(snaps) == 1
    assert snaps[0]["symbol"] == "ETH-USDT"


async def test_audit_decision_snapshot_never_raises(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    # Missing required `symbol` — should not raise (caught internally)
    result = await audit_decision_snapshot(
        module="test",
        symbol="BTC-USDT",
        indicators={},
    )
    assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# audit_decision_snapshot_sync
# ═══════════════════════════════════════════════════════════════════════════════


def test_audit_decision_snapshot_sync_returns_id(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    result = audit_decision_snapshot_sync(
        module="sync_mod",
        symbol="SOL-USDT",
        indicators={"adx": 30.0},
    )
    assert result is not None
    assert result.startswith("snap_")


def test_audit_decision_snapshot_sync_persists(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    audit_decision_snapshot_sync(
        module="sync_mod",
        symbol="SOL-USDT",
        indicators={"adx": 30.0},
    )
    assert store.count_process_snapshots() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# audit_api_call
# ═══════════════════════════════════════════════════════════════════════════════


async def test_audit_api_call_returns_id(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    result = await audit_api_call(
        module="scanner",
        provider="fmp",
        endpoint="/v3/quote",
        status="success",
        duration_ms=100.0,
        estimated_cost=0.001,
    )
    assert result is not None
    assert result.startswith("call_")


async def test_audit_api_call_persists(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    await audit_api_call(
        module="scanner",
        provider="fmp",
        endpoint="/v3/quote",
        status="error",
        duration_ms=5000.0,
        estimated_cost=0.01,
        error_message="timeout",
        request_context={"retry": 2},
    )
    calls = store.list_api_calls()
    assert len(calls) == 1
    assert calls[0]["status"] == "error"
    assert calls[0]["error_message"] == "timeout"


async def test_audit_api_call_never_raises(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    result = await audit_api_call(
        module="scanner",
        provider="fmp",
        endpoint="/test",
        status="success",
        duration_ms=0.0,
        estimated_cost=0.0,
    )
    assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# audit_error
# ═══════════════════════════════════════════════════════════════════════════════


async def test_audit_error_returns_id(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    result = await audit_error(
        module="scanner",
        error_type="TIMEOUT",
        message="request timed out",
        severity="error",
    )
    assert result is not None
    assert result.startswith("err_")


async def test_audit_error_persists(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    await audit_error(
        module="bingx",
        error_type="EXECUTION_FAILURE",
        message="order rejected",
        severity="critical",
        context={"order_id": "123"},
    )
    errors = store.list_errors()
    assert len(errors) == 1
    assert errors[0]["severity"] == "critical"


async def test_audit_error_with_exception_captures_stack(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    try:
        _ = 1 / 0
    except ZeroDivisionError as exc:
        await audit_error(
            module="engine",
            error_type="MATH_ERROR",
            message="division by zero",
            exc=exc,
        )
    errors = store.list_errors()
    assert len(errors) == 1
    assert "ZeroDivisionError" in errors[0]["stack_trace"]


async def test_audit_error_never_raises(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    result = await audit_error(
        module="test",
        error_type="TEST",
        message="test",
    )
    assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# audit_bingx_decision
# ═══════════════════════════════════════════════════════════════════════════════


class MockTechnical:
    def __init__(self) -> None:
        self.rsi = 60.0
        self.macd = 1.5
        self.macd_signal = 1.2
        self.vwap = 50000.0
        self.consensus = "BULLISH"


class MockPredictive:
    def __init__(self) -> None:
        self.direction = "LONG"
        self.confidence = 0.8
        self.probability = 0.75


class MockOptions:
    def __init__(self) -> None:
        self.direction = "CALL"
        self.iv_rank = 0.45
        self.put_call_ratio = 1.2


class MockL2:
    def __init__(self) -> None:
        self.quality_score = 0.9
        self.spread_pct = 0.01
        self.bid_depth = 100.0
        self.ask_depth = 80.0
        self.best_bid = 49900.0
        self.best_ask = 50100.0


class MockVenue:
    def __init__(self) -> None:
        self.price = 50000.0
        self.volume_24h = 1_000_000.0
        self.change_24h_pct = 2.5
        self.market_type = "crypto_standard"


class MockAnalysis:
    def __init__(self) -> None:
        self.venue_symbol = "BTC-USDT"
        self.underlying_symbol = "BTC"
        self.technical = MockTechnical()
        self.predictive = MockPredictive()
        self.options = MockOptions()
        self.l2 = MockL2()
        self.venue = MockVenue()


class MockDecision:
    def __init__(self) -> None:
        self.symbol = "BTC-USDT"
        self.decision = "ENTER"
        self.direction = "LONG"
        self.confidence = 0.85
        self.score_total = 78.0
        self.reason_codes = ["TECH_BULLISH", "PREDICTIVE_ALIGNED"]
        self.module_scores = MockModuleScores()


class MockModuleScores:
    def __init__(self) -> None:
        self.venue = 20.0
        self.technical = 25.0
        self.options = 10.0
        self.predictive = 15.0
        self.l2 = 5.0
        self.risk = 3.0


async def test_audit_bingx_decision_returns_id(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    analysis = MockAnalysis()
    decision = MockDecision()
    result = await audit_bingx_decision(analysis=analysis, decision=decision)
    assert result is not None
    assert result.startswith("snap_")


async def test_audit_bingx_decision_persists_full_snapshot(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    analysis = MockAnalysis()
    decision = MockDecision()
    await audit_bingx_decision(analysis=analysis, decision=decision)
    snaps = store.list_process_snapshots(module="bingx")
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap["symbol"] == "BTC-USDT"
    indicators = snap["indicators"]
    assert indicators["rsi"] == 60.0
    assert indicators["macd"] == 1.5


async def test_audit_bingx_decision_without_optional_attrs(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    result = await audit_bingx_decision(analysis=MagicMock(), decision=None)
    assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# audit_scanner_result
# ═══════════════════════════════════════════════════════════════════════════════


async def test_audit_scanner_result_returns_id(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    row = {"rsi": 55.0, "macd": 0.5}
    result = await audit_scanner_result(symbol="BTC-USDT", row=row, phase="A", score=0.85)
    assert result is not None
    assert result.startswith("snap_")


async def test_audit_scanner_result_persists(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    row = {"rsi": 55.0, "macd": 0.5, "composite_score": 0.9}
    await audit_scanner_result(symbol="ETH-USDT", row=row, phase="B", score=0.75)
    snaps = store.list_process_snapshots(module="scanner")
    assert len(snaps) == 1
    indicators = snaps[0]["indicators"]
    assert indicators["rsi"] == 55.0
    assert indicators["phase"] == "B"
    decisions = snaps[0]["decisions"]
    assert decisions["phase"] == "B"
    assert decisions["score"] == 0.75


async def test_audit_scanner_result_with_object_row(store: AuditComplexStore) -> None:
    import backend.audit.hooks as hooks_mod

    hooks_mod._store = store
    row = MagicMock()
    row.rsi = 70.0
    row.macd = 2.0
    row.vwap = 100.0
    row.atr = 5.0
    row.adx = 35.0
    row.phase_a_score = 0.9
    row.phase_b_score = 0.7
    row.composite_score = 0.8
    result = await audit_scanner_result(symbol="SOL-USDT", row=row, phase="A", score=0.9)
    assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# extract_bingx_indicators
# ═══════════════════════════════════════════════════════════════════════════════


def test_extract_bingx_indicators_with_full_analysis() -> None:
    analysis = MockAnalysis()
    indicators = extract_bingx_indicators(analysis)
    assert indicators["rsi"] == 60.0
    assert indicators["macd"] == 1.5
    assert indicators["macd_signal"] == 1.2
    assert indicators["vwap"] == 50000.0
    assert indicators["technical_consensus"] == "BULLISH"


def test_extract_bingx_indicators_empty_when_no_technical() -> None:
    analysis = MagicMock()
    analysis.technical = None
    indicators = extract_bingx_indicators(analysis)
    assert indicators == {}


# ═══════════════════════════════════════════════════════════════════════════════
# extract_bingx_signals
# ═══════════════════════════════════════════════════════════════════════════════


def test_extract_bingx_signals_full() -> None:
    analysis = MockAnalysis()
    decision = MockDecision()
    signals = extract_bingx_signals(analysis, decision)
    assert signals["predictive_direction"] == "LONG"
    assert signals["options_direction"] == "CALL"
    assert signals["l2_quality_score"] == 0.9
    assert signals["decision_status"] == "ENTER"
    assert signals["decision_score_total"] == 78.0


def test_extract_bingx_signals_graceful_with_none() -> None:
    analysis = MagicMock()
    analysis.predictive = None
    analysis.options = None
    analysis.l2 = None
    signals = extract_bingx_signals(analysis, None)
    assert signals == {}


# ═══════════════════════════════════════════════════════════════════════════════
# extract_bingx_decision_data
# ═══════════════════════════════════════════════════════════════════════════════


def test_extract_bingx_decision_data_full() -> None:
    decision = MockDecision()
    data = extract_bingx_decision_data(decision)
    assert data["decision"] == "ENTER"
    assert data["direction"] == "LONG"
    assert data["score_total"] == 78.0
    assert data["reason_codes"] == ["TECH_BULLISH", "PREDICTIVE_ALIGNED"]
    assert data["module_scores"]["technical"] == 25.0


def test_extract_bingx_decision_data_none() -> None:
    assert extract_bingx_decision_data(None) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# extract_bingx_market_data
# ═══════════════════════════════════════════════════════════════════════════════


def test_extract_bingx_market_data_full() -> None:
    analysis = MockAnalysis()
    data = extract_bingx_market_data(analysis)
    assert data["price"] == 50000.0
    assert data["volume_24h"] == 1_000_000.0
    assert data["change_24h_pct"] == 2.5
    assert data["market_type"] == "crypto_standard"
    assert data["best_bid"] == 49900.0
    assert data["best_ask"] == 50100.0


def test_extract_bingx_market_data_empty() -> None:
    analysis = MagicMock()
    analysis.venue = None
    analysis.l2 = None
    data = extract_bingx_market_data(analysis)
    assert data == {}


# ═══════════════════════════════════════════════════════════════════════════════
# extract_scanner_indicators
# ═══════════════════════════════════════════════════════════════════════════════


def test_extract_scanner_indicators_from_dict() -> None:
    row = {
        "rsi": 55.0,
        "macd": 0.5,
        "macd_signal": 0.3,
        "vwap": 100.0,
        "atr": 2.0,
        "adx": 30.0,
        "volume_sma": 1_000_000.0,
        "volume_ratio": 1.5,
        "obv": 500_000.0,
        "mfi": 60.0,
        "cci": 100.0,
        "phase_a_score": 0.8,
        "composite_score": 0.75,
    }
    indicators = extract_scanner_indicators(row)
    assert indicators["rsi"] == 55.0
    assert indicators["composite_score"] == 0.75
    assert indicators["volume_sma"] == 1_000_000.0


def test_extract_scanner_indicators_from_dict_partial() -> None:
    row = {"rsi": 55.0, "unknown_key": "ignore"}
    indicators = extract_scanner_indicators(row)
    assert indicators == {"rsi": 55.0}


def test_extract_scanner_indicators_empty() -> None:
    assert extract_scanner_indicators({}) == {}


def test_extract_scanner_indicators_from_object() -> None:
    row = MagicMock()
    row.rsi = 70.0
    row.macd = 1.0
    row.vwap = 200.0
    row.atr = 3.0
    row.adx = 40.0
    row.phase_a_score = 0.9
    row.phase_b_score = 0.8
    row.composite_score = 0.85
    indicators = extract_scanner_indicators(row)
    assert indicators["rsi"] == 70.0
    assert indicators["composite_score"] == 0.85


def test_extract_scanner_indicators_none_values_skipped() -> None:
    row = {"rsi": None, "macd": 0.5}
    indicators = extract_scanner_indicators(row)
    assert "rsi" not in indicators
    assert indicators["macd"] == 0.5
