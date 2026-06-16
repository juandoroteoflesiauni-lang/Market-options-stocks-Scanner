"""Tests for equity L2 gate (Fase 3). # [TH]"""

from __future__ import annotations

from backend.domain.alpaca_models import AlpacaDecision
from backend.services.equity_l2_gate_service import (
    REASON_L2_BEARISH_OFI,
    REASON_L2_SPOOFING_BID,
    EquityL2GateConfig,
    evaluate_equity_l2_gate,
)


def _allow_decision(symbol: str = "AAPL") -> AlpacaDecision:
    return AlpacaDecision(
        symbol=symbol,
        decision="ALLOW",
        direction="LONG",
        score=0.8,
        probability=0.75,
        reason_codes=(),
    )


def test_gate_blocks_bearish_ofi() -> None:
    micro = {
        "ok": True,
        "ofi": {"ok": True, "regime": "StrongDistribution"},
        "lob_stream": {"spoofing_state": "NORMAL", "ctr_bid": 1.0},
        "last_depth_at": 9_999_999_999.0,
    }
    gated, meta = evaluate_equity_l2_gate(
        _allow_decision(),
        micro,
        config=EquityL2GateConfig(enabled=True, max_depth_age_s=60),
    )
    assert gated.decision == "BLOCK"
    assert REASON_L2_BEARISH_OFI in gated.reason_codes
    assert meta["applied"] is True


def test_gate_blocks_bid_spoofing() -> None:
    micro = {
        "ok": True,
        "ofi": {"ok": True, "regime": "Neutral"},
        "lob_stream": {"spoofing_state": "BID_SPOOFING", "ctr_bid": 2.0},
        "last_depth_at": 9_999_999_999.0,
    }
    gated, _ = evaluate_equity_l2_gate(
        _allow_decision(),
        micro,
        config=EquityL2GateConfig(enabled=True),
    )
    assert gated.decision == "BLOCK"
    assert REASON_L2_SPOOFING_BID in gated.reason_codes


def test_gate_skips_non_watchlist() -> None:
    gated, meta = evaluate_equity_l2_gate(
        _allow_decision("BTC"),
        {"ok": True},
        config=EquityL2GateConfig(enabled=True),
    )
    assert gated.decision == "ALLOW"
    assert meta.get("skipped") == "not_in_watchlist"
