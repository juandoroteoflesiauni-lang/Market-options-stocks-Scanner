"""Risk & Sizing Engines v2 — uses options metrics already on the analysis path."""

from __future__ import annotations

from typing import Any

from backend.config.bingx_risk_sizing_v2_calibration import (
    BACKWARDATION_RATIO,
    BACKWARDATION_SIZE_CAP,
    CHEX_REF_DEFAULT,
    DTE0_DOMINANT_PCT,
    IVR_HIGH_TAILWIND_CAP,
    IVR_VEX_OVERRIDE_CAP,
    RISK_SIZING_MAX_MULT,
    RISK_SIZING_MIN_MULT,
    VEX_CHEX_CHEX_WEIGHT,
    VEX_CHEX_HEADWIND,
    VEX_CHEX_TAILWIND,
    VEX_CHEX_VEX_WEIGHT,
    VEX_REF_DEFAULT,
    chex_ref,
    dark_pool_bearish_penalty_mult,
    dark_pool_bullish_bonus_cap,
    dark_pool_min_confidence,
    vex_ref,
)
from backend.config.logger_setup import get_logger
from backend.services.bingx_candidate_analysis import BingXCandidateAnalysis
from backend.services.calibration.bayesian_kelly_sizer import bayesian_kelly_for_decide

logger = get_logger(__name__)


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
        return out if abs(out) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _inner_metrics(analysis: BingXCandidateAnalysis) -> dict[str, Any]:
    metrics = analysis.options.metrics or {}
    if not isinstance(metrics, dict):
        return {}
    nested = metrics.get("metrics")
    return nested if isinstance(nested, dict) else metrics


def _vex_chex_flow_score(inner: dict[str, Any]) -> tuple[float, str]:
    """Motor 12 — VEX-CHEX composite flow score in [-1, 1]."""
    net_vex = _safe_float(inner.get("total_vex")) or 0.0
    charm_raw = inner.get("charm_flow")
    net_chex = (_safe_float(charm_raw) or 0.0) if isinstance(charm_raw, int | float) else 0.0
    vex_r = vex_ref() or VEX_REF_DEFAULT
    chex_r = chex_ref() or CHEX_REF_DEFAULT
    vex_score = max(-1.0, min(1.0, net_vex / max(abs(vex_r), 1.0)))
    chex_score = max(-1.0, min(1.0, net_chex / max(abs(chex_r), 1.0)))
    flow = VEX_CHEX_VEX_WEIGHT * vex_score + VEX_CHEX_CHEX_WEIGHT * chex_score
    if flow > VEX_CHEX_TAILWIND:
        bias = "TAILWIND_LONG"
    elif flow < VEX_CHEX_HEADWIND:
        bias = "HEADWIND_LONG"
    else:
        bias = "NEUTRAL"
    return flow, bias


def _iv_rank_vex_mult(inner: dict[str, Any]) -> float:
    """Motor 2 — IV rank inverse sizing with VEX override."""
    ivr = _safe_float(inner.get("iv_rank_hv_rolling"))
    if ivr is None:
        ivr = _safe_float(inner.get("iv_rank_cross_expiry"))
    if ivr is None:
        ivr = 50.0
    if ivr > 1.0:
        ivr = ivr / 100.0
    size_mult = 1.0 - ivr * 0.70
    net_vex = _safe_float(inner.get("total_vex")) or 0.0
    vex_threshold = abs(vex_ref()) * 1.5
    if net_vex < -vex_threshold:
        size_mult = min(size_mult, IVR_VEX_OVERRIDE_CAP)
    elif net_vex > vex_threshold and ivr < 0.30:
        size_mult = min(IVR_HIGH_TAILWIND_CAP, size_mult * 1.20)
    return max(0.15, min(1.30, size_mult))


def _vrp_term_mult(inner: dict[str, Any]) -> float:
    """Motor 8 — VRP + backwardation cap."""
    vrp = _safe_float(inner.get("vrp"))
    iv_1w = _safe_float(inner.get("iv_rank_cross_expiry"))
    iv_1m = _safe_float(inner.get("iv_percentile_cross_term"))
    if vrp is not None and vrp < 0:
        return 0.0
    if iv_1w is not None and iv_1m is not None and iv_1m > 0:
        ratio = iv_1w / iv_1m if iv_1w > 1 else (iv_1w * 100) / max(iv_1m, 1e-9)
        if ratio > BACKWARDATION_RATIO:
            return BACKWARDATION_SIZE_CAP
    if vrp is not None and vrp > 0.05:
        return min(1.20, 1.0 + (vrp - 0.05) * 4.0)
    return 1.0


def _gamma_survival_mult(inner: dict[str, Any], direction: str) -> float:
    """Motor 10 — gamma regime survival sizing (partial; full block in decide)."""
    net_gex = _safe_float(inner.get("net_gex_total")) or 0.0
    squeeze = _safe_float(inner.get("squeeze_probability")) or 0.0
    dte0_proxy = squeeze  # proxy when 0DTE% unavailable
    if net_gex < 0 and dte0_proxy > DTE0_DOMINANT_PCT:
        return 0.10
    if net_gex < 0:
        return 0.60
    return 1.0


def _dark_pool_mult(analysis: BingXCandidateAnalysis, direction: str) -> float:
    """Motor ⑭ — dark-pool directional confirmation sizing.

    Confirms the trade direction → bonus (capped); contradicts → penalty.
    Neutral (1.0) when the block is unavailable or below the confidence floor.
    """
    dp = getattr(analysis, "dark_pool", None)
    if dp is None or getattr(dp, "status", "unavailable") != "available":
        return 1.0
    confidence = float(getattr(dp, "confidence", 0.0) or 0.0)
    if confidence < dark_pool_min_confidence():
        return 1.0

    bias = str(getattr(dp, "bias", "NEUTRAL")).upper()
    bonus_cap = dark_pool_bullish_bonus_cap()
    penalty = dark_pool_bearish_penalty_mult()
    dir_up = direction.upper()

    if dir_up == "LONG":
        if bias == "BULLISH":
            return min(bonus_cap, 1.0 + confidence * 0.15)
        if bias == "BEARISH":
            return penalty
    elif dir_up == "SHORT":
        if bias == "BEARISH":
            return min(bonus_cap, 1.0 + confidence * 0.15)
        if bias == "BULLISH":
            return penalty
    return 1.0


def compute_risk_sizing_v2(
    analysis: BingXCandidateAnalysis,
    *,
    direction: str = "FLAT",
) -> dict[str, Any]:
    """Aggregate v2 risk/sizing motors into one multiplier and diagnostics."""
    inner = _inner_metrics(analysis)
    if not inner:
        return {"ok": False, "multiplier": 1.0, "reason": "no_options_metrics"}

    flow_score, flow_bias = _vex_chex_flow_score(inner)
    iv_mult = _iv_rank_vex_mult(inner)
    vrp_mult = _vrp_term_mult(inner)
    gamma_mult = _gamma_survival_mult(inner, direction)

    if vrp_mult == 0.0:
        return {
            "ok": True,
            "multiplier": 0.0,
            "reason": "negative_vrp",
            "flow_score": round(flow_score, 4),
            "flow_bias": flow_bias,
            "iv_rank_mult": round(iv_mult, 4),
            "vrp_mult": 0.0,
            "gamma_mult": round(gamma_mult, 4),
            "flow_mult": 1.0,
        }

    flow_mult = 1.0
    if direction == "LONG":
        if flow_bias == "TAILWIND_LONG":
            flow_mult = min(1.20, 1.0 + flow_score * 0.25)
        elif flow_bias == "HEADWIND_LONG":
            flow_mult = max(0.50, 1.0 + flow_score * 0.30)
        elif abs(flow_score) < 0.20:
            flow_mult = 0.80

    combined = iv_mult * vrp_mult * gamma_mult * flow_mult

    # Motor ⑭ — dark-pool directional confirmation (neutral when unavailable).
    dark_pool_mult = _dark_pool_mult(analysis, direction)
    combined *= dark_pool_mult

    # Motor ⑬ — Bayesian Kelly from the trade journal (route-bucketed). Neutral
    # (1.0) when degraded; applied before the final composite clamp.
    bk = bayesian_kelly_for_decide(route="BINGX")
    combined *= bk.multiplier

    combined = max(RISK_SIZING_MIN_MULT, min(RISK_SIZING_MAX_MULT, combined))

    result = {
        "ok": True,
        "multiplier": round(combined, 4),
        "flow_score": round(flow_score, 4),
        "flow_bias": flow_bias,
        "iv_rank_mult": round(iv_mult, 4),
        "vrp_mult": round(vrp_mult, 4),
        "gamma_mult": round(gamma_mult, 4),
        "flow_mult": round(flow_mult, 4),
        "dark_pool_mult": round(dark_pool_mult, 4),
        "bayesian_kelly_mult": round(bk.multiplier, 4),
        "bayesian_kelly_fraction": bk.fraction,
    }
    logger.debug(
        "bingx_risk_sizing_v2 venue=%s mult=%.4f flow=%s iv=%.3f vrp=%.3f gamma=%.3f",
        analysis.venue_symbol,
        combined,
        flow_bias,
        iv_mult,
        vrp_mult,
        gamma_mult,
    )
    return result


def risk_sizing_multiplier(analysis: BingXCandidateAnalysis, *, direction: str = "FLAT") -> float:
    """Public helper — returns sizing multiplier only."""
    payload = compute_risk_sizing_v2(analysis, direction=direction)
    if payload.get("multiplier") is None:
        return 1.0
    return float(payload["multiplier"])
