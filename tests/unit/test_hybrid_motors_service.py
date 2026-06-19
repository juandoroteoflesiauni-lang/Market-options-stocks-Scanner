"""Unit tests for hybrid motors service and delta profile motor."""

from __future__ import annotations

import numpy as np

from backend.config.bingx_hybrid_motors_calibration import TECHNICAL_WEIGHT_MATRIX
from backend.quant_engine.engines.hybrid.delta_profile_hybrid import run_delta_profile_hybrid
from backend.services.bingx_decision_engine import _engine_bias_vote, _technical_consensus
from backend.services.hybrid_motors_service import (
    hybrid_bias_from_block,
    merge_hybrid_blocks_into_payload,
    run_hybrid_motors,
)


def _synthetic_candles(n: int = 120, *, base: float = 100.0) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    price = base
    for i in range(n):
        drift = np.sin(i / 8.0) * 0.4
        o = price
        c = price + drift
        h = max(o, c) + 0.2
        low = min(o, c) - 0.2
        vol = 1000 + (i % 7) * 120
        candles.append(
            {
                "open_time_ms": 1_700_000_000_000 + i * 300_000,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": vol,
            }
        )
        price = c
    return candles


def test_technical_weight_matrix_sums_to_one() -> None:
    total = round(sum(TECHNICAL_WEIGHT_MATRIX.values()), 6)
    assert total == 1.0
    assert len(TECHNICAL_WEIGHT_MATRIX) == 28


def test_run_hybrid_motors_returns_seven_blocks() -> None:
    candles = _synthetic_candles(120)
    metrics = {
        "spot": 105.0,
        "net_gex_total": 1_500_000.0,
        "call_gex_total": 2_000_000.0,
        "put_gex_total": -500_000.0,
        "zero_gamma": 103.0,
        "atm_iv": 0.32,
        "total_vanna": 25_000.0,
        "ndde": -120_000.0,
        "charm_flow": -5_000.0,
    }
    blocks = run_hybrid_motors(
        ticker="AMD",
        candles=candles,
        options_metrics=metrics,
        raw_options_snapshot={"chain": [{"strike": 105.0, "call_oi": 100, "put_oi": 80}]},
    )
    assert set(blocks) == {
        "hybrid_wavetrend",
        "hybrid_divergences",
        "hybrid_vsa",
        "hybrid_elliott",
        "hybrid_exhaustion",
        "hybrid_shadow_macd",
        "hybrid_delta_profile",
    }
    assert blocks["hybrid_wavetrend"]["ok"] is True
    assert "signal" in blocks["hybrid_wavetrend"]


def test_merge_hybrid_blocks_into_payload() -> None:
    payload = {"hmm_regime": {"ok": True, "regime_signal": "BULLISH"}}
    hybrid = {"hybrid_wavetrend": {"ok": True, "signal": "WT_CROSS_BULL", "strength": 2}}
    merged = merge_hybrid_blocks_into_payload(payload, hybrid)
    assert merged["hybrid_wavetrend"]["signal"] == "WT_CROSS_BULL"
    assert merged["hybrid_motors"]["active"] == 1


def test_hybrid_bias_from_block_maps_bull_and_bear() -> None:
    assert hybrid_bias_from_block({"ok": True, "signal": "DOUBLE_CROSS_BULL"}) == "BULLISH"
    assert hybrid_bias_from_block({"ok": True, "signal": "DISTRIBUTION_ALIGNED"}) == "BEARISH"
    assert hybrid_bias_from_block({"ok": False}) == "NEUTRAL"


def test_engine_bias_vote_hybrid_wavetrend() -> None:
    block = {"ok": True, "signal": "GEX_LEAD_BULL", "strength": 3}
    assert _engine_bias_vote(block, "hybrid_wavetrend") == "BULLISH"


def test_delta_profile_hybrid_with_chain() -> None:
    candles = _synthetic_candles(80)
    chain = [
        {
            "strike": candles[-1]["close"],
            "call_oi": 200,
            "put_oi": 50,
            "call_delta": 0.55,
            "put_delta": -0.45,
        }
    ]
    result = run_delta_profile_hybrid(
        symbol="TEST",
        candles=candles,
        chain_rows=chain,
        spot=float(candles[-1]["close"]),
    )
    assert result["ok"] is True
    assert "vap_delta_pos" in result


class _FakeTechnical:
    venue_technical: dict

    def __init__(self, venue_technical: dict) -> None:
        self.venue_technical = venue_technical


class _FakeAnalysis:
    technical: _FakeTechnical
    avwap_hybrid_signals: dict | None = None

    def __init__(self, payload: dict) -> None:
        self.technical = _FakeTechnical(
            {
                "status": "available",
                "payload": payload,
            }
        )


def test_technical_consensus_includes_hybrid_vote() -> None:
    payload = {
        "hmm_regime": {"ok": True, "regime_signal": "BULLISH"},
        "hybrid_wavetrend": {"ok": True, "signal": "WT_CROSS_BULL", "strength": 2},
        "hybrid_shadow_macd": {"ok": True, "signal": "NEUTRAL", "strength": 0},
    }
    analysis = _FakeAnalysis(payload)
    score, direction, details = _technical_consensus(analysis)
    assert 0.0 <= score <= 1.0
    assert "hybrid_wavetrend" in details["votes"]
    assert details["votes"]["hybrid_wavetrend"]["vote"] == "BULLISH"
    assert direction in ("LONG", "SHORT", "FLAT")
