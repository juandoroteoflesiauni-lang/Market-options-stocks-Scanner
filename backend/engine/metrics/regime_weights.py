"""
backend/engine/metrics/regime_weights.py
Sector: Options / Regime-Based Weighting Engine
[ARCH-1, PD-4]

Theoretical basis:
    Dynamically adjusts factor and model weights in the ensemble layer based on
    the active market volatility and trend regime (Bull Quiet, Bear Volatile, etc.).
"""

from __future__ import annotations

import logging
import math
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.regime_weights")

_REPUTATION_LR = 0.20
_REPUTATION_FLOOR = 0.50
_REPUTATION_CEIL = 1.50

ALL_MOTORS: tuple[str, ...] = (
    "tail_risk",
    "gamma_flip",
    "vsa_forecast",
    "sentiment",
    "fear_greed",
    "cross_asset",
    "squeeze",
    "shadow_delta",
    "zomma",
    "speed_instability",
    "volatility_skew",
    "risk_neutral_density",
    "dealer_flow_dynamics",
    "options_flow_toxicity",
    "macro_regime_prior",
    "orchestrator",
)

ENSEMBLE_REGIMES: tuple[str, ...] = (
    "bull_quiet",
    "bull_volatile",
    "bear_quiet",
    "bear_volatile",
    "transition",
    "chaotic",
)


class MarketRegime(str, Enum):
    """Market regime classification."""

    BULL_QUIET = "bull_quiet"
    BULL_VOLATILE = "bull_volatile"
    BEAR_QUIET = "bear_quiet"
    BEAR_VOLATILE = "bear_volatile"
    TRANSITION = "transition"
    CHAOTIC = "chaotic"


def _normalised(weights: dict[str, float]) -> dict[str, float]:
    """Return a copy of weights with values summing to 1.0."""
    total = float(sum(max(0.0, v) for v in weights.values()))
    if total <= 0:
        return dict(weights)
    return {k: max(0.0, v) / total for k, v in weights.items()}


def default_meta_learner_weights() -> dict[str, float]:
    """Default meta-learner trust factor weights by regime."""
    return {
        "bull_quiet": 0.60,
        "bull_volatile": 0.45,
        "bear_quiet": 0.50,
        "bear_volatile": 0.40,
        "transition": 0.50,
        "chaotic": 0.30,
    }


def default_motor_matrix() -> dict[str, dict[str, float]]:
    """Default hand-tuned motor weights baseline matrix."""
    return {
        "bull_quiet": {
            "tail_risk": 0.06,
            "gamma_flip": 0.07,
            "vsa_forecast": 0.10,
            "sentiment": 0.10,
            "fear_greed": 0.06,
            "cross_asset": 0.06,
            "squeeze": 0.05,
            "shadow_delta": 0.05,
            "zomma": 0.04,
            "speed_instability": 0.04,
            "volatility_skew": 0.05,
            "risk_neutral_density": 0.06,
            "dealer_flow_dynamics": 0.07,
            "options_flow_toxicity": 0.05,
            "macro_regime_prior": 0.06,
            "orchestrator": 0.08,
        },
        "bull_volatile": {
            "tail_risk": 0.08,
            "gamma_flip": 0.09,
            "vsa_forecast": 0.08,
            "sentiment": 0.08,
            "fear_greed": 0.06,
            "cross_asset": 0.06,
            "squeeze": 0.05,
            "shadow_delta": 0.06,
            "zomma": 0.05,
            "speed_instability": 0.05,
            "volatility_skew": 0.06,
            "risk_neutral_density": 0.07,
            "dealer_flow_dynamics": 0.08,
            "options_flow_toxicity": 0.06,
            "macro_regime_prior": 0.05,
            "orchestrator": 0.08,
        },
        "bear_quiet": {
            "tail_risk": 0.10,
            "gamma_flip": 0.08,
            "vsa_forecast": 0.06,
            "sentiment": 0.05,
            "fear_greed": 0.06,
            "cross_asset": 0.07,
            "squeeze": 0.04,
            "shadow_delta": 0.05,
            "zomma": 0.05,
            "speed_instability": 0.04,
            "volatility_skew": 0.08,
            "risk_neutral_density": 0.09,
            "dealer_flow_dynamics": 0.08,
            "options_flow_toxicity": 0.06,
            "macro_regime_prior": 0.06,
            "orchestrator": 0.08,
        },
        "bear_volatile": {
            "tail_risk": 0.12,
            "gamma_flip": 0.08,
            "vsa_forecast": 0.05,
            "sentiment": 0.04,
            "fear_greed": 0.05,
            "cross_asset": 0.06,
            "squeeze": 0.04,
            "shadow_delta": 0.05,
            "zomma": 0.04,
            "speed_instability": 0.05,
            "volatility_skew": 0.08,
            "risk_neutral_density": 0.09,
            "dealer_flow_dynamics": 0.10,
            "options_flow_toxicity": 0.09,
            "macro_regime_prior": 0.05,
            "orchestrator": 0.06,
        },
        "transition": {
            "tail_risk": 0.07,
            "gamma_flip": 0.07,
            "vsa_forecast": 0.07,
            "sentiment": 0.06,
            "fear_greed": 0.06,
            "cross_asset": 0.06,
            "squeeze": 0.06,
            "shadow_delta": 0.06,
            "zomma": 0.05,
            "speed_instability": 0.05,
            "volatility_skew": 0.06,
            "risk_neutral_density": 0.07,
            "dealer_flow_dynamics": 0.07,
            "options_flow_toxicity": 0.06,
            "macro_regime_prior": 0.06,
            "orchestrator": 0.07,
        },
        "chaotic": {
            "tail_risk": 0.08,
            "gamma_flip": 0.06,
            "vsa_forecast": 0.04,
            "sentiment": 0.04,
            "fear_greed": 0.05,
            "cross_asset": 0.06,
            "squeeze": 0.04,
            "shadow_delta": 0.05,
            "zomma": 0.04,
            "speed_instability": 0.06,
            "volatility_skew": 0.06,
            "risk_neutral_density": 0.07,
            "dealer_flow_dynamics": 0.10,
            "options_flow_toxicity": 0.12,
            "macro_regime_prior": 0.08,
            "orchestrator": 0.05,
        },
    }


class RegimeWeights(BaseModel):
    """Factor weights for a specific regime (frozen)."""

    model_config = ConfigDict(frozen=True)

    regime: MarketRegime
    weights: dict[str, float]
    description: str


class RegimeWeightConfig(BaseModel):
    """Adaptive-weight configuration for the full ensemble (frozen)."""

    model_config = ConfigDict(frozen=True)

    MOTOR_WEIGHTS_BY_REGIME: dict[str, dict[str, float]] = Field(
        default_factory=default_motor_matrix
    )
    META_LEARNER_WEIGHT_BY_REGIME: dict[str, float] = Field(
        default_factory=default_meta_learner_weights
    )

    @model_validator(mode="after")
    def validate_and_normalise(self) -> RegimeWeightConfig:
        normalized_matrix = {r: _normalised(w) for r, w in self.MOTOR_WEIGHTS_BY_REGIME.items()}
        object.__setattr__(self, "MOTOR_WEIGHTS_BY_REGIME", normalized_matrix)
        return self


class RegimeWeightingEngine:
    """
    Dynamic factor weighting based on market regime.
    Purely stateless.
    """

    REGIME_WEIGHTS: ClassVar[dict[MarketRegime, RegimeWeights]] = {
        MarketRegime.BULL_QUIET: RegimeWeights(
            regime=MarketRegime.BULL_QUIET,
            weights={
                "momentum": 0.25,
                "strength": 0.20,
                "volatility": 0.10,
                "put_call": 0.10,
                "credit": 0.10,
                "safe_haven": 0.05,
                "event_risk": 0.20,
            },
            description="Bull market: momentum and strength overweight",
        ),
        MarketRegime.BEAR_QUIET: RegimeWeights(
            regime=MarketRegime.BEAR_QUIET,
            weights={
                "momentum": 0.05,
                "strength": 0.10,
                "volatility": 0.20,
                "put_call": 0.15,
                "credit": 0.15,
                "safe_haven": 0.25,
                "event_risk": 0.10,
            },
            description="Bear market: defensive factors overweight",
        ),
        MarketRegime.BEAR_VOLATILE: RegimeWeights(
            regime=MarketRegime.BEAR_VOLATILE,
            weights={
                "momentum": 0.05,
                "strength": 0.05,
                "volatility": 0.25,
                "put_call": 0.15,
                "credit": 0.10,
                "safe_haven": 0.30,
                "event_risk": 0.10,
            },
            description="Bear volatile: maximum defense",
        ),
        MarketRegime.TRANSITION: RegimeWeights(
            regime=MarketRegime.TRANSITION,
            weights={
                "momentum": 0.15,
                "strength": 0.15,
                "volatility": 0.15,
                "put_call": 0.15,
                "credit": 0.15,
                "safe_haven": 0.15,
                "event_risk": 0.10,
            },
            description="Transition: balanced weights",
        ),
        MarketRegime.CHAOTIC: RegimeWeights(
            regime=MarketRegime.CHAOTIC,
            weights={
                "momentum": 0.05,
                "strength": 0.10,
                "volatility": 0.20,
                "put_call": 0.10,
                "credit": 0.10,
                "safe_haven": 0.15,
                "event_risk": 0.30,
            },
            description="Chaotic: event risk and volatility overweight",
        ),
    }

    def get_regime_weights(self, regime: MarketRegime | None = None) -> RegimeWeights:
        """Get factor weights for the specified regime."""
        if regime is None:
            regime = MarketRegime.TRANSITION
        return self.REGIME_WEIGHTS.get(regime, self.REGIME_WEIGHTS[MarketRegime.TRANSITION])

    def classify_regime(
        self,
        vix: float,
        spy_ma50: float,
        spy_ma200: float,
        spy_price: float,
        vix_ma50: float | None = None,
    ) -> Result[MarketRegime]:
        """Classifies the market regime."""
        if vix <= 0.0:
            return Result.failure(reason="vix must be positive")
        if spy_ma50 <= 0.0 or spy_ma200 <= 0.0 or spy_price <= 0.0:
            return Result.failure(reason="spy_ma50, spy_ma200, and spy_price must be positive")
        if vix_ma50 is not None and vix_ma50 <= 0.0:
            return Result.failure(reason="vix_ma50 must be positive")

        # Trend classification
        price_vs_ma50 = (spy_price - spy_ma50) / spy_ma50
        price_vs_ma200 = (spy_price - spy_ma200) / spy_ma200

        is_uptrend = price_vs_ma50 > 0.0 and price_vs_ma200 > 0.0
        is_downtrend = price_vs_ma50 < 0.0 and price_vs_ma200 < 0.0

        # Volatility classification
        vix_high = vix > 25.0
        vix_above_avg = vix > vix_ma50 * 1.2 if vix_ma50 is not None else vix > 20.0

        is_high_vol = vix_high or vix_above_avg

        # Classify
        if is_uptrend and not is_high_vol:
            regime = MarketRegime.BULL_QUIET
        elif is_uptrend and is_high_vol:
            regime = MarketRegime.BULL_VOLATILE
        elif is_downtrend and is_high_vol:
            regime = MarketRegime.BEAR_VOLATILE
        elif is_downtrend and not is_high_vol:
            regime = MarketRegime.BEAR_QUIET
        elif abs(price_vs_ma50) < 0.02:  # Within 2% of MA50
            regime = MarketRegime.TRANSITION
        else:
            regime = MarketRegime.CHAOTIC

        return Result.success(regime)

    def get_adaptive_weights(
        self,
        vix: float,
        spy_ma50: float,
        spy_ma200: float,
        spy_price: float,
        vix_ma50: float | None = None,
    ) -> Result[dict[str, float]]:
        """Get adaptive weights based on current market classification."""
        regime_res = self.classify_regime(vix, spy_ma50, spy_ma200, spy_price, vix_ma50)
        if regime_res.is_failure:
            return Result.failure(reason=regime_res.reason)
        regime = regime_res.unwrap()
        weights = self.get_regime_weights(regime)
        return Result.success(weights.weights)


# ── Ensemble helpers ─────────────────────────────────────────────────────────


def get_optimal_weights_for_regime(
    regime: str,
    regime_probs: dict[str, float] | None = None,
    config: RegimeWeightConfig | None = None,
) -> Result[dict[str, float]]:
    """Compute the per-motor weight vector for the current regime."""
    cfg = config or RegimeWeightConfig()
    matrix = cfg.MOTOR_WEIGHTS_BY_REGIME

    if regime not in ENSEMBLE_REGIMES:
        return Result.failure(reason=f"Unknown regime: {regime}")

    if regime_probs:
        accum: dict[str, float] = {motor: 0.0 for motor in ALL_MOTORS}
        prob_sum = sum(p for p in regime_probs.values() if p is not None) or 0.0
        if prob_sum <= 0.0:
            return Result.success(_normalised(matrix.get(regime, matrix["transition"])))
        for r, p in regime_probs.items():
            if p is None or p <= 0.0:
                continue
            row = matrix.get(r, matrix["transition"])
            for motor, w in row.items():
                accum[motor] = accum.get(motor, 0.0) + (p / prob_sum) * w
        return Result.success(_normalised(accum))

    return Result.success(_normalised(matrix.get(regime, matrix["transition"])))


def _regime_certainty(regime_probs: dict[str, float] | None) -> float:
    """Calculates certainty entropy metric from probabilities [0, 1]."""
    if not regime_probs:
        return 1.0
    probs = [p for p in regime_probs.values() if p and p > 0.0]
    if not probs:
        return 1.0
    s = sum(probs)
    if s <= 0.0:
        return 1.0
    norm = [p / s for p in probs]
    entropy = -sum(p * math.log(p) for p in norm if p > 0.0)
    max_entropy = math.log(len(norm)) if len(norm) > 1 else 1.0
    return float(max(0.0, min(1.0, 1.0 - entropy / max_entropy)))


def blend_meta_with_engines(
    meta_signal: dict[str, float],
    engine_signal: dict[str, float],
    regime: str,
    regime_probs: dict[str, float] | None = None,
    config: RegimeWeightConfig | None = None,
) -> Result[dict[str, float]]:
    """Combines meta-learner output with orchestrator signal."""
    if regime not in ENSEMBLE_REGIMES:
        return Result.failure(reason=f"Unknown regime: {regime}")

    cfg = config or RegimeWeightConfig()
    base = cfg.META_LEARNER_WEIGHT_BY_REGIME.get(regime, 0.5)
    certainty = _regime_certainty(regime_probs)

    meta_weight = float(max(0.0, min(1.0, base * certainty)))
    engine_weight = float(1.0 - meta_weight)

    m_sig = float(meta_signal.get("signal", 0.0))
    e_sig = float(engine_signal.get("signal", 0.0))
    m_conf = float(meta_signal.get("confidence", 0.0))
    e_conf = float(engine_signal.get("confidence", 0.0))

    final_signal = max(-1.0, min(1.0, meta_weight * m_sig + engine_weight * e_sig))
    final_confidence = max(0.0, min(1.0, meta_weight * m_conf + engine_weight * e_conf))

    return Result.success(
        {
            "signal": final_signal,
            "confidence": final_confidence,
            "meta_weight": meta_weight,
            "engine_weight": engine_weight,
            "regime_certainty": certainty,
        }
    )


def update_weights_from_performance(
    motor_name: str,
    recent_accuracy: float,
    regime: str,
    current_reputation: dict[str, dict[str, float]],
) -> Result[dict[str, dict[str, float]]]:
    """Pure reputation dictionary update on accuracy feedback."""
    if motor_name not in ALL_MOTORS:
        return Result.failure(reason=f"Unknown motor: {motor_name}")
    if regime not in ENSEMBLE_REGIMES:
        return Result.failure(reason=f"Unknown regime: {regime}")
    if not 0.0 <= recent_accuracy <= 1.0:
        return Result.failure(reason=f"recent_accuracy must be in [0, 1]; got {recent_accuracy}")

    # Copy reputation to maintain stateless pure updates
    new_reputation = {m: dict(regs) for m, regs in current_reputation.items()}
    motor_rep = dict(new_reputation.get(motor_name, {}))
    old_mult = float(motor_rep.get(regime, 1.0))

    target = 0.5 + float(recent_accuracy)
    new_mult = (1.0 - _REPUTATION_LR) * old_mult + _REPUTATION_LR * target
    new_mult = float(max(_REPUTATION_FLOOR, min(_REPUTATION_CEIL, new_mult)))

    motor_rep[regime] = new_mult
    new_reputation[motor_name] = motor_rep

    return Result.success(new_reputation)
