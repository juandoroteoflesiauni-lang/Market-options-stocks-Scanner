"""Unit tests for the Flow Desk (OBV-OI + MFI-Flow confluence)."""

from __future__ import annotations

from typing import Any

import pytest

import backend.services.bingx_flow_desk as mod
from backend.services.bingx_flow_desk import FlowDeskSnapshot, build_flow_desk_snapshot
from backend.services.market_scanner_mfi_flow import MfiFlowScannerResult
from backend.services.market_scanner_obv_oi import ObvOiScannerResult


def _klines(n: int = 40) -> list[dict[str, Any]]:
    base_ms = 1_700_000_000_000
    out: list[dict[str, Any]] = []
    for i in range(n):
        price = 100.0 + i * 0.1
        out.append(
            {
                "t": base_ms + i * 300_000,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price + 0.2,
                "volume": 1000.0 + i,
            }
        )
    return out


def _obv(ok: bool, score: float, bias: str) -> ObvOiScannerResult:
    return ObvOiScannerResult(
        ok=ok,
        score=score,
        bias=bias,
        signal=0,
        confidence=0.5,
        engine_status="real",
        metrics={},
        reasons=[],
    )


def _mfi(ok: bool, score: float, bias: str) -> MfiFlowScannerResult:
    return MfiFlowScannerResult(
        ok=ok,
        score=score,
        bias=bias,
        signal=0,
        confidence=0.5,
        engine_status="real",
        metrics={},
        reasons=[],
    )


def _patch(monkeypatch: pytest.MonkeyPatch, obv: Any, mfi: Any) -> None:
    monkeypatch.setattr(mod, "analyze_obv_oi_for_scanner", lambda *a, **k: obv)
    monkeypatch.setattr(mod, "analyze_mfi_flow_for_scanner", lambda *a, **k: mfi)


def test_confluence_bullish(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _obv(True, 70.0, "bullish"), _mfi(True, 65.0, "bullish"))
    snap = build_flow_desk_snapshot("AAPL", klines=_klines(), options_snapshot={})
    assert isinstance(snap, FlowDeskSnapshot)
    assert snap.status == "available"
    assert snap.confluence_vote == "BULLISH"
    assert snap.engine_blocks["flow_obv_oi"]["score"] == 0.7
    assert snap.engine_blocks["flow_mfi_flow"]["bias"] == "BULLISH"
    assert snap.weight == pytest.approx(0.05)


def test_confluence_bearish(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _obv(True, 30.0, "bearish"), _mfi(True, 35.0, "bearish"))
    snap = build_flow_desk_snapshot("AAPL", klines=_klines(), options_snapshot={})
    assert snap.confluence_vote == "BEARISH"


def test_mixed_bias_is_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _obv(True, 70.0, "bullish"), _mfi(True, 35.0, "bearish"))
    snap = build_flow_desk_snapshot("AAPL", klines=_klines(), options_snapshot={})
    assert snap.status == "available"
    assert snap.confluence_vote == "NEUTRAL"


def test_both_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _obv(False, 50.0, "neutral"), _mfi(False, 50.0, "neutral"))
    snap = build_flow_desk_snapshot("AAPL", klines=_klines(), options_snapshot=None)
    assert snap.status == "unavailable"
    assert snap.reason == "scanner_unavailable"
    assert snap.confluence_vote == "NEUTRAL"


def test_insufficient_bars() -> None:
    snap = build_flow_desk_snapshot("AAPL", klines=_klines(5), options_snapshot={})
    assert snap.status == "unavailable"
    assert snap.reason == "insufficient_bars"


def test_scanner_exception_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("pipeline error")

    monkeypatch.setattr(mod, "analyze_obv_oi_for_scanner", _boom)
    monkeypatch.setattr(
        mod, "analyze_mfi_flow_for_scanner", lambda *a, **k: _mfi(True, 60.0, "bullish")
    )
    snap = build_flow_desk_snapshot("AAPL", klines=_klines(), options_snapshot={})
    assert snap.status == "unavailable"
    assert snap.reason == "flow_desk_failed"


def test_partial_obv_only_available(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only OBV ok → still available, but vote NEUTRAL (mfi not bullish).
    _patch(monkeypatch, _obv(True, 70.0, "bullish"), _mfi(False, 50.0, "neutral"))
    snap = build_flow_desk_snapshot("AAPL", klines=_klines(), options_snapshot={})
    assert snap.status == "available"
    assert snap.confluence_vote == "NEUTRAL"
