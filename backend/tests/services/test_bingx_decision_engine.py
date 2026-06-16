from __future__ import annotations
from typing import Any
"""Tests for ``bingx_decision_engine`` — multi-module BingX decision rules.

Coverage:
- INSUFFICIENT_DATA when fewer than 2 core motors are available
- L2 BLOCK in live mode for equity perps; relaxed in dry_run
- Predictive confidence floor with/without technical confirmation
- Options-GEX contradiction with/without technical compensation
- SIZE_DOWN band, partial confluence, medium L2 quality
- ALLOW with full confluence and FULL_CONFLUENCE reason
- Direction resolution (predictive primary, technical fallback, conflict → FLAT)
- Env-knob plumbing
- JSON safety
"""


import json

import pytest

from backend.services.bingx_candidate_analysis import (
    BingXCandidateAnalysis,
    BingXL2Block,
    BingXOptionsBlock,
    BingXPredictiveBlock,
    BingXTechnicalBlock,
    BingXUnderlyingBlock,
    BingXVenueBlock,
)
from backend.services.bingx_decision_engine import (
    REASON_CONFLUENCE_DIVERGENCE,
    REASON_CRYPTO_DERIVATIVES_OVERHEATING,
    REASON_DEX_ZGL_INVALIDATION,
    REASON_DIRECTION_CONFLICT,
    REASON_FULL_CONFLUENCE,
    REASON_INSUFFICIENT_CORE_MOTORS,
    REASON_L2_REQUIRED_FOR_EQUITY_LIVE,
    REASON_LOW_AGGREGATE_SCORE,
    REASON_MEDIUM_DATA_QUALITY,
    REASON_OPTIONS_CONTRADICTS_DIRECTION,
    REASON_PARTIAL_CONFLUENCE,
    REASON_PREDICTIVE_BELOW_FLOOR,
    REASON_TECHNICAL_CONSENSUS_NEUTRAL,
    REASON_TECHNICAL_DOES_NOT_COMPENSATE,
    REASON_TECHNICAL_NOT_CONFIRMING,
    REASON_VENUE_TECHNICAL_ALIGNED,
    REASON_VOLATILITY_PANIC_SIZE_DOWN,
    BingXDecisionConfig,
    decide,
)

# ── Fixture builders ─────────────────────────────────────────────────────────


def _venue(*, available: bool = True, bars: int = 60) -> BingXVenueBlock:
    if not available:
        return BingXVenueBlock(venue_symbol="GOOGL-USDT", status="unavailable", source="none")
    return BingXVenueBlock(
        venue_symbol="GOOGL-USDT",
        status="available",
        source="bingx_perp_klines",
        venue_ta={
            "bars_count": bars,
            "last_price": 180.0,
            "trend": "bullish",
            "rsi_14": 58.0,
            "ema_9": 180.5,
            "ema_21": 179.0,
        },
    )


def _underlying() -> BingXUnderlyingBlock:
    return BingXUnderlyingBlock(
        underlying_symbol="GOOGL",
        market_type="stock_perp",
        ohlcv_status="available",
        source="fmp",
    )


def _technical(
    *,
    available: bool = True,
    quality: float = 0.85,
    smc_bias: str = "BULLISH",
    trend: str = "bullish",
    venue_status: str = "available",
    payload: dict[str, Any] | None = None,
) -> BingXTechnicalBlock:
    venue_tech: dict[str, Any] = {
        "status": venue_status,
        "technical_quality_score": quality,
        "summary": {
            "trend_direction": trend,
            "smc_bias": smc_bias,
            "vsa_signal": "STRONG_BUY" if smc_bias == "BULLISH" else "STRONG_SELL",
            "fvg_state": "bullish_dominant" if smc_bias == "BULLISH" else "bearish_dominant",
            "volume_profile_bias": trend,
            "composite_score": 0.72,
            "bars_used": 40,
        },
    }
    if payload is not None:
        venue_tech["payload"] = payload
    if not available:
        return BingXTechnicalBlock(
            status="unavailable",
            source="none",
            reason="no_equity_ta_for_market_type",
            venue_technical=None,
        )
    return BingXTechnicalBlock(
        status="available",
        source="fmp",
        quality_score=quality,
        metrics={"ok": True, "rsi_14": 55.0, "bars_used": 200},
        venue_technical=venue_tech,
    )


def _options(
    *,
    available: bool = True,
    quality: float = 0.8,
    dealer_bias: str = "BULLISH",
) -> BingXOptionsBlock:
    if not available:
        return BingXOptionsBlock(status="unavailable", source="none", reason="no_chain")
    return BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=quality,
        metrics={
            "status": "available",
            "metrics": {
                "dealer_bias": dealer_bias,
                "call_wall": 185.0,
                "put_wall": 175.0,
            },
        },
    )


def _predictive(
    *,
    available: bool = True,
    bias: str = "LONG",
    confidence: float = 0.7,
    quality: float = 0.65,
    source: str = "meta_signal",
) -> BingXPredictiveBlock:
    if not available:
        return BingXPredictiveBlock(
            status="unavailable", source="none", reason="all_sources_failed"
        )
    return BingXPredictiveBlock(
        status="available",
        source=source,
        quality_score=quality,
        signal={
            "directional_bias": bias,
            "probability_long": 0.65 if bias == "LONG" else 0.25,
            "probability_short": 0.20 if bias == "LONG" else 0.65,
            "confidence": confidence,
            "horizon": "intraday",
            "source": source,
            "quality_score": quality,
            "reason_codes": [],
        },
    )


def _l2(*, available: bool = True, quality: float = 0.75) -> BingXL2Block:
    if not available:
        return BingXL2Block(
            status="unavailable", source="bingx_l2_unavailable", reason="snapshot_empty"
        )
    return BingXL2Block(
        status="available",
        source="bingx_l2_snapshot_rest",
        quality_score=quality,
        lob_analysis={"ok": True, "source": "bingx_l2_snapshot_rest"},
    )


def _analysis(
    *,
    market_type: str = "stock_perp",
    venue: BingXVenueBlock | None = None,
    technical: BingXTechnicalBlock | None = None,
    options: BingXOptionsBlock | None = None,
    predictive: BingXPredictiveBlock | None = None,
    l2: BingXL2Block | None = None,
) -> BingXCandidateAnalysis:
    return BingXCandidateAnalysis(
        venue_symbol="GOOGL-USDT",
        underlying_symbol="GOOGL",
        market_type=market_type,
        venue=venue if venue is not None else _venue(),
        underlying=_underlying(),
        options=options if options is not None else _options(),
        technical=technical if technical is not None else _technical(),
        predictive=predictive if predictive is not None else _predictive(),
        l2=l2 if l2 is not None else _l2(),
    )


def _config(**overrides: Any) -> BingXDecisionConfig:
    defaults: dict[str, Any] = {
        "min_decision_score": 0.55,
        "min_predictive_confidence": 0.50,
        "require_l2_for_equity_live": True,
        "size_down_band": 0.10,
    }
    defaults.update(overrides)
    return BingXDecisionConfig(**defaults)


# ── INSUFFICIENT_DATA ───────────────────────────────────────────────────────


def test_insufficient_data_when_only_one_core_motor_available() -> None:
    """Only venue is up — every other engine unavailable."""
    analysis = _analysis(
        venue=_venue(),
        technical=_technical(available=False),
        options=_options(available=False),
        predictive=_predictive(available=False),
        l2=_l2(available=False),
    )
    result = decide(analysis, mode="dry_run", config=_config())
    assert result.decision == "INSUFFICIENT_DATA"
    assert result.direction == "FLAT"
    assert REASON_INSUFFICIENT_CORE_MOTORS in result.reason_codes


def test_insufficient_data_still_yields_score_zero_safely() -> None:
    analysis = _analysis(
        venue=_venue(available=False),
        technical=_technical(available=False),
        options=_options(available=False),
        predictive=_predictive(available=False),
        l2=_l2(available=False),
    )
    result = decide(analysis, mode="dry_run", config=_config())
    assert result.decision == "INSUFFICIENT_DATA"
    assert result.score_total == 0.0
    assert result.module_scores.venue == 0.0
    assert result.module_scores.predictive == 0.0


# ── L2 live-mode gate ────────────────────────────────────────────────────────


def test_block_when_equity_perp_missing_l2_in_live_mode() -> None:
    analysis = _analysis(l2=_l2(available=False))
    result = decide(analysis, mode="live", config=_config(require_l2_for_equity_live=True))
    assert result.decision == "BLOCK"
    assert REASON_L2_REQUIRED_FOR_EQUITY_LIVE in result.reason_codes


def test_dry_run_does_not_block_equity_perp_without_l2() -> None:
    """In dry_run the L2 gate is informational — the decision proceeds and may SIZE_DOWN."""
    analysis = _analysis(l2=_l2(available=False))
    result = decide(analysis, mode="dry_run", config=_config(require_l2_for_equity_live=True))
    assert result.decision != "BLOCK"
    # Live-only reason must NOT surface in dry_run.
    assert REASON_L2_REQUIRED_FOR_EQUITY_LIVE not in result.reason_codes


def test_crypto_never_triggers_l2_live_gate() -> None:
    analysis = _analysis(
        market_type="crypto_standard",
        l2=_l2(available=False),
        # Crypto won't have technical/predictive in the equity sense, but
        # we keep them populated to clear the INSUFFICIENT_DATA gate.
    )
    result = decide(analysis, mode="live", config=_config())
    assert REASON_L2_REQUIRED_FOR_EQUITY_LIVE not in result.reason_codes


def test_l2_gate_off_via_config_skips_block() -> None:
    analysis = _analysis(l2=_l2(available=False))
    result = decide(
        analysis,
        mode="live",
        config=_config(require_l2_for_equity_live=False),
    )
    assert REASON_L2_REQUIRED_FOR_EQUITY_LIVE not in result.reason_codes


# ── Predictive confidence floor ─────────────────────────────────────────────


def test_block_when_predictive_below_floor_and_technical_does_not_confirm() -> None:
    """Predictive LONG with low confidence + technical bearish → BLOCK."""
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.30),
        technical=_technical(smc_bias="BEARISH", trend="bearish"),
    )
    result = decide(analysis, mode="dry_run", config=_config(min_predictive_confidence=0.50))
    assert result.decision == "BLOCK"
    assert REASON_PREDICTIVE_BELOW_FLOOR in result.reason_codes
    assert REASON_TECHNICAL_NOT_CONFIRMING in result.reason_codes


def test_low_predictive_confidence_with_technical_confirmation_does_not_block() -> None:
    """Same low confidence but technical agrees → not BLOCK (may be SIZE_DOWN)."""
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.30),
        technical=_technical(smc_bias="BULLISH", trend="bullish"),
    )
    result = decide(analysis, mode="dry_run", config=_config(min_predictive_confidence=0.50))
    assert result.decision != "BLOCK"
    assert REASON_PREDICTIVE_BELOW_FLOOR in result.reason_codes


# ── Options-GEX contradiction ───────────────────────────────────────────────


def test_block_when_options_contradicts_and_technical_does_not_compensate() -> None:
    """Predictive LONG, options BEARISH, technical bearish → BLOCK."""
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.70),
        options=_options(dealer_bias="BEARISH"),
        technical=_technical(smc_bias="BEARISH", trend="bearish"),
    )
    result = decide(analysis, mode="dry_run", config=_config())
    assert result.decision == "BLOCK"
    assert REASON_OPTIONS_CONTRADICTS_DIRECTION in result.reason_codes
    assert REASON_TECHNICAL_DOES_NOT_COMPENSATE in result.reason_codes


def test_options_contradiction_compensated_by_technical_size_down() -> None:
    """Options contradicts predictive, but technical confirms predictive → SIZE_DOWN."""
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.70),
        options=_options(dealer_bias="BEARISH"),  # contradicts LONG
        technical=_technical(smc_bias="BULLISH", trend="bullish"),  # compensates
    )
    result = decide(analysis, mode="dry_run", config=_config())
    assert result.decision == "SIZE_DOWN"
    assert REASON_OPTIONS_CONTRADICTS_DIRECTION in result.reason_codes
    assert REASON_PARTIAL_CONFLUENCE in result.reason_codes


# ── ALLOW path ──────────────────────────────────────────────────────────────


def test_allow_when_all_modules_align_long() -> None:
    """Predictive + technical + options all bullish, L2 available, scores high."""
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.80, quality=0.85),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.90),
        options=_options(dealer_bias="BULLISH", quality=0.85),
        l2=_l2(quality=0.80),
    )
    result = decide(analysis, mode="live", config=_config())
    assert result.decision == "ALLOW"
    assert result.direction == "LONG"
    assert REASON_FULL_CONFLUENCE in result.reason_codes
    assert REASON_VENUE_TECHNICAL_ALIGNED in result.reason_codes
    assert result.score_total >= 0.55


def test_allow_when_all_modules_align_short() -> None:
    analysis = _analysis(
        predictive=_predictive(bias="SHORT", confidence=0.78, quality=0.80),
        technical=_technical(smc_bias="BEARISH", trend="bearish", quality=0.85),
        options=_options(dealer_bias="BEARISH", quality=0.80),
        l2=_l2(quality=0.75),
    )
    result = decide(analysis, mode="live", config=_config())
    assert result.decision == "ALLOW"
    assert result.direction == "SHORT"


def test_allow_when_options_neutral_does_not_count_as_contradiction() -> None:
    """Options direction FLAT (no dealer_bias) should NOT trigger contradiction."""
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.75, quality=0.75),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.85),
        options=_options(dealer_bias="NEUTRAL", quality=0.6),
        l2=_l2(quality=0.75),
    )
    result = decide(analysis, mode="live", config=_config())
    assert result.decision == "ALLOW"
    assert REASON_OPTIONS_CONTRADICTS_DIRECTION not in result.reason_codes


# ── SIZE_DOWN band ──────────────────────────────────────────────────────────


def test_size_down_when_score_below_min_but_in_band() -> None:
    """Score in the [min - band, min] window → SIZE_DOWN, not BLOCK."""
    # Low quality across the board pushes aggregate score below 0.55 but
    # not below the 0.45 band lower bound.
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.55, quality=0.40),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.40),
        options=_options(dealer_bias="BULLISH", quality=0.40),
        l2=_l2(quality=0.30),
    )
    result = decide(analysis, mode="dry_run", config=_config(min_decision_score=0.55))
    assert result.decision == "SIZE_DOWN"
    assert REASON_MEDIUM_DATA_QUALITY in result.reason_codes


def test_block_when_score_below_band_lower_bound() -> None:
    """Score < min - band → BLOCK with low_aggregate_score."""
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.55, quality=0.15),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.15),
        options=_options(dealer_bias="BULLISH", quality=0.15),
        l2=_l2(quality=0.10),
    )
    result = decide(analysis, mode="dry_run", config=_config(min_decision_score=0.55))
    assert result.decision == "BLOCK"
    assert REASON_LOW_AGGREGATE_SCORE in result.reason_codes


# ── Direction resolution ────────────────────────────────────────────────────


def test_direction_taken_from_predictive_when_available() -> None:
    analysis = _analysis(
        predictive=_predictive(bias="SHORT", confidence=0.7, quality=0.7),
        technical=_technical(smc_bias="BEARISH", trend="bearish"),
        options=_options(dealer_bias="BEARISH"),
    )
    result = decide(analysis, mode="live", config=_config())
    assert result.direction == "SHORT"


def test_direction_falls_back_to_technical_when_predictive_neutral() -> None:
    analysis = _analysis(
        predictive=_predictive(bias="NEUTRAL", confidence=0.55, quality=0.55),
        technical=_technical(smc_bias="BULLISH", trend="bullish"),
    )
    result = decide(analysis, mode="live", config=_config())
    # Direction should follow technical when predictive is FLAT.
    assert result.direction in ("LONG", "FLAT")


def test_direction_conflict_forces_flat_and_size_down() -> None:
    """Predictive LONG vs technical SHORT → FLAT direction, max SIZE_DOWN."""
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.75, quality=0.75),
        technical=_technical(smc_bias="BEARISH", trend="bearish", quality=0.75),
        options=_options(dealer_bias="NEUTRAL", quality=0.7),
        l2=_l2(quality=0.7),
    )
    result = decide(analysis, mode="live", config=_config())
    assert result.decision == "SIZE_DOWN"
    assert result.direction == "FLAT"
    assert REASON_DIRECTION_CONFLICT in result.reason_codes


# ── Env knobs ───────────────────────────────────────────────────────────────


def test_env_min_decision_score_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINGX_MIN_DECISION_SCORE", "5.0")  # > 1.0
    cfg = BingXDecisionConfig.from_env()
    assert cfg.min_decision_score == 1.0


def test_env_min_decision_score_invalid_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BINGX_MIN_DECISION_SCORE", "not-a-number")
    cfg = BingXDecisionConfig.from_env()
    assert cfg.min_decision_score == 0.55


def test_env_min_predictive_confidence_respected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BINGX_MIN_PREDICTIVE_CONFIDENCE", "0.85")
    cfg = BingXDecisionConfig.from_env()
    assert cfg.min_predictive_confidence == 0.85


def test_env_require_l2_for_equity_live_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINGX_REQUIRE_L2_FOR_EQUITY_LIVE", "false")
    cfg = BingXDecisionConfig.from_env()
    assert cfg.require_l2_for_equity_live is False


# ── Module-score correctness ─────────────────────────────────────────────────


def test_module_scores_reflect_block_quality() -> None:
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.80, quality=0.72),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.85),
        options=_options(dealer_bias="BULLISH", quality=0.65),
        l2=_l2(quality=0.55),
    )
    result = decide(analysis, mode="live", config=_config())
    assert result.module_scores.venue > 0.0
    assert result.module_scores.technical == 0.85
    assert result.module_scores.options == 0.65
    assert result.module_scores.predictive == 0.72
    assert result.module_scores.l2 == 0.55
    # Risk score starts at 1.0 with no conflicts → 1.0
    assert result.module_scores.risk == 1.0


def test_risk_score_penalises_direction_conflict() -> None:
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.7, quality=0.7),
        technical=_technical(smc_bias="BEARISH", trend="bearish"),
        options=_options(dealer_bias="NEUTRAL"),
        l2=_l2(quality=0.8),
    )
    result = decide(analysis, mode="live", config=_config())
    # 1.0 - 0.4 (direction conflict) = 0.6
    assert result.module_scores.risk == pytest.approx(0.6, abs=1e-9)


def test_risk_score_penalises_options_contradiction_when_technical_does_not_compensate() -> None:
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.7, quality=0.7),
        # Technical is FLAT (neutral) — neither confirms nor conflicts.
        technical=_technical(smc_bias="NEUTRAL", trend="neutral"),
        options=_options(dealer_bias="BEARISH"),
        l2=_l2(quality=0.8),
    )
    result = decide(analysis, mode="live", config=_config())
    # 1.0 - 0.3 (options contradicts, no technical compensation) = 0.7
    assert result.module_scores.risk == pytest.approx(0.7, abs=1e-9)


# ── JSON safety ──────────────────────────────────────────────────────────────


def test_decision_to_dict_is_json_safe() -> None:
    analysis = _analysis()
    result = decide(analysis, mode="live", config=_config())
    payload = result.to_dict()
    serialised = json.dumps(payload)
    parsed = json.loads(serialised)
    assert parsed["symbol"] == "GOOGL-USDT"
    assert parsed["decision"] in {"ALLOW", "SIZE_DOWN", "BLOCK", "INSUFFICIENT_DATA"}
    assert parsed["direction"] in {"LONG", "SHORT", "FLAT"}
    assert isinstance(parsed["module_scores"], dict)
    assert isinstance(parsed["reason_codes"], list)


def test_ndde_veto_long() -> None:
    opts = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "dealer_bias": "BULLISH",
                "ndde": -5.0,  # contradicts LONG
            },
        },
    )
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.7),
        options=opts,
    )
    result = decide(analysis, mode="live", config=_config())
    assert result.decision == "BLOCK"
    assert "ndde_contradicts_direction" in result.reason_codes


def test_ndde_veto_short() -> None:
    opts = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "dealer_bias": "BEARISH",
                "ndde": 5.0,  # contradicts SHORT
            },
        },
    )
    analysis = _analysis(
        predictive=_predictive(bias="SHORT", confidence=0.7),
        options=opts,
    )
    result = decide(analysis, mode="live", config=_config())
    assert result.decision == "BLOCK"
    assert "ndde_contradicts_direction" in result.reason_codes


def test_charm_flow_penalty_long_bearish_string() -> None:
    opts = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "dealer_bias": "BULLISH",
                "charm_flow": "bearish",  # contradicts LONG
            },
        },
    )
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.8),
        options=opts,
    )
    # Get base decision to see base score
    base_analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.8),
        options=_options(dealer_bias="BULLISH", quality=0.8),
    )
    base_res = decide(base_analysis, mode="live", config=_config())

    result = decide(analysis, mode="live", config=_config())
    assert "charm_flow_penalty" in result.reason_codes
    assert result.score_total == pytest.approx(base_res.score_total - 0.15, abs=1e-4)


def test_charm_flow_penalty_short_numeric() -> None:
    opts = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "dealer_bias": "BEARISH",
                "charm_flow": 1.25,  # positive contradicts SHORT
            },
        },
    )
    analysis = _analysis(
        predictive=_predictive(bias="SHORT", confidence=0.8, quality=0.8),
        technical=_technical(smc_bias="BEARISH", trend="bearish", quality=0.8),
        options=opts,
    )
    base_analysis = _analysis(
        predictive=_predictive(bias="SHORT", confidence=0.8, quality=0.8),
        technical=_technical(smc_bias="BEARISH", trend="bearish", quality=0.8),
        options=_options(dealer_bias="BEARISH", quality=0.8),
    )
    base_res = decide(base_analysis, mode="live", config=_config())

    result = decide(analysis, mode="live", config=_config())
    assert "charm_flow_penalty" in result.reason_codes
    assert result.score_total == pytest.approx(base_res.score_total - 0.15, abs=1e-4)


# ── Weighted institutional consensus tests ──────────────────────────────────


def _directional_engine_payload(direction: str = "BULLISH") -> dict[str, Any]:
    """Build a realistic 16-engine terminal payload voting ``direction``."""
    if direction == "BEARISH":
        vote = "BEARISH"
        above = False
        acc_ofi = -5.0
        bull_cnt = 1
        bear_cnt = 5
        total_bull = 200.0
        total_bear = 1000.0
        zscore = -1.2
        imbalance = -0.3
        period_delta = -100.0
        poc_delta = -2.0
        skew = -0.5
    else:
        vote = "BULLISH"
        above = True
        acc_ofi = 5.0
        bull_cnt = 5
        bear_cnt = 1
        total_bull = 1000.0
        total_bear = 200.0
        zscore = 1.2
        imbalance = 0.3
        period_delta = 100.0
        poc_delta = 2.0
        skew = 0.5

    return {
        "hmm_regime": {"ok": True, "regime_signal": vote, "current_label": vote},
        "ofi": {"ok": True, "regime": "NEUTRAL", "latest_accumulated_ofi": acc_ofi},
        "volume_profile": {
            "ok": True,
            "volume_bias": vote.lower(),
            "is_above_avwap": above,
            "is_above_poc": above,
        },
        "vwap_advanced": {
            "ok": True,
            "above_vwap": above,
            "price_zscore": zscore,
            "price_vs_vwap": "above" if above else "below",
        },
        "lob_dynamics": {"ok": True, "imbalance": imbalance},
        "vsa": {"ok": True, "signal": "STRONG_BUY" if above else "STRONG_SELL"},
        "fvg": {"ok": True, "bullish_active_count": bull_cnt, "bearish_active_count": bear_cnt},
        "order_flow_delta": {"ok": True, "delta_bias": vote, "latest_period_delta": period_delta},
        "delta_volume": {
            "ok": True,
            "poc_delta_bias": vote,
            "total_bull": total_bull,
            "total_bear": total_bear,
        },
        "vpoc_migration": {"ok": True, "state": vote, "poc_delta": poc_delta},
        "tpo_skewness": {"ok": True, "skewness_value": skew, "profile_shape": vote},
        "single_prints": {"ok": True, "active_count": 3, "zones": []},
        "vsa_footprint": {"ok": True, "nearest_support": 178.0, "nearest_resistance": 182.0},
        "smc": {"ok": True, "sesgo": vote, "composite_score": 0.7},
        "candle_geometry": {"ok": True},
        "market_structure": {"ok": True, "bias": vote},
    }


def test_consensus_long_above_threshold() -> None:
    analysis = _analysis(
        technical=_technical(payload=_directional_engine_payload("BULLISH")),
    )
    from backend.services.bingx_decision_engine import _technical_consensus

    score, direction, details = _technical_consensus(analysis)
    assert direction == "LONG"
    assert score > 0.5
    assert details.get("raw_consensus", 0) > 0.60


def test_consensus_short_above_threshold() -> None:
    analysis = _analysis(
        technical=_technical(payload=_directional_engine_payload("BEARISH")),
    )
    from backend.services.bingx_decision_engine import _technical_consensus

    score, direction, details = _technical_consensus(analysis)
    assert direction == "SHORT"
    assert score < 0.5
    assert details.get("raw_consensus", 0) < -0.60


def test_consensus_flat_below_threshold() -> None:
    payload = _directional_engine_payload("BULLISH")
    for neutral_engine in ("hmm_regime", "ofi", "vwap_advanced", "lob_dynamics", "vsa", "fvg"):
        engine = payload.get(neutral_engine)
        if isinstance(engine, dict):
            engine["ok"] = False
    analysis = _analysis(technical=_technical(payload=payload))
    from backend.services.bingx_decision_engine import _technical_consensus

    score, direction, details = _technical_consensus(analysis)
    assert direction == "FLAT"
    raw = details.get("raw_consensus", 1.0)
    assert -0.60 <= raw <= 0.60


def test_consensus_falls_back_when_payload_missing() -> None:
    analysis = _analysis(technical=_technical())
    from backend.services.bingx_decision_engine import _technical_consensus

    score, direction, details = _technical_consensus(analysis)
    assert details.get("fallback") is not None
    assert direction == "FLAT"
    assert score == 0.5


def test_technical_direction_uses_consensus_when_available() -> None:
    analysis = _analysis(
        technical=_technical(payload=_directional_engine_payload("BULLISH")),
    )
    from backend.services.bingx_decision_engine import _technical_direction

    assert _technical_direction(analysis) == "LONG"


def test_technical_score_uses_consensus_when_available() -> None:
    analysis = _analysis(
        technical=_technical(payload=_directional_engine_payload("BULLISH")),
    )
    from backend.services.bingx_decision_engine import _technical_score

    score = _technical_score(analysis)
    assert 0.0 <= score <= 1.0
    assert score > 0.5


def test_full_confluence_passes_with_consensus_payload() -> None:
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        technical=_technical(payload=_directional_engine_payload("BULLISH")),
        options=_options(dealer_bias="BULLISH", quality=0.8),
    )
    result = decide(analysis, mode="live", config=_config())
    assert result.decision == "ALLOW"
    assert result.direction == "LONG"
    assert REASON_FULL_CONFLUENCE in result.reason_codes


def test_technical_consensus_neutral_reason_added() -> None:
    """When consensus is FLAT (< 60%) with a payload present, the reason code is added."""
    payload = _directional_engine_payload("BULLISH")
    for engine in (
        "hmm_regime",
        "ofi",
        "vwap_advanced",
        "lob_dynamics",
        "vsa",
        "fvg",
        "order_flow_delta",
    ):
        eng = payload.get(engine)
        if isinstance(eng, dict):
            eng["ok"] = False
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        technical=_technical(payload=payload),
        options=_options(dealer_bias="BULLISH", quality=0.8),
    )
    result = decide(analysis, mode="live", config=_config())
    assert REASON_TECHNICAL_CONSENSUS_NEUTRAL in result.reason_codes


def test_confluence_multiplier_and_divergence() -> None:
    # 1. Alignment (LONG predictive, BULLISH confluence signal)
    # This should apply +0.20 score total boost.
    opts_align = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "confluence_score": 0.8,
                "confluence_signal": "BULLISH",
            },
        },
    )
    analysis_align = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.8),
        options=opts_align,
    )
    # Check baseline score without confluence signal
    opts_base = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "confluence_score": 0.8,
                "confluence_signal": None,
            },
        },
    )
    analysis_base = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.8),
        options=opts_base,
    )
    res_base = decide(analysis_base, mode="live", config=_config())
    res_align = decide(analysis_align, mode="live", config=_config())

    assert res_align.score_total == min(1.0, round(res_base.score_total + 0.20, 4))

    # 2. Divergence (LONG predictive, BEARISH confluence signal) -> BLOCK
    opts_diverge = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "confluence_score": 0.8,
                "confluence_signal": "BEARISH",
            },
        },
    )
    analysis_diverge = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        options=opts_diverge,
    )
    res_diverge = decide(analysis_diverge, mode="live", config=_config())
    assert res_diverge.decision == "BLOCK"
    assert REASON_CONFLUENCE_DIVERGENCE in res_diverge.reason_codes


def test_crypto_derivatives_overheating_funding() -> None:
    from backend.services.bingx_candidate_analysis import BingXExchangeDerivativesBlock

    # 1. Overheating funding rate for LONG (avg funding > 0.0008)
    deriv = BingXExchangeDerivativesBlock(
        status="available",
        source="coinglass",
        providers=(
            {"provider": "binance", "funding_rate": 0.0009, "status": "available"},
            {"provider": "okx", "funding_rate": 0.0008, "status": "available"},
        ),
    )
    analysis = _analysis(
        market_type="crypto_standard",
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
    )
    # Set the exchange_derivatives attribute
    object.__setattr__(analysis, "exchange_derivatives", deriv)

    res = decide(analysis, mode="live", config=_config())
    assert res.decision == "BLOCK"
    assert REASON_CRYPTO_DERIVATIVES_OVERHEATING in res.reason_codes


def test_crypto_derivatives_overheating_liquidations() -> None:
    from backend.services.bingx_candidate_analysis import BingXExchangeDerivativesBlock

    # 2. Extreme cascading liquidations (> 100k USD on long direction for a LONG)
    deriv = BingXExchangeDerivativesBlock(
        status="available",
        source="coinglass",
        providers=(
            {"provider": "binance", "long_liquidations_usd": 150000.0, "status": "available"},
        ),
    )
    analysis = _analysis(
        market_type="crypto_standard",
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
    )
    object.__setattr__(analysis, "exchange_derivatives", deriv)

    res = decide(analysis, mode="live", config=_config())
    assert res.decision == "BLOCK"
    assert REASON_CRYPTO_DERIVATIVES_OVERHEATING in res.reason_codes


def test_volatility_sizing_vrp_and_skew() -> None:
    # 1. Negative VRP (< -0.15) -> SIZE_DOWN
    opts_vrp = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "vrp": -0.20,
            },
        },
    )
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.8),
        options=opts_vrp,
    )
    res = decide(analysis, mode="live", config=_config())
    assert res.decision == "SIZE_DOWN"
    assert REASON_VOLATILITY_PANIC_SIZE_DOWN in res.reason_codes

    # 2. Skew panic (> 0.15) -> SIZE_DOWN
    opts_skew = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "skew_25d": 0.25,
            },
        },
    )
    analysis2 = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        technical=_technical(smc_bias="BULLISH", trend="bullish", quality=0.8),
        options=opts_skew,
    )
    res2 = decide(analysis2, mode="live", config=_config())
    assert res2.decision == "SIZE_DOWN"
    assert REASON_VOLATILITY_PANIC_SIZE_DOWN in res2.reason_codes


def test_dex_zgl_invalidation() -> None:
    # Spot below ZGL (zero_gamma) and negative total_dex -> BLOCK long trade
    opts = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "zero_gamma": 190.0,
                "total_dex": -1000.0,
            },
        },
    )
    underlying = BingXUnderlyingBlock(
        underlying_symbol="GOOGL",
        market_type="stock_perp",
        ohlcv_status="available",
        source="fmp",
        quote={"price": 180.0},
    )
    # venue spot price is 180.0 from underlying.quote
    analysis = _analysis(
        predictive=_predictive(bias="LONG", confidence=0.8, quality=0.8),
        options=opts,
    )
    object.__setattr__(analysis, "underlying", underlying)
    res = decide(analysis, mode="live", config=_config())
    assert res.decision == "BLOCK"
    assert REASON_DEX_ZGL_INVALIDATION in res.reason_codes
