"""Integration tests for Flow Desk wired into the technical consensus vote."""

from __future__ import annotations

from backend.config.bingx_hybrid_motors_calibration import TECHNICAL_WEIGHT_MATRIX
from backend.services.bingx_decision_engine import _engine_bias_vote, _technical_consensus


def test_weight_matrix_includes_flow_and_sums_to_one() -> None:
    assert "flow_obv_oi" in TECHNICAL_WEIGHT_MATRIX
    assert "flow_mfi_flow" in TECHNICAL_WEIGHT_MATRIX
    assert sum(TECHNICAL_WEIGHT_MATRIX.values()) == 0.05 + (1.0 - 0.05)
    assert round(sum(TECHNICAL_WEIGHT_MATRIX.values()), 6) == 1.0


def test_engine_bias_vote_flow_blocks() -> None:
    assert _engine_bias_vote({"ok": True, "score": 0.70}, "flow_obv_oi") == "BULLISH"
    assert _engine_bias_vote({"ok": True, "score": 0.30}, "flow_mfi_flow") == "BEARISH"
    assert _engine_bias_vote({"ok": True, "score": 0.50}, "flow_obv_oi") == "NEUTRAL"
    assert _engine_bias_vote({"ok": False, "score": 0.90}, "flow_obv_oi") == "NEUTRAL"
    assert _engine_bias_vote(None, "flow_mfi_flow") == "NEUTRAL"


class _Technical:
    def __init__(self, payload: dict) -> None:
        self.venue_technical = {"status": "available", "payload": payload}


class _Analysis:
    def __init__(self, payload: dict) -> None:
        self.technical = _Technical(payload)
        self.avwap_hybrid_signals = None


def test_consensus_counts_flow_votes() -> None:
    # Both flow engines bullish → their weighted contribution is positive.
    payload = {
        "flow_obv_oi": {"ok": True, "score": 0.8, "bias": "BULLISH"},
        "flow_mfi_flow": {"ok": True, "score": 0.75, "bias": "BULLISH"},
    }
    _, _, details = _technical_consensus(_Analysis(payload))  # type: ignore[arg-type]
    votes = details["votes"]
    assert votes["flow_obv_oi"]["vote"] == "BULLISH"
    assert votes["flow_mfi_flow"]["vote"] == "BULLISH"
    assert details["raw_consensus"] > 0.0


def test_consensus_without_flow_blocks_is_neutral_for_flow() -> None:
    _, _, details = _technical_consensus(_Analysis({}))  # type: ignore[arg-type]
    votes = details["votes"]
    assert votes["flow_obv_oi"]["vote"] == "NEUTRAL"
    assert votes["flow_obv_oi"]["ok"] is False
