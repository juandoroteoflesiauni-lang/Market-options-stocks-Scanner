from __future__ import annotations
from typing import Any
"""final_signal_composer.py
============================
Final-stage signal composition.

Stitches together:
  · meta-learner calibrated probabilities {UP, DOWN, NEUTRAL}
  · orchestrator engine signal (signal/confidence/regime info)
  · SignalQuality assessment (conviction + contradictions)
  · FilterResult (selectivity gate + position multiplier)
  · Kelly fat-tail sizing from layer 2 math

Public API
----------
- compose_final_signal(meta_result, engine_result, quality, filter_result,
                       portfolio_context) -> dict
"""



import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.services.signal_filter import FilterResult
from backend.services.signal_quality import ConvictionLevel, SignalQuality

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HALF_KELLY = 0.5  # canonical Half-Kelly multiplier
_DRAWDOWN_REDUCTION_PCT = 0.10  # > 10 % current DD ⇒ shrink size 50 %
_DRAWDOWN_REDUCTION_MULT = 0.50
_DEFAULT_MAX_RISK_PCT = 0.02  # 2 % per trade if not provided
_DEFAULT_ODDS = 1.0  # 1:1 reward/risk fallback


# ---------------------------------------------------------------------------
# Kelly fat-tail wrapper (binary outcome formulation)
# ---------------------------------------------------------------------------


def _kelly_fat_tail_binary(
    p_win: float,
    odds: float,
    tail_factor: float = 1.0,
) -> float:
    """
    Binary-outcome Kelly with explicit fat-tail penalty.

        f* = p_win - (1 - p_win) / odds
        f_adjusted = f* / tail_factor

    Where tail_factor ≥ 1 deflates the fraction in proportion to fat-tail
    risk (e.g. 1 + q_kurtosis/4). Negative Kelly clips to 0 — no edge ⇒ no bet.
    """
    p = max(0.0, min(1.0, float(p_win)))
    o = max(1e-9, float(odds))
    tf = max(1.0, float(tail_factor))

    raw = p - (1.0 - p) / o
    if raw <= 0:
        return 0.0
    return float(max(0.0, min(1.0, raw / tf)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _direction_from_probs(p_up: float, p_down: float, p_neutral: float) -> str:
    """Argmax over the three calibrated probabilities."""
    arr = np.array([p_down, p_neutral, p_up], dtype=float)
    idx = int(np.argmax(arr))
    return ["DOWN", "NEUTRAL", "UP"][idx]


def _signal_from_probs(p_up: float, p_down: float) -> float:
    """Map directional probabilities to a signed score in [-1, 1]."""
    return float(max(-1.0, min(1.0, p_up - p_down)))


def _extract_key_drivers(
    meta_result: dict[str, Any],
    quality: SignalQuality,
    top_n: int = 5,
) -> list[str]:
    """
    Pull the top SHAP-attributed feature names from the meta-learner output.
    Falls back to SignalQuality.top_drivers if SHAP isn't available.
    """
    explanation = meta_result.get("_explanation") if isinstance(meta_result, dict) else None
    if isinstance(explanation, dict):
        top_pos = explanation.get("top_positive_features") or []
        top_neg = explanation.get("top_negative_features") or []
        merged = [name for name, _ in (*top_pos, *top_neg)]
        if merged:
            return merged[:top_n]

    return [name for name, _ in (quality.top_drivers or [])][:top_n]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compose_final_signal(
    meta_result: dict[str, Any],
    engine_result: dict[str, Any],
    quality: SignalQuality,
    filter_result: FilterResult,
    portfolio_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Produce the final actionable signal.

    Parameters
    ──────────
    meta_result : dict from EnsembleMetaLearner.predict_proba()
        Required keys: UP, DOWN, NEUTRAL
        Optional keys: _explanation (SHAP dict), q_kurtosis, expected_move
    engine_result : dict from synthesize_fusion_signal()
        Used for regime, conflict_score, expected_move fallback.
    quality : SignalQuality
    filter_result : FilterResult
    portfolio_context : {capital_total, max_risk_per_trade_pct,
                         current_drawdown_pct, n_open_positions}

    Returns
    ───────
    dict with the full final-signal contract — see module docstring / spec.
    """
    if not isinstance(meta_result, dict) or not isinstance(engine_result, dict):
        raise TypeError("meta_result and engine_result must be dicts")
    if not isinstance(quality, SignalQuality):
        raise TypeError("quality must be a SignalQuality instance")
    if not isinstance(filter_result, FilterResult):
        raise TypeError("filter_result must be a FilterResult instance")
    if not isinstance(portfolio_context, dict):
        raise TypeError("portfolio_context must be a dict")

    # ── Calibrated probabilities ────────────────────────────────────────────
    p_up = _safe_float(meta_result.get("UP"), 0.0)
    p_down = _safe_float(meta_result.get("DOWN"), 0.0)
    p_neutral = _safe_float(meta_result.get("NEUTRAL"), 0.0)

    direction = _direction_from_probs(p_up, p_down, p_neutral)
    signal = _signal_from_probs(p_up, p_down)

    # ── Confidence: prefer calibrated max prob; fall back to engine field ──
    confidence = max(p_up, p_down, p_neutral)
    if confidence <= 0:
        confidence = _safe_float(engine_result.get("confidence"), 0.0)
    confidence = float(max(0.0, min(1.0, confidence)))

    # ── Kelly fat-tail sizing ───────────────────────────────────────────────
    if direction == "NEUTRAL":
        p_win = max(p_up, p_down)
    else:
        p_win = p_up if direction == "UP" else p_down
    p_win = float(max(0.0, min(1.0, p_win)))

    expected_move = _safe_float(
        meta_result.get("expected_move", engine_result.get("expected_move", _DEFAULT_ODDS)),
        _DEFAULT_ODDS,
    )
    odds = max(0.10, expected_move) if expected_move > 0 else _DEFAULT_ODDS

    q_kurtosis = _safe_float(
        meta_result.get("q_kurtosis", engine_result.get("q_kurtosis", 0.0)),
        0.0,
    )
    tail_factor = 1.0 + max(0.0, q_kurtosis) / 4.0

    kelly_fraction = _kelly_fat_tail_binary(p_win, odds, tail_factor)
    kelly_fraction_half = kelly_fraction * _HALF_KELLY

    # ── Final position sizing ───────────────────────────────────────────────
    capital = _safe_float(portfolio_context.get("capital_total"), 0.0)
    max_risk = _safe_float(
        portfolio_context.get("max_risk_per_trade_pct"),
        _DEFAULT_MAX_RISK_PCT,
    )
    current_dd = _safe_float(portfolio_context.get("current_drawdown_pct"), 0.0)

    # 3A. Options Flow Toxicity (si data disponible)
    toxicity_mult_dict = engine_result.get("options_flow_toxicity", {})
    toxicity_multiplier = _safe_float(toxicity_mult_dict.get("multiplier"), 1.0)
    toxicity_multiplier = float(max(0.50, min(1.0, toxicity_multiplier)))

    # 3B. Shadow Delta (si data disponible)
    shadow_delta_dict = engine_result.get("shadow_delta", {})
    shadow_delta_multiplier = _safe_float(shadow_delta_dict.get("multiplier"), 1.0)
    shadow_delta_multiplier = float(max(1.0, min(1.40, shadow_delta_multiplier)))

    # 3C. Combinar multiplicadores vía weighted average
    TOXICITY_WEIGHT = 0.40
    SHADOW_DELTA_WEIGHT = 0.60

    combined_options_multiplier = float(
        toxicity_multiplier * TOXICITY_WEIGHT + shadow_delta_multiplier * SHADOW_DELTA_WEIGHT
    )

    # 3D. Aplicar al base_size_pct (ANTES de quality & filter multipliers)
    base_size_pct = (
        kelly_fraction_half
        * combined_options_multiplier
        * float(filter_result.position_size_multiplier)
        * float(quality.position_size_factor)
    )

    if current_dd > _DRAWDOWN_REDUCTION_PCT:
        base_size_pct *= _DRAWDOWN_REDUCTION_MULT

    position_size_pct = float(max(0.0, min(base_size_pct, max_risk)))
    if not filter_result.should_trade:
        position_size_pct = 0.0

    position_size_usd = float(position_size_pct * max(0.0, capital))

    # ── FTMO Validation ─────────────────────────────────────────────────────
    actual_risk_pct = position_size_pct / max(capital, 1e-9) * 100.0 if capital > 0 else 0.0
    if actual_risk_pct > 3.0:
        logger.warning(
            "Position size %.4f%% exceeds FTMO 3%% daily loss limit. Clamping.",
            actual_risk_pct,
        )
        position_size_pct = 0.03 * max(0.0, capital) if max_risk > 0.03 else max_risk
        position_size_usd = position_size_pct * max(0.0, capital)

    # Logging para debugging
    try:
        logger.debug(
            "Position sizing: kelly=%.4f, toxicity_mult=%.4f, shadow_delta_mult=%.4f, "
            "combined=%.4f, quality_factor=%.4f, filter_mult=%.4f, final_pct=%.4f",
            kelly_fraction_half,
            toxicity_multiplier,
            shadow_delta_multiplier,
            combined_options_multiplier,
            float(quality.position_size_factor),
            float(filter_result.position_size_multiplier),
            position_size_pct,
        )
    except Exception:
        pass

    # ── Drivers / metadata ──────────────────────────────────────────────────
    key_drivers = _extract_key_drivers(meta_result, quality)
    regime = str(engine_result.get("regime", meta_result.get("regime", "UNKNOWN")))
    conflict_score = _safe_float(engine_result.get("conflict_score"), 0.0)

    # If filter blocks, surface NEUTRAL direction to consumers
    if not filter_result.should_trade:
        final_direction = "NEUTRAL"
    else:
        final_direction = direction

    return {
        "direction": final_direction,
        "signal": signal,
        "confidence": confidence,
        "p_up": float(p_up),
        "p_down": float(p_down),
        "p_neutral": float(p_neutral),
        "conviction_level": (
            quality.conviction_level.value
            if isinstance(quality.conviction_level, ConvictionLevel)
            else str(quality.conviction_level)
        ),
        "should_trade": bool(filter_result.should_trade),
        "position_size_pct": position_size_pct,
        "position_size_usd": position_size_usd,
        "kelly_fraction": float(kelly_fraction),
        "options_flow_toxicity_mult": float(toxicity_multiplier),
        "shadow_delta_mult": float(shadow_delta_multiplier),
        "combined_options_mult": float(combined_options_multiplier),
        "toxicity_metadata": toxicity_mult_dict,
        "shadow_delta_metadata": shadow_delta_dict,
        "key_drivers": key_drivers,
        "regime": regime,
        "conflict_score": conflict_score,
        "filter_reason": filter_result.filter_reason,
        "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
    }
