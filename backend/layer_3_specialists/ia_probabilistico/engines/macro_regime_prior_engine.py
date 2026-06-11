"""Macro regime prior engine.

Transforms a sparse macro snapshot into a probabilistic prior over broad market
regimes. The engine is intentionally stateless and uses only numeric inputs.
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

REGIME_KEYS = ("BULL_QUIET", "BEAR_VOLATILE", "CHAOTIC")
INPUT_KEYS = (
    "vix_spot",
    "vix_3m",
    "hy_spread",
    "ig_spread",
    "yield_2y",
    "yield_10y",
    "sp500_200ma_pct",
)
UNIFORM_PRIOR = {"BULL_QUIET": 0.33, "BEAR_VOLATILE": 0.33, "CHAOTIC": 0.34}


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        val_float = float(cast(Any, value))
        if not np.isfinite(val_float):
            return None
        return val_float
    except (TypeError, ValueError):
        return None


def _softmax(scores: list[float]) -> dict[str, float]:
    arr = np.asarray(scores, dtype=float)
    exp_scores = np.exp(arr - np.max(arr))
    probs = exp_scores / np.sum(exp_scores)
    return {regime: float(prob) for regime, prob in zip(REGIME_KEYS, probs, strict=True)}


def _dominant_regime(prior: dict[str, float]) -> str:
    return max(prior, key=lambda k: prior[k])


def get_macro_regime_prior(macro_data: dict[str, Any]) -> dict[str, Any]:
    """Compute macro-regime probabilities from a sparse macro data snapshot."""
    inputs = {key: _safe_float(macro_data.get(key)) for key in INPUT_KEYS}

    if all(value is None for value in inputs.values()):
        return {
            "macro_regime_prior": dict(UNIFORM_PRIOR),
            "macro_regime_dominant": _dominant_regime(UNIFORM_PRIOR),
            "macro_confidence": max(UNIFORM_PRIOR.values()),
            "vix_term_slope": None,
            "yield_curve": None,
            "credit_stress_score": None,
            "macro_alerts": [],
        }

    vix_spot = inputs["vix_spot"]
    vix_3m = inputs["vix_3m"]
    hy_spread = inputs["hy_spread"]
    ig_spread = inputs["ig_spread"]
    yield_2y = inputs["yield_2y"]
    yield_10y = inputs["yield_10y"]
    sp500_200ma_pct = inputs["sp500_200ma_pct"]

    vix_term_slope = None
    if vix_spot is not None and vix_3m is not None:
        vix_term_slope = vix_3m - vix_spot

    yield_curve = None
    if yield_2y is not None and yield_10y is not None:
        yield_curve = yield_10y - yield_2y

    hy_ig_ratio = None
    credit_stress_score = None
    if hy_spread is not None and ig_spread is not None:
        hy_ig_ratio = hy_spread / (ig_spread + 0.001)
        if np.isfinite(hy_ig_ratio):
            normalized = np.asarray([hy_spread / 500.0, hy_ig_ratio / 10.0], dtype=float)
            credit_stress_score = float(np.clip(np.mean(normalized), 0.0, 1.0))

    score_bull = 0.0
    if vix_term_slope is not None and vix_term_slope > 0:
        score_bull += 0.30
    if yield_curve is not None and yield_curve > 0:
        score_bull += 0.25
    if credit_stress_score is not None and credit_stress_score < 0.35:
        score_bull += 0.25
    if sp500_200ma_pct is not None and sp500_200ma_pct > 0:
        score_bull += 0.20

    score_bear = 0.0
    if vix_term_slope is not None and vix_term_slope < -2:
        score_bear += 0.35
    if credit_stress_score is not None and credit_stress_score > 0.65:
        score_bear += 0.35
    if yield_curve is not None and yield_curve < -0.20:
        score_bear += 0.30

    score_chaotic = 0.0
    if (
        yield_curve is not None
        and credit_stress_score is not None
        and yield_curve < -0.20
        and credit_stress_score > 0.50
    ):
        score_chaotic += 0.40
    if vix_spot is not None and vix_spot > 30:
        score_chaotic += 0.30
    if (
        vix_term_slope is not None
        and credit_stress_score is not None
        and abs(vix_term_slope) < 0.5
        and credit_stress_score > 0.40
    ):
        score_chaotic += 0.30

    prior = _softmax([score_bull, score_bear, score_chaotic])
    dominant = _dominant_regime(prior)

    alerts: list[str] = []
    if vix_term_slope is not None and vix_term_slope < -2:
        alerts.append("VIX backwardation severa")
    if yield_curve is not None and yield_curve < -0.20:
        alerts.append("Yield curve invertida")
    if credit_stress_score is not None and credit_stress_score > 0.70:
        alerts.append("Credit stress elevado")
    if vix_spot is not None and vix_spot > 30:
        alerts.append("VIX elevado (>30)")
    if sp500_200ma_pct is not None and sp500_200ma_pct < -5:
        alerts.append("SP500 bajo MA200")

    return {
        "macro_regime_prior": prior,
        "macro_regime_dominant": dominant,
        "macro_confidence": float(prior[dominant]),
        "vix_term_slope": vix_term_slope,
        "yield_curve": yield_curve,
        "credit_stress_score": credit_stress_score,
        "macro_alerts": alerts,
    }


if __name__ == "__main__":
    sample = {
        "vix_spot": 34.0,
        "vix_3m": 30.0,
        "hy_spread": 600.0,
        "ig_spread": 90.0,
        "yield_2y": 4.8,
        "yield_10y": 4.4,
        "sp500_200ma_pct": -6.5,
    }
    logger.info("Manual macro regime prior sample: %s", get_macro_regime_prior(sample))
