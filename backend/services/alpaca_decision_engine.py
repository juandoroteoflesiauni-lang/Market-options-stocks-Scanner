"""Motor de decisión nativo para acciones (Alpaca). # [PD-3][IM][TH]

LONG-only. Combina señales de equities (spike de volumen, posición en el
rango/ruptura, momentum MACD y fuerza relativa) en un score determinista.
No depende de ``bingx_decision_engine`` ni de semántica de perpetuos.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict

from backend.config.alpaca_r1_options_scoring_config import get_r1_blend_weights
from backend.config.alpaca_r2_scoring_config import (
    R2_CLASSIC_WEIGHT,
    R2_TECH_WEIGHT,
    r2_confluence_min_engines,
    r2_min_score,
)
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import AlpacaCandidateAnalysis, AlpacaDecision

logger = get_logger(__name__)

# ─── Reason codes estables ────────────────────────────────────────────────────
REASON_INSUFFICIENT_DATA = "insufficient_data"
REASON_NOT_BULLISH = "not_bullish"
REASON_LOW_PROBABILITY = "low_probability"
REASON_R2_GATE_VETO = "r2_gate_veto"
REASON_R2_LOW_CONFLUENCE = "r2_low_confluence"
REASON_R2_LOW_TECH_SCORE = "r2_low_technical_score"
REASON_R2_BEARISH_REGIME = "r2_bearish_regime"

# ─── Pesos del score compuesto (suman 1.0) ────────────────────────────────────
_WEIGHT_VOLUME = 0.30
_WEIGHT_BREAKOUT = 0.30
_WEIGHT_MACD = 0.20
_WEIGHT_RS = 0.20
_PROB_BASE = 0.50
_PROB_SPAN = 0.45


class AlpacaDecisionConfig(BaseModel):
    """Umbrales configurables del motor de decisión."""

    model_config = ConfigDict(frozen=True)

    min_volume_z: float = 1.0
    min_close_position: float = 0.60
    prob_floor: float = 0.55
    size_down_band: float = 0.05

    @classmethod
    def from_env(cls) -> AlpacaDecisionConfig:
        """Construye la config desde variables de entorno (con defaults)."""
        return cls(
            min_volume_z=float(os.getenv("ALPACA_MIN_VOLUME_Z", "1.0")),
            min_close_position=float(os.getenv("ALPACA_MIN_CLOSE_POSITION", "0.60")),
            prob_floor=float(os.getenv("ALPACA_PROB_FLOOR", "0.55")),
            size_down_band=float(os.getenv("ALPACA_SIZE_DOWN_BAND", "0.05")),
        )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _feature_score(analysis: AlpacaCandidateAnalysis, config: AlpacaDecisionConfig) -> float:
    """Score compuesto 0..1 a partir de las señales técnicas."""
    vol_z = analysis.volume_z_score or 0.0
    vol_component = _clamp01(vol_z / max(config.min_volume_z * 2.0, 1e-9))
    breakout = _clamp01(analysis.close_position_in_range or 0.0)
    macd_component = 1.0 if (analysis.macd_histogram or 0.0) > 0.0 else 0.0
    rs_component = 1.0 if (analysis.relative_strength or 0.0) > 0.0 else 0.0
    return (
        _WEIGHT_VOLUME * vol_component
        + _WEIGHT_BREAKOUT * breakout
        + _WEIGHT_MACD * macd_component
        + _WEIGHT_RS * rs_component
    )


def _is_bullish(analysis: AlpacaCandidateAnalysis, config: AlpacaDecisionConfig) -> bool:
    """Condición de sesgo alcista para habilitar LONG."""
    if os.getenv("ALPACA_VERIFICATION_RELAXED_BULLISH", "").lower() in {"1", "true", "yes"}:
        return (analysis.latest_close or 0.0) > 0.0
    return (
        (analysis.close_position_in_range or 0.0) >= config.min_close_position
        and (analysis.macd_histogram or 0.0) > 0.0
        and (analysis.volume_z_score or 0.0) >= config.min_volume_z
    )


def _blend_route2_score(classic_score: float, tech_score_0_100: float) -> float:
    """Mezcla score clásico (0-1) con score técnico L1 (0-100)."""
    tech_norm = _clamp01(tech_score_0_100 / 100.0)
    return _clamp01(R2_CLASSIC_WEIGHT * classic_score + R2_TECH_WEIGHT * tech_norm)


def _blend_route1_score(classic_score: float, options_score: float) -> float:
    """Mezcla score técnico R1 con confluencia de opciones (0-1)."""
    classic_w, options_w = get_r1_blend_weights()
    return _clamp01(classic_w * classic_score + options_w * options_score)


def _apply_route2_gates(
    analysis: AlpacaCandidateAnalysis,
    classic_score: float,
    reasons: list[str],
) -> tuple[float, tuple[str, ...], str]:
    """Aplica gates L1 de R2 sobre el veredicto clásico."""
    r2 = analysis.r2_technical_score or {}
    if not r2 or analysis.route != "scan":
        return classic_score, tuple(reasons), "pass"

    tech_score = float(r2.get("score_0_100") or 0.0)
    tier = str(r2.get("confluence_tier") or analysis.r2_confluence_tier or "NONE")
    confluence = int(r2.get("confluence_count") or 0)
    veto = bool(r2.get("veto"))
    r2_reasons = list(r2.get("reason_codes") or ())
    for code in r2_reasons:
        if code not in reasons:
            reasons.append(code)

    blended = _blend_route2_score(classic_score, tech_score)

    if veto:
        return blended, tuple(reasons + [REASON_R2_GATE_VETO]), "block_veto"
    min_tiers = {"S1", "S2", "S3"} if os.getenv(
        "ALPACA_R2_ACCEPT_S1", ""
    ).lower() in {"1", "true", "yes"} else {"S2", "S3"}
    if tier not in min_tiers or confluence < r2_confluence_min_engines():
        return blended, tuple(reasons + [REASON_R2_LOW_CONFLUENCE]), "block_confluence"
    if tech_score < r2_min_score():
        return blended, tuple(reasons + [REASON_R2_LOW_TECH_SCORE]), "block_tech_score"

    return blended, tuple(dict.fromkeys(reasons)), "pass"


def decide(
    analysis: AlpacaCandidateAnalysis,
    config: AlpacaDecisionConfig | None = None,
) -> AlpacaDecision:
    """Produce un veredicto LONG/FLAT a partir del análisis técnico."""
    cfg = config or AlpacaDecisionConfig()
    if analysis.latest_close is None or not analysis.technical_ok:
        return AlpacaDecision(
            symbol=analysis.symbol,
            decision="INSUFFICIENT_DATA",
            direction="FLAT",
            score=0.0,
            reason_codes=(REASON_INSUFFICIENT_DATA,),
            route=analysis.route,
        )

    classic_score = _feature_score(analysis, cfg)
    score = classic_score
    reasons_acc: list[str] = []

    if analysis.route == "priority" and analysis.options_confluence is not None:
        conf = analysis.options_confluence
        score = _blend_route1_score(classic_score, conf.score)
        reasons_acc.extend(list(conf.reason_codes))

    if analysis.route == "scan" and analysis.r2_technical_score:
        score, r2_reasons, r2_verdict = _apply_route2_gates(
            analysis, classic_score, reasons_acc
        )
        reasons_acc = list(r2_reasons)
        if r2_verdict.startswith("block"):
            probability = _PROB_BASE + _PROB_SPAN * score
            return AlpacaDecision(
                symbol=analysis.symbol,
                decision="BLOCK",
                direction="FLAT",
                score=round(score, 4),
                probability=round(probability, 4),
                reason_codes=tuple(dict.fromkeys(reasons_acc)),
                route=analysis.route,
            )

    probability = _PROB_BASE + _PROB_SPAN * score
    
    try:
        from backend.ml_engine.models.random_forest_classifier import TradePredictor
        predictor = TradePredictor()
        if predictor.load():
            indicators = {
                "volume_z_score": analysis.volume_z_score,
                "close_position_in_range": analysis.close_position_in_range,
                "macd_histogram": analysis.macd_histogram,
                "relative_strength": analysis.relative_strength,
                "score": score,
                "probability": probability
            }
            ml_prob = predictor.predict_prob(indicators)
            probability = 0.70 * probability + 0.30 * ml_prob
            if ml_prob < 0.40:
                reasons_acc.append("ml_prob_low")
    except Exception as exc:
        logger.debug("alpaca_bot.ml_predict_failed error=%s", exc)
    if not _is_bullish(analysis, cfg):
        return AlpacaDecision(
            symbol=analysis.symbol,
            decision="BLOCK",
            direction="FLAT",
            score=round(score, 4),
            probability=round(probability, 4),
            reason_codes=(REASON_NOT_BULLISH,),
            route=analysis.route,
        )

    if probability < cfg.prob_floor:
        suitability = "BLOCK"
        reasons: tuple[str, ...] = (REASON_LOW_PROBABILITY,)
    elif probability < cfg.prob_floor + cfg.size_down_band:
        suitability = "SIZE_DOWN"
        reasons = (REASON_LOW_PROBABILITY,)
    else:
        suitability = "ALLOW"
        reasons = ()
    merged_reasons = tuple(dict.fromkeys((*reasons_acc, *reasons)))
    return AlpacaDecision(
        symbol=analysis.symbol,
        decision=suitability,
        direction="LONG" if suitability != "BLOCK" else "FLAT",
        score=round(score, 4),
        probability=round(probability, 4),
        reason_codes=merged_reasons,
        route=analysis.route,
    )
