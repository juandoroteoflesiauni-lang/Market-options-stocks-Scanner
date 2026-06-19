"""Confidence-based fractional Kelly and volatility regime sizing. # [PD-3][TH]"""

from __future__ import annotations

import os

from backend.models.options_strategy import NormalizedFeatures
from backend.services.options_strategy._scoring import clamp01

_DEFAULT_KELLY_FRACTION = 0.5
_DEFAULT_KELLY_MAX = 0.25
_DEFAULT_CONFIDENCE_FLOOR = 0.35


def kelly_fraction(
    features: NormalizedFeatures,
    *,
    win_rate: float | None = None,
    win_loss_ratio: float | None = None,
    fractional: float | None = None,
) -> float:
    """Fractional Kelly f* = (p*b - q) / b scaled by confidence.

    Args:
        features: Normalized signal features.
        win_rate: Optional explicit win probability ``p`` in [0, 1].
        win_loss_ratio: Optional payoff ratio ``b`` (avg win / avg loss).
        fractional: Kelly fraction (default 0.5 = half-Kelly).

    Returns:
        Kelly fraction capped in [0, OPTIONS_KELLY_MAX_FRACTION].
    """
    p = win_rate if win_rate is not None else clamp01(0.45 + features.global_confidence * 0.35)
    q = 1.0 - p
    b = (
        win_loss_ratio
        if win_loss_ratio is not None
        else max(0.5, 1.0 + features.trend_quality_score * features.structure_alignment_score)
    )
    if b <= 0:
        return 0.0
    raw = (p * b - q) / b
    if raw <= 0:
        return 0.0
    frac = (
        fractional
        if fractional is not None
        else float(os.getenv("OPTIONS_KELLY_FRACTION", str(_DEFAULT_KELLY_FRACTION)))
    )
    conf_scale = clamp01(0.5 + features.global_confidence * 0.5)
    kelly = raw * frac * conf_scale
    cap = float(os.getenv("OPTIONS_KELLY_MAX_FRACTION", str(_DEFAULT_KELLY_MAX)))
    return min(kelly, cap)


def confidence_size_multiplier(features: NormalizedFeatures) -> float:
    """Scale size by global confidence with configurable floor."""
    floor = float(os.getenv("OPTIONS_CONFIDENCE_SIZE_FLOOR", str(_DEFAULT_CONFIDENCE_FLOOR)))
    return max(floor, features.global_confidence)


def dispersion_size_multiplier(features: NormalizedFeatures) -> float:
    """Existing dispersion penalty as a size multiplier."""
    penalty = float(os.getenv("OPTIONS_DISPERSION_PENALTY", "0.35"))
    return clamp01(1.0 - features.forecast_dispersion_score * penalty)


def compute_risk_budget_pct(features: NormalizedFeatures, base_pct: float) -> float:
    """Blend base risk rule with fractional Kelly and confidence scaling."""
    kelly = kelly_fraction(features)
    conf_mult = confidence_size_multiplier(features)
    kelly_pct = kelly * 100.0
    base_scaled = base_pct * conf_mult
    blended = max(base_scaled, kelly_pct * conf_mult)
    max_cap = float(os.getenv("OPTIONS_MAX_RISK_BUDGET_PCT", str(base_pct * 2.5)))
    return max(0.0, min(blended, max_cap))


def vix_proxy_from_features(features: NormalizedFeatures) -> float | None:
    """Map IV state / expected move to a VIX-like level for regime scaling."""
    iv_map = {"cheap": 14.0, "fair": 20.0, "rich": 28.0, "extreme": 36.0}
    if features.iv_state in iv_map:
        return iv_map[features.iv_state]
    if features.expected_move_pct > 0:
        return max(10.0, min(80.0, features.expected_move_pct * 8.0))
    return None


def volatility_regime_scalar(vix_level: float | None) -> float:
    """High volatility regimes reduce notional (literature: cut size in stress)."""
    if vix_level is None or vix_level <= 0:
        return 1.0
    elevated = float(os.getenv("SIZING_VIX_ELEVATED", "25.0"))
    high = float(os.getenv("SIZING_VIX_HIGH", "30.0"))
    if vix_level >= high:
        return float(os.getenv("SIZING_REGIME_SCALAR_HIGH", "0.4"))
    if vix_level >= elevated:
        return float(os.getenv("SIZING_REGIME_SCALAR_ELEVATED", "0.65"))
    return float(os.getenv("SIZING_REGIME_SCALAR_NORMAL", "1.0"))


def atr_pct_to_vix_proxy(atr: float, price: float) -> float:
    """Translate equity ATR% into a pseudo-VIX for regime scalar."""
    if price <= 0 or atr <= 0:
        return float(os.getenv("SIZING_VIX_REFERENCE", "20.0"))
    atr_pct = (atr / price) * 100.0
    scale = float(os.getenv("SIZING_ATR_TO_VIX_SCALE", "5.0"))
    return max(10.0, min(80.0, atr_pct * scale))


def equity_confidence_multiplier(*, score: float, probability: float | None = None) -> float:
    """Confidence scalar for equity/BingX notional from decision scores."""
    if probability is not None:
        conf = probability
    elif score > 1.0:
        conf = score / 100.0
    else:
        conf = score
    floor = float(os.getenv("ALPACA_CONFIDENCE_SIZE_FLOOR", "0.4"))
    high_prob = float(os.getenv("ALPACA_HIGH_PROB_THRESHOLD", "0.85"))
    if conf >= high_prob:
        boost = 1.0 + min(0.15, (conf - high_prob) / max(1e-9, 1.0 - high_prob) * 0.15)
        return max(floor, min(1.15, conf * boost))
    return max(floor, min(1.0, conf))


def resolve_equity_buying_power_pct(
    *,
    score: float,
    probability: float | None = None,
    base_pct: float | None = None,
) -> float:
    """Buying-power % per trade; alta probabilidad escala entre 10% y 15%."""
    base = base_pct
    if base is None:
        base = float(os.getenv("ALPACA_BUYING_POWER_PCT", "0.05"))
    threshold = float(os.getenv("ALPACA_HIGH_PROB_THRESHOLD", "0.85"))
    min_hp = float(os.getenv("ALPACA_HIGH_PROB_BUYING_POWER_PCT_MIN", "0.10"))
    max_hp = float(os.getenv("ALPACA_HIGH_PROB_BUYING_POWER_PCT_MAX", "0.15"))
    prob = probability
    if prob is None:
        prob = score / 100.0 if score > 1.0 else score
    if prob >= threshold:
        span = max(1e-9, 1.0 - threshold)
        t = (prob - threshold) / span
        return min_hp + t * (max_hp - min_hp)
    return base


__all__ = [
    "atr_pct_to_vix_proxy",
    "compute_risk_budget_pct",
    "confidence_size_multiplier",
    "dispersion_size_multiplier",
    "equity_confidence_multiplier",
    "kelly_fraction",
    "resolve_equity_buying_power_pct",
    "vix_proxy_from_features",
    "volatility_regime_scalar",
]
