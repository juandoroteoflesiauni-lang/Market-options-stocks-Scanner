"""
backend/layer_3_specialists/ia_probabilistico/engines/regime_weights.py
════════════════════════════════════════════════════════════════════════════════
Regime-Based Weighting Engine — Dynamic factor weights by market regime.

Adjusts Fear & Greed factor weights based on current market regime:
- Bull Quiet: Low volatility, upward trend → Momentum overweight
- Bear Volatile: High volatility, downward trend → Safe haven overweight
- Transition: Mixed signals → Equal weights
- Chaotic: High uncertainty → Event risk overweight
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import cast, Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Market regime classification."""
    BULL_QUIET = "bull_quiet"  # Low vol, uptrend
    BULL_VOLATILE = "bull_volatile"  # High vol, uptrend
    BEAR_QUIET = "bear_quiet"  # Low vol, downtrend
    BEAR_VOLATILE = "bear_volatile"  # High vol, downtrend
    TRANSITION = "transition"  # Mixed signals
    CHAOTIC = "chaotic"  # High uncertainty


@dataclass
class RegimeWeights:
    """Factor weights for a specific regime."""
    regime: MarketRegime
    weights: dict[str, float]
    description: str


class RegimeWeightingEngine:
    """
    Dynamic factor weighting based on market regime.
    """

    # Default weights by regime
    REGIME_WEIGHTS: dict[MarketRegime, RegimeWeights] = {
        MarketRegime.BULL_QUIET: RegimeWeights(
            regime=MarketRegime.BULL_QUIET,
            weights={
                "momentum": 0.25,      # Overweight momentum in bull
                "strength": 0.20,       # Strength important
                "volatility": 0.10,     # Low concern in bull
                "put_call": 0.10,
                "credit": 0.10,
                "safe_haven": 0.05,     # Underweight safe havens
                "event_risk": 0.20,
            },
            description="Bull market: momentum and strength overweight"
        ),
        MarketRegime.BEAR_QUIET: RegimeWeights(
            regime=MarketRegime.BEAR_QUIET,
            weights={
                "momentum": 0.05,       # Underweight momentum
                "strength": 0.10,
                "volatility": 0.20,      # Volatility important
                "put_call": 0.15,
                "credit": 0.15,
                "safe_haven": 0.25,     # Overweight safe havens
                "event_risk": 0.10,
            },
            description="Bear market: defensive factors overweight"
        ),
        MarketRegime.BEAR_VOLATILE: RegimeWeights(
            regime=MarketRegime.BEAR_VOLATILE,
            weights={
                "momentum": 0.05,
                "strength": 0.05,
                "volatility": 0.25,      # Very high vol weight
                "put_call": 0.15,
                "credit": 0.10,
                "safe_haven": 0.30,     # Maximum safe haven
                "event_risk": 0.10,
            },
            description="Bear volatile: maximum defense"
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
            description="Transition: balanced weights"
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
                "event_risk": 0.30,     # Event risk most important
            },
            description="Chaotic: event risk and volatility overweight"
        ),
    }

    def __init__(self) -> None:
        self._current_regime: MarketRegime | None = None

    def get_regime_weights(
        self,
        regime: MarketRegime | None = None
    ) -> RegimeWeights:
        """
        Get factor weights for current or specified regime.

        Args:
            regime: Market regime (default: current)

        Returns:
            RegimeWeights with factor weights
        """
        if regime is None:
            regime = self._current_regime or MarketRegime.TRANSITION

        return self.REGIME_WEIGHTS.get(
            regime,
            self.REGIME_WEIGHTS[MarketRegime.TRANSITION]
        )

    def classify_regime(
        self,
        vix: float,
        spy_ma50: float,
        spy_ma200: float,
        spy_price: float,
        vix_ma50: float | None = None,
    ) -> MarketRegime:
        """
        Classify current market regime.

        Args:
            vix: Current VIX level
            spy_ma50: SPY 50-day MA
            spy_ma200: SPY 200-day MA
            spy_price: Current SPY price
            vix_ma50: VIX 50-day MA (optional)

        Returns:
            Classified market regime
        """
        # Trend classification
        price_vs_ma50 = (spy_price - spy_ma50) / spy_ma50
        price_vs_ma200 = (spy_price - spy_ma200) / spy_ma200

        is_uptrend = price_vs_ma50 > 0 and price_vs_ma200 > 0
        is_downtrend = price_vs_ma50 < 0 and price_vs_ma200 < 0

        # Volatility classification
        vix_high = vix > 25
        if vix_ma50:
            vix_above_avg = vix > vix_ma50 * 1.2
        else:
            vix_above_avg = vix > 20

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

        self._current_regime = regime
        return regime

    def get_adaptive_weights(
        self,
        vix: float,
        spy_ma50: float,
        spy_ma200: float,
        spy_price: float,
        vix_ma50: float | None = None,
    ) -> dict[str, float]:
        """
        Get adaptive factor weights based on current regime.

        Args:
            vix: Current VIX
            spy_ma50: SPY 50-day MA
            spy_ma200: SPY 200-day MA
            spy_price: Current SPY price
            vix_ma50: VIX 50-day MA

        Returns:
            Dict of factor weights
        """
        regime = self.classify_regime(vix, spy_ma50, spy_ma200, spy_price, vix_ma50)
        regime_weights = self.get_regime_weights(regime)

        logger.info(f"Regime: {regime.value}, weights: {regime_weights.weights}")

        return regime_weights.weights


# Global instance
_engine: RegimeWeightingEngine | None = None


def get_regime_engine() -> RegimeWeightingEngine:
    """Get or create regime weighting engine."""
    global _engine
    if _engine is None:
        _engine = RegimeWeightingEngine()
    return _engine


# ════════════════════════════════════════════════════════════════════════════════
# Ensemble-aware regime weighting
# ════════════════════════════════════════════════════════════════════════════════
#
# Layer added on top of the legacy RegimeWeightingEngine to manage adaptive
# weights for ALL motors in the system (including the new probabilistic engines)
# and to blend the meta-learner's ensemble output with the orchestrator signal.
#
# The legacy code above remains untouched for backward compatibility with the
# Fear & Greed factor weighting consumers.
# ════════════════════════════════════════════════════════════════════════════════

import json  # noqa: E402
import math  # noqa: E402
from pathlib import Path  # noqa: E402

# Canonical motor identifiers used everywhere in the ensemble layer.
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

# Regimes used by the ensemble layer (string keys for JSON friendliness).
ENSEMBLE_REGIMES: tuple[str, ...] = (
    "bull_quiet",
    "bull_volatile",
    "bear_quiet",
    "bear_volatile",
    "transition",
    "chaotic",
)


def _normalised(weights: dict[str, float]) -> dict[str, float]:
    """Return a copy of `weights` with values summing to 1.0 (or unchanged if total=0)."""
    total = float(sum(max(0.0, v) for v in weights.values()))
    if total <= 0:
        return dict(weights)
    return {k: max(0.0, v) / total for k, v in weights.items()}


@dataclass
class RegimeWeightConfig:
    """
    Adaptive-weight configuration for the full ensemble.

    MOTOR_WEIGHTS_BY_REGIME:
        regime → {motor_name → weight}. Each regime row is normalised so the
        engine-level weights sum to 1.0.

    META_LEARNER_WEIGHT_BY_REGIME:
        regime → meta-learner trust factor in [0, 1]. Higher values give more
        authority to the trained ensemble model versus the rule-based orchestrator.

        Rationale:
          BULL_QUIET     → 0.60: clean historical signal, ML model thrives.
          TRANSITION     → 0.50: balanced.
          BULL_VOLATILE  → 0.45: trend persists but noise rises.
          BEAR_QUIET     → 0.50.
          BEAR_VOLATILE  → 0.40: regime breaks priors; trust orchestrator more.
          CHAOTIC        → 0.30: historical patterns least transferable.
    """

    MOTOR_WEIGHTS_BY_REGIME: dict[str, dict[str, float]] = None  # type: ignore[assignment]
    META_LEARNER_WEIGHT_BY_REGIME: dict[str, float] = None       # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.MOTOR_WEIGHTS_BY_REGIME is None:
            self.MOTOR_WEIGHTS_BY_REGIME = self._default_motor_matrix()  # type: ignore[unreachable]
        if self.META_LEARNER_WEIGHT_BY_REGIME is None:
            self.META_LEARNER_WEIGHT_BY_REGIME = {  # type: ignore[unreachable]
                "bull_quiet":     0.60,
                "bull_volatile":  0.45,
                "bear_quiet":     0.50,
                "bear_volatile":  0.40,
                "transition":     0.50,
                "chaotic":        0.30,
            }
        # Normalise each regime row defensively (callers may inject custom dicts).
        self.MOTOR_WEIGHTS_BY_REGIME = {
            r: _normalised(w) for r, w in self.MOTOR_WEIGHTS_BY_REGIME.items()
        }

    @staticmethod
    def _default_motor_matrix() -> dict[str, dict[str, float]]:
        """
        Hand-tuned baseline matrix. Each row sums to 1.0 after normalisation
        in __post_init__. Numbers reflect domain priors:

          - BULL_QUIET       trend & sentiment overweight, hedging signals quiet
          - BULL_VOLATILE    sentiment + dealer_flow rise; tail_risk wakes up
          - BEAR_QUIET       defensive engines (skew, dealer_flow, RND) overweight
          - BEAR_VOLATILE    tail_risk + flow toxicity dominate
          - TRANSITION       roughly equal across all motors
          - CHAOTIC          flow toxicity + dealer_flow + macro overweighted;
                             historical-pattern engines underweighted
        """
        return {
            "bull_quiet": {
                "tail_risk": 0.06, "gamma_flip": 0.07, "vsa_forecast": 0.10,
                "sentiment": 0.10, "fear_greed": 0.06, "cross_asset": 0.06,
                "squeeze": 0.05, "shadow_delta": 0.05, "zomma": 0.04,
                "speed_instability": 0.04, "volatility_skew": 0.05,
                "risk_neutral_density": 0.06, "dealer_flow_dynamics": 0.07,
                "options_flow_toxicity": 0.05, "macro_regime_prior": 0.06,
                "orchestrator": 0.08,
            },
            "bull_volatile": {
                "tail_risk": 0.08, "gamma_flip": 0.09, "vsa_forecast": 0.08,
                "sentiment": 0.08, "fear_greed": 0.06, "cross_asset": 0.06,
                "squeeze": 0.05, "shadow_delta": 0.06, "zomma": 0.05,
                "speed_instability": 0.05, "volatility_skew": 0.06,
                "risk_neutral_density": 0.07, "dealer_flow_dynamics": 0.08,
                "options_flow_toxicity": 0.06, "macro_regime_prior": 0.05,
                "orchestrator": 0.08,
            },
            "bear_quiet": {
                "tail_risk": 0.10, "gamma_flip": 0.08, "vsa_forecast": 0.06,
                "sentiment": 0.05, "fear_greed": 0.06, "cross_asset": 0.07,
                "squeeze": 0.04, "shadow_delta": 0.05, "zomma": 0.05,
                "speed_instability": 0.04, "volatility_skew": 0.08,
                "risk_neutral_density": 0.09, "dealer_flow_dynamics": 0.08,
                "options_flow_toxicity": 0.06, "macro_regime_prior": 0.06,
                "orchestrator": 0.08,
            },
            "bear_volatile": {
                "tail_risk": 0.12, "gamma_flip": 0.08, "vsa_forecast": 0.05,
                "sentiment": 0.04, "fear_greed": 0.05, "cross_asset": 0.06,
                "squeeze": 0.04, "shadow_delta": 0.05, "zomma": 0.04,
                "speed_instability": 0.05, "volatility_skew": 0.08,
                "risk_neutral_density": 0.09, "dealer_flow_dynamics": 0.10,
                "options_flow_toxicity": 0.09, "macro_regime_prior": 0.05,
                "orchestrator": 0.06,
            },
            "transition": {
                "tail_risk": 0.07, "gamma_flip": 0.07, "vsa_forecast": 0.07,
                "sentiment": 0.06, "fear_greed": 0.06, "cross_asset": 0.06,
                "squeeze": 0.06, "shadow_delta": 0.06, "zomma": 0.05,
                "speed_instability": 0.05, "volatility_skew": 0.06,
                "risk_neutral_density": 0.07, "dealer_flow_dynamics": 0.07,
                "options_flow_toxicity": 0.06, "macro_regime_prior": 0.06,
                "orchestrator": 0.07,
            },
            "chaotic": {
                "tail_risk": 0.08, "gamma_flip": 0.06, "vsa_forecast": 0.04,
                "sentiment": 0.04, "fear_greed": 0.05, "cross_asset": 0.06,
                "squeeze": 0.04, "shadow_delta": 0.05, "zomma": 0.04,
                "speed_instability": 0.06, "volatility_skew": 0.06,
                "risk_neutral_density": 0.07, "dealer_flow_dynamics": 0.10,
                "options_flow_toxicity": 0.12, "macro_regime_prior": 0.08,
                "orchestrator": 0.05,
            },
        }


# ── Global config singleton (mutable via update_weights_from_performance) ───

_REGIME_CONFIG: RegimeWeightConfig | None = None


def get_regime_config() -> RegimeWeightConfig:
    global _REGIME_CONFIG
    if _REGIME_CONFIG is None:
        _REGIME_CONFIG = RegimeWeightConfig()
    return _REGIME_CONFIG


def reset_regime_config() -> None:
    """Reset cached config — primarily for tests."""
    global _REGIME_CONFIG
    _REGIME_CONFIG = None


# ── Public API ───────────────────────────────────────────────────────────────

def get_optimal_weights_for_regime(
    regime: str,
    regime_probs: dict[str, float] | None = None,
    config: RegimeWeightConfig | None = None,
) -> dict[str, float]:
    """
    Compute the per-motor weight vector for the current regime.

    If `regime_probs` is supplied, the result is a probability-weighted
    mixture of every regime's row (soft assignment). Otherwise the row of the
    hard-classified `regime` is returned.

    The output is always re-normalised to sum to 1.0.
    """
    cfg = config or get_regime_config()
    matrix = cfg.MOTOR_WEIGHTS_BY_REGIME

    if regime_probs:
        accum: dict[str, float] = {motor: 0.0 for motor in ALL_MOTORS}
        prob_sum = sum(p for p in regime_probs.values() if p is not None) or 0.0
        if prob_sum <= 0:
            return _normalised(matrix.get(regime, matrix["transition"]))
        for r, p in regime_probs.items():
            if p is None or p <= 0:
                continue
            row = matrix.get(r, matrix["transition"])
            for motor, w in row.items():
                accum[motor] = accum.get(motor, 0.0) + (p / prob_sum) * w
        return _normalised(accum)

    return _normalised(matrix.get(regime, matrix["transition"]))


def _regime_certainty(regime_probs: dict[str, float] | None) -> float:
    """
    1.0 when one regime fully dominates, 0.0 when uniform across N regimes.
    Defined as 1 - normalised_entropy. Returns 1.0 when probs are missing.
    """
    if not regime_probs:
        return 1.0
    probs = [p for p in regime_probs.values() if p and p > 0]
    if not probs:
        return 1.0
    s = sum(probs)
    if s <= 0:
        return 1.0
    norm = [p / s for p in probs]
    entropy = -sum(p * math.log(p) for p in norm if p > 0)
    max_entropy = math.log(len(norm)) if len(norm) > 1 else 1.0
    return float(max(0.0, min(1.0, 1.0 - entropy / max_entropy)))


def blend_meta_with_engines(
    meta_signal: dict[str, float],
    engine_signal: dict[str, float],
    regime: str,
    regime_probs: dict[str, float] | None = None,
    config: RegimeWeightConfig | None = None,
) -> dict[str, float]:
    """
    Combine the meta-learner output with the orchestrator signal.

    Inputs (each dict):
        signal      ∈ [-1, 1]  — directional bias
        confidence  ∈ [0, 1]   — model self-reported confidence

    Mixing weight:
        meta_weight   = META_LEARNER_WEIGHT_BY_REGIME[regime] × regime_certainty
        engine_weight = 1 - meta_weight

    Returns
    ───────
    dict with keys:
        signal           : float ∈ [-1, 1]
        confidence       : float ∈ [0, 1]
        meta_weight      : float ∈ [0, 1]
        engine_weight    : float ∈ [0, 1]
        regime_certainty : float ∈ [0, 1]
    """
    cfg = config or get_regime_config()
    base = cfg.META_LEARNER_WEIGHT_BY_REGIME.get(regime, 0.5)
    certainty = _regime_certainty(regime_probs)

    meta_weight = float(max(0.0, min(1.0, base * certainty)))
    engine_weight = float(1.0 - meta_weight)

    m_sig = float(meta_signal.get("signal", 0.0))
    e_sig = float(engine_signal.get("signal", 0.0))
    m_conf = float(meta_signal.get("confidence", 0.0))
    e_conf = float(engine_signal.get("confidence", 0.0))

    final_signal     = max(-1.0, min(1.0, meta_weight * m_sig  + engine_weight * e_sig))
    final_confidence = max(0.0,  min(1.0, meta_weight * m_conf + engine_weight * e_conf))

    return {
        "signal":           final_signal,
        "confidence":       final_confidence,
        "meta_weight":      meta_weight,
        "engine_weight":    engine_weight,
        "regime_certainty": certainty,
    }


# ── Reputation system: per-motor performance feedback ───────────────────────

# How quickly recent accuracy moves the multiplier. 0.20 means a single
# 50%-accuracy report shifts the multiplier ~10% toward 1.0.
_REPUTATION_LR = 0.20
_REPUTATION_FLOOR = 0.50    # never drop a motor below 50% of baseline
_REPUTATION_CEIL  = 1.50    # never lift above 150%

_DEFAULT_REPUTATION_PATH = Path(__file__).resolve().parent / "_motor_reputation.json"


def _load_reputation(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            return {}
        return {str(motor): {str(r): float(v) for r, v in regs.items()}
                for motor, regs in raw.items() if isinstance(regs, dict)}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to load motor reputation file %s: %s", path, exc)
        return {}


def _save_reputation(reputation: dict[str, dict[str, float]], path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(reputation, fh, indent=2, sort_keys=True)
    except OSError as exc:
        logger.warning("Failed to persist motor reputation to %s: %s", path, exc)


def update_weights_from_performance(
    motor_name: str,
    recent_accuracy: float,
    regime: str,
    *,
    config: RegimeWeightConfig | None = None,
    reputation_path: Path | None = None,
) -> dict[str, float]:
    """
    Adjust the base weight of `motor_name` for the given `regime` based on its
    recent observed accuracy. Implements an EMA-style multiplicative update:

        target_multiplier = 0.5 + recent_accuracy        # acc=0.50 → 1.0
        new_multiplier    = (1-lr) × old + lr × target

    The multiplier is bounded to [_REPUTATION_FLOOR, _REPUTATION_CEIL] so a
    single bad period cannot delete a motor from the ensemble.

    Returns the updated regime row (re-normalised to sum to 1.0).
    """
    if motor_name not in ALL_MOTORS:
        raise ValueError(f"Unknown motor: {motor_name}")
    if regime not in ENSEMBLE_REGIMES:
        raise ValueError(f"Unknown regime: {regime}")
    if not 0.0 <= recent_accuracy <= 1.0:
        raise ValueError(f"recent_accuracy must be in [0, 1]; got {recent_accuracy}")

    cfg = config or get_regime_config()
    path = reputation_path or _DEFAULT_REPUTATION_PATH

    reputation = _load_reputation(path)
    motor_rep = reputation.get(motor_name, {})
    old_mult  = float(motor_rep.get(regime, 1.0))

    # Map accuracy → target multiplier centred on 1.0
    target = 0.5 + float(recent_accuracy)
    new_mult = (1.0 - _REPUTATION_LR) * old_mult + _REPUTATION_LR * target
    new_mult = float(max(_REPUTATION_FLOOR, min(_REPUTATION_CEIL, new_mult)))

    motor_rep[regime] = new_mult
    reputation[motor_name] = motor_rep
    _save_reputation(reputation, path)

    # Apply to live config (so subsequent calls see the new weight)
    base_row = cfg.MOTOR_WEIGHTS_BY_REGIME[regime]
    adjusted = dict(base_row)
    adjusted[motor_name] = max(0.0, base_row[motor_name] * new_mult / max(old_mult, 1e-9))
    cfg.MOTOR_WEIGHTS_BY_REGIME[regime] = _normalised(adjusted)

    return cfg.MOTOR_WEIGHTS_BY_REGIME[regime]