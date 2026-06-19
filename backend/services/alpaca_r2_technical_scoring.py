"""Scoring técnico L1 para Ruta 2 Alpaca (sin L2 ni opciones). # [PD-3][TH][IM]"""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.config.alpaca_institutional_config import ivpin_enabled
from backend.config.alpaca_r2_scoring_config import (
    R2_CONFLUENCE_CORE_ENGINES,
    R2_L1_ENGINE_KEYS,
    R2_S1_MIN_ENGINES,
    R2_S2_MIN_ENGINES,
    R2_S3_MIN_ENGINES,
    R2_STRUCTURE_GATE_ENGINES,
    R2_VOLUME_GATE_ENGINES,
    r2_confluence_min_engines,
    r2_gate_veto_threshold,
    r2_hmm_bullish_only,
    r2_min_score,
    r2_vsa_volume_gate,
)
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import AlpacaCandidateAnalysis
from backend.quant_engine.math.technical.ivpin import compute_ivpin

logger = get_logger(__name__)

EngineVote = Literal["BULLISH", "BEARISH", "NEUTRAL"]
ConfluenceTier = Literal["NONE", "S1", "S2", "S3"]

REASON_R2_GATE_VETO = "r2_gate_veto"
REASON_R2_LOW_CONFLUENCE = "r2_low_confluence"
REASON_R2_LOW_TECH_SCORE = "r2_low_technical_score"
REASON_R2_BEARISH_REGIME = "r2_bearish_regime"
REASON_R2_INSUFFICIENT_PAYLOAD = "r2_insufficient_technical_payload"


class R2TechnicalScoreResult(BaseModel):
    """Resultado del scoring multi-motor L1 para R2."""

    model_config = ConfigDict(frozen=True)

    score_0_100: float = 0.0
    confluence_count: int = 0
    core_confluence_count: int = 0
    confluence_tier: ConfluenceTier = "NONE"
    regime_gate: float = 0.0
    volume_gate: float = 0.0
    structure_gate: float = 0.0
    veto: bool = False
    bullish_engines: tuple[str, ...] = ()
    engine_votes: dict[str, str] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()
    ivpin: float | None = None
    ivpin_gate: float = 1.0


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _vote_value(vote: EngineVote) -> float:
    if vote == "BULLISH":
        return 1.0
    if vote == "BEARISH":
        return 0.0
    return 0.5


def _engine_bias_vote(block: dict[str, Any] | None, engine: str) -> EngineVote:
    """Normaliza el voto direccional de un motor L1."""
    if not isinstance(block, dict) or not block.get("ok"):
        return "NEUTRAL"

    if engine == "hmm_regime":
        signal = str(block.get("regime_signal") or "").upper()
        if signal == "BULLISH":
            return "BULLISH"
        if signal == "BEARISH":
            return "BEARISH"
        label = str(block.get("current_label") or "").upper()
        if label in {"BULLISH", "BULL"}:
            return "BULLISH"
        if label in {"BEARISH", "BEAR"}:
            return "BEARISH"
        return "NEUTRAL"

    if engine == "market_structure":
        regime = str(block.get("regime") or "").upper()
        if regime in {"BULLISH", "BULL"}:
            return "BULLISH"
        if regime in {"BEARISH", "BEAR"}:
            return "BEARISH"
        return "NEUTRAL"

    if engine == "candle_geometry":
        direction = block.get("latest_direction") or block.get("direction")
        if direction == 1 or str(direction).upper() in {"1", "BULLISH", "UP"}:
            return "BULLISH"
        if direction == -1 or str(direction).upper() in {"-1", "BEARISH", "DOWN"}:
            return "BEARISH"
        return "NEUTRAL"

    if engine == "ofi":
        regime = str(block.get("regime") or "").upper()
        if regime in {"BUYING", "ACCUMULATION", "STRONG_ACCUMULATION"}:
            return "BULLISH"
        if regime in {"SELLING", "DISTRIBUTION", "STRONG_DISTRIBUTION"}:
            return "BEARISH"
        acc = _safe_float(block.get("latest_accumulated_ofi"))
        if acc is not None:
            return "BULLISH" if acc > 0 else "BEARISH" if acc < 0 else "NEUTRAL"
        return "NEUTRAL"

    if engine == "volume_profile":
        bias = str(block.get("volume_bias") or "").lower()
        if bias == "bullish":
            return "BULLISH"
        if bias == "bearish":
            return "BEARISH"
        if block.get("is_above_avwap") is True and block.get("is_above_poc") is True:
            return "BULLISH"
        if block.get("is_above_avwap") is False and block.get("is_above_poc") is False:
            return "BEARISH"
        return "NEUTRAL"

    if engine == "vwap_advanced":
        if block.get("above_vwap") is True:
            return "BULLISH"
        if block.get("above_vwap") is False:
            return "BEARISH"
        zscore = _safe_float(block.get("price_zscore"))
        if zscore is not None:
            return "BULLISH" if zscore > 0 else "BEARISH" if zscore < 0 else "NEUTRAL"
        return "NEUTRAL"

    if engine == "vsa":
        signal = str(block.get("signal") or "").upper().replace(" ", "_")
        if signal in {"STRONG_BUY", "BUY"}:
            return "BULLISH"
        if signal in {"STRONG_SELL", "SELL"}:
            return "BEARISH"
        if block.get("long_signal_active") is True:
            return "BULLISH"
        return "NEUTRAL"

    if engine == "fvg":
        bull = int(block.get("bullish_active_count") or 0)
        bear = int(block.get("bearish_active_count") or 0)
        if bull > bear:
            return "BULLISH"
        if bear > bull:
            return "BEARISH"
        return "NEUTRAL"

    if engine == "order_flow_delta":
        bias = str(block.get("delta_bias") or "").upper()
        if bias == "BULLISH":
            return "BULLISH"
        if bias == "BEARISH":
            return "BEARISH"
        latest = _safe_float(block.get("latest_period_delta"))
        if latest is not None:
            return "BULLISH" if latest > 0 else "BEARISH" if latest < 0 else "NEUTRAL"
        return "NEUTRAL"

    if engine == "delta_volume":
        bias = str(block.get("poc_delta_bias") or "").upper()
        if bias in {"BULLISH", "BUYING"}:
            return "BULLISH"
        if bias in {"BEARISH", "SELLING"}:
            return "BEARISH"
        bull = _safe_float(block.get("total_bull"))
        bear = _safe_float(block.get("total_bear"))
        if bull is not None and bear is not None and (bull + bear) > 0:
            return "BULLISH" if bull > bear else "BEARISH" if bear > bull else "NEUTRAL"
        return "NEUTRAL"

    if engine == "vpoc_migration":
        state = str(block.get("state") or "").upper()
        if state == "BULLISH":
            return "BULLISH"
        if state == "BEARISH":
            return "BEARISH"
        poc_delta = _safe_float(block.get("poc_delta"))
        if poc_delta is not None:
            return "BULLISH" if poc_delta > 0 else "BEARISH" if poc_delta < 0 else "NEUTRAL"
        return "NEUTRAL"

    if engine == "tpo_skewness":
        skew = _safe_float(block.get("skewness_value"))
        if skew is not None:
            return "BULLISH" if skew > 0 else "BEARISH" if skew < 0 else "NEUTRAL"
        shape = str(block.get("profile_shape") or "").upper()
        if shape == "BULLISH":
            return "BULLISH"
        if shape == "BEARISH":
            return "BEARISH"
        return "NEUTRAL"

    if engine == "single_prints":
        active = int(block.get("active_count") or 0)
        if active > 0:
            return "BULLISH"
        return "NEUTRAL"

    if engine == "volume_nodes":
        nodes = block.get("nodes") or block.get("active_nodes") or []
        if isinstance(nodes, list) and nodes:
            bull_nodes = sum(
                1
                for n in nodes
                if isinstance(n, dict) and str(n.get("bias", "")).upper() == "BULLISH"
            )
            bear_nodes = sum(
                1
                for n in nodes
                if isinstance(n, dict) and str(n.get("bias", "")).upper() == "BEARISH"
            )
            if bull_nodes > bear_nodes:
                return "BULLISH"
            if bear_nodes > bull_nodes:
                return "BEARISH"
        return "NEUTRAL"

    if engine == "vsa_footprint":
        support = _safe_float(block.get("nearest_support"))
        resistance = _safe_float(block.get("nearest_resistance"))
        if support is not None and resistance is not None and resistance > support:
            return "BULLISH"
        return "NEUTRAL"

    return "NEUTRAL"


def _mean_gate(votes: list[EngineVote]) -> float:
    if not votes:
        return 0.5
    return sum(_vote_value(v) for v in votes) / len(votes)


def _regime_gate(hmm_vote: EngineVote, *, hmm_bullish_only: bool) -> tuple[float, tuple[str, ...]]:
    reasons: list[str] = []
    if hmm_vote == "BULLISH":
        return 1.0, tuple(reasons)
    if hmm_vote == "BEARISH":
        if hmm_bullish_only:
            reasons.append(REASON_R2_BEARISH_REGIME)
        return 0.15, tuple(reasons)
    return 0.7, tuple(reasons)


def _confluence_tier(count: int) -> ConfluenceTier:
    if count >= R2_S3_MIN_ENGINES:
        return "S3"
    if count >= R2_S2_MIN_ENGINES:
        return "S2"
    if count >= R2_S1_MIN_ENGINES:
        return "S1"
    return "NONE"


def _ivpin_from_payload(payload: dict[str, Any]) -> tuple[float | None, float]:
    """Extract iVPIN from microstructure block and return (ivpin, gate multiplier)."""
    if not ivpin_enabled():
        return None, 1.0
    micro = payload.get("microstructure") or payload.get("vpin") or {}
    if not isinstance(micro, dict):
        return None, 1.0
    buy = micro.get("buy_volumes") or micro.get("buy_volume_buckets")
    sell = micro.get("sell_volumes") or micro.get("sell_volume_buckets")
    if isinstance(buy, list) and isinstance(sell, list) and buy and sell:
        result = compute_ivpin([float(x) for x in buy], [float(x) for x in sell])
        ivpin_val = result.get("ivpin")
        if ivpin_val is None:
            return None, 1.0
        # High toxicity reduces score; gate in [0.5, 1.0]
        gate = max(0.5, 1.0 - float(ivpin_val) * 0.5)
        return float(ivpin_val), gate
    raw = micro.get("ivpin") or micro.get("vpin") or micro.get("vpin_proxy")
    if raw is not None:
        ivpin_val = float(raw)
        gate = max(0.5, 1.0 - ivpin_val * 0.5)
        return ivpin_val, gate
    return None, 1.0


def score_route2_technical(payload: dict[str, Any]) -> R2TechnicalScoreResult:
    """Calcula score 0-100, gates y tier de confluencia desde technical_payload."""
    if not payload or payload.get("ok") is False:
        return R2TechnicalScoreResult(
            veto=True,
            reason_codes=(REASON_R2_INSUFFICIENT_PAYLOAD,),
        )

    votes: dict[str, EngineVote] = {}
    for engine in R2_L1_ENGINE_KEYS:
        block = payload.get(engine)
        votes[engine] = _engine_bias_vote(block if isinstance(block, dict) else None, engine)

    bullish = tuple(sorted(e for e, v in votes.items() if v == "BULLISH"))
    confluence_count = len(bullish)
    core_count = sum(1 for e in R2_CONFLUENCE_CORE_ENGINES if votes.get(e) == "BULLISH")
    tier = _confluence_tier(confluence_count)

    hmm_vote = votes.get("hmm_regime", "NEUTRAL")
    regime_gate, regime_reasons = _regime_gate(hmm_vote, hmm_bullish_only=r2_hmm_bullish_only())

    volume_votes = [votes[e] for e in R2_VOLUME_GATE_ENGINES if e in votes]
    volume_gate = _mean_gate(volume_votes) if r2_vsa_volume_gate() else 1.0

    structure_votes = [votes[e] for e in R2_STRUCTURE_GATE_ENGINES if e in votes]
    structure_gate = _mean_gate(structure_votes)

    ivpin_val, ivpin_gate = _ivpin_from_payload(payload)

    raw_confluence = (confluence_count / max(len(R2_L1_ENGINE_KEYS), 1)) * 100.0
    gate_product = regime_gate * volume_gate * structure_gate * ivpin_gate
    score = round(min(100.0, raw_confluence * gate_product), 2)

    reasons = list(regime_reasons)
    veto = False
    veto_threshold = r2_gate_veto_threshold()
    accept_s1 = os.getenv("ALPACA_R2_ACCEPT_S1", "").lower() in {"1", "true", "yes"}
    min_tiers = {"S1", "S2", "S3"} if accept_s1 else {"S2", "S3"}
    if regime_gate < veto_threshold:
        veto = True
        reasons.append(REASON_R2_GATE_VETO)
    if volume_gate < veto_threshold:
        veto = True
        if REASON_R2_GATE_VETO not in reasons:
            reasons.append(REASON_R2_GATE_VETO)
    if structure_gate < veto_threshold:
        veto = True
        if REASON_R2_GATE_VETO not in reasons:
            reasons.append(REASON_R2_GATE_VETO)
    if tier not in min_tiers or confluence_count < r2_confluence_min_engines():
        reasons.append(REASON_R2_LOW_CONFLUENCE)
    if score < r2_min_score():
        reasons.append(REASON_R2_LOW_TECH_SCORE)

    return R2TechnicalScoreResult(
        score_0_100=score,
        confluence_count=confluence_count,
        core_confluence_count=core_count,
        confluence_tier=tier,
        regime_gate=round(regime_gate, 4),
        volume_gate=round(volume_gate, 4),
        structure_gate=round(structure_gate, 4),
        veto=veto,
        bullish_engines=bullish,
        engine_votes=dict(votes),
        reason_codes=tuple(dict.fromkeys(reasons)),
        ivpin=ivpin_val,
        ivpin_gate=round(ivpin_gate, 4),
    )


def enrich_route2_analysis(
    analysis: AlpacaCandidateAnalysis,
) -> AlpacaCandidateAnalysis:
    """Adjunta scoring L1 al análisis de Ruta 2."""
    if analysis.route != "scan":
        return analysis
    result = score_route2_technical(analysis.technical_payload)
    return analysis.model_copy(
        update={
            "r2_technical_score": result.model_dump(mode="json"),
            "r2_confluence_tier": result.confluence_tier,
        }
    )


__all__ = [
    "REASON_R2_BEARISH_REGIME",
    "REASON_R2_GATE_VETO",
    "REASON_R2_LOW_CONFLUENCE",
    "REASON_R2_LOW_TECH_SCORE",
    "R2TechnicalScoreResult",
    "enrich_route2_analysis",
    "score_route2_technical",
]
