"""Tests scoring técnico L1 Ruta 2 Alpaca. # [TH][IM]"""

from __future__ import annotations

from backend.domain.alpaca_models import AlpacaCandidateAnalysis
from backend.services.alpaca_decision_engine import (
    REASON_R2_GATE_VETO,
    REASON_R2_LOW_CONFLUENCE,
    decide,
)
from backend.services.alpaca_r2_technical_scoring import (
    REASON_R2_BEARISH_REGIME,
    enrich_route2_analysis,
    score_route2_technical,
)


def _bullish_block() -> dict:
    return {"ok": True, "enabled": True}


def _bullish_payload() -> dict[str, object]:
    return {
        "ok": True,
        "market_structure": {**_bullish_block(), "regime": "Bullish"},
        "fvg": {**_bullish_block(), "bullish_active_count": 3, "bearish_active_count": 0},
        "vsa": {**_bullish_block(), "signal": "BUY", "long_signal_active": True},
        "tpo_skewness": {**_bullish_block(), "skewness_value": 0.4, "profile_shape": "BULLISH"},
        "volume_profile": {
            **_bullish_block(),
            "volume_bias": "bullish",
            "is_above_avwap": True,
            "is_above_poc": True,
        },
        "hmm_regime": {**_bullish_block(), "regime_signal": "BULLISH", "current_label": "BULL"},
        "ofi": {**_bullish_block(), "regime": "BUYING", "latest_accumulated_ofi": 1.2},
        "order_flow_delta": {**_bullish_block(), "delta_bias": "BULLISH", "latest_period_delta": 5.0},
        "vwap_advanced": {**_bullish_block(), "above_vwap": True, "price_zscore": 0.5},
        "delta_volume": {**_bullish_block(), "poc_delta_bias": "BULLISH", "total_bull": 10, "total_bear": 2},
        "vpoc_migration": {**_bullish_block(), "state": "BULLISH", "poc_delta": 0.3},
        "single_prints": {**_bullish_block(), "active_count": 2},
        "candle_geometry": {**_bullish_block(), "latest_direction": 1},
        "volume_nodes": {**_bullish_block(), "nodes": [{"bias": "BULLISH"}]},
        "vsa_footprint": {
            **_bullish_block(),
            "nearest_support": 10.0,
            "nearest_resistance": 12.0,
        },
    }


def _bearish_hmm_payload() -> dict[str, object]:
    payload = _bullish_payload()
    payload["hmm_regime"] = {
        **_bullish_block(),
        "regime_signal": "BEARISH",
        "current_label": "BEAR",
    }
    return payload


def _weak_volume_payload() -> dict[str, object]:
    payload = _bullish_payload()
    payload["vsa"] = {**_bullish_block(), "signal": "SELL"}
    payload["volume_profile"] = {
        **_bullish_block(),
        "volume_bias": "bearish",
        "is_above_avwap": False,
        "is_above_poc": False,
    }
    payload["ofi"] = {**_bullish_block(), "regime": "SELLING", "latest_accumulated_ofi": -2.0}
    return payload


def test_score_route2_high_confluence_tier_s3() -> None:
    result = score_route2_technical(_bullish_payload())
    assert result.confluence_tier == "S3"
    assert result.confluence_count >= 6
    assert result.score_0_100 >= 65
    assert not result.veto


def test_score_route2_hmm_bearish_sets_regime_gate_low() -> None:
    result = score_route2_technical(_bearish_hmm_payload())
    assert result.regime_gate < 0.3
    assert result.veto
    assert REASON_R2_BEARISH_REGIME in result.reason_codes


def test_score_route2_volume_gate_veto() -> None:
    result = score_route2_technical(_weak_volume_payload())
    assert result.volume_gate < 0.3
    assert result.veto
    assert REASON_R2_GATE_VETO in result.reason_codes


def test_enrich_route2_analysis_attaches_score() -> None:
    analysis = AlpacaCandidateAnalysis(
        symbol="CLSK",
        timestamp="t",
        route="scan",
        technical_payload=_bullish_payload(),
    )
    enriched = enrich_route2_analysis(analysis)
    assert enriched.r2_confluence_tier in {"S2", "S3"}
    assert enriched.r2_technical_score.get("score_0_100", 0) >= 65


def test_decide_blocks_route2_when_r2_gates_fail() -> None:
    analysis = AlpacaCandidateAnalysis(
        symbol="CLSK",
        timestamp="t",
        route="scan",
        latest_close=10.0,
        atr=0.5,
        macd_histogram=0.1,
        relative_strength=1.0,
        volume_z_score=2.0,
        close_position_in_range=0.8,
        technical_ok=True,
        technical_payload=_weak_volume_payload(),
    )
    enriched = enrich_route2_analysis(analysis)
    decision = decide(enriched)
    assert decision.decision == "BLOCK"
    assert REASON_R2_GATE_VETO in decision.reason_codes


def test_decide_allows_route2_when_classic_and_r2_pass() -> None:
    analysis = AlpacaCandidateAnalysis(
        symbol="CLSK",
        timestamp="t",
        route="scan",
        latest_close=10.0,
        atr=0.5,
        macd_histogram=0.1,
        relative_strength=1.0,
        volume_z_score=2.0,
        close_position_in_range=0.8,
        technical_ok=True,
        technical_payload=_bullish_payload(),
    )
    enriched = enrich_route2_analysis(analysis)
    decision = decide(enriched)
    assert decision.decision == "ALLOW"
    assert decision.direction == "LONG"


def test_decide_blocks_route2_low_confluence() -> None:
    sparse = {"ok": True, "hmm_regime": {**_bullish_block(), "regime_signal": "BULLISH"}}
    analysis = AlpacaCandidateAnalysis(
        symbol="X",
        timestamp="t",
        route="scan",
        latest_close=10.0,
        atr=0.5,
        macd_histogram=0.1,
        relative_strength=1.0,
        volume_z_score=2.0,
        close_position_in_range=0.8,
        technical_ok=True,
        technical_payload=sparse,
    )
    enriched = enrich_route2_analysis(analysis)
    decision = decide(enriched)
    assert decision.decision == "BLOCK"
    assert REASON_R2_LOW_CONFLUENCE in decision.reason_codes
