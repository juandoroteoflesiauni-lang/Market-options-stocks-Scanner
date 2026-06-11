"""
probabilistic_signal_fusion.py
================================
Bayesian multi-engine signal fusion for the probabilistic scanner.

Replaces the linear blend in synthesize_probabilistic_signal_v2() with
regime-adaptive weighted fusion, conflict scoring, and a regime gate.

Public API
----------
- synthesize_fusion_signal(symbol, engine_outputs, regime_result) -> FusionResult
- _normalize_engine_output(engine_name, raw) -> float   [-1, 1]
- _compute_conflict_score(signals, weights) -> float     [0, 1]
- Regime, Direction enums
- FusionResult dataclass
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Regime(str, Enum):
    BULL_QUIET = "BULL_QUIET"
    BEAR_VOLATILE = "BEAR_VOLATILE"
    CHAOTIC = "CHAOTIC"
    UNKNOWN = "UNKNOWN"


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class FusionResult:
    """Auditable output of synthesize_fusion_signal().

    Legacy fields (v1-compatible):
        signal      float [-1, 1]
        direction   str   UP | DOWN | NEUTRAL
        confidence  float [0, 1]
        regime      str   active regime name

    Extended fields (v2):
        conviction_drivers  list[str]        top-3 engines by weighted contribution
        conflict_score      float [0, 1]     0 = consensus, 1 = max divergence
        regime_alignment    bool             signal and regime are consistent
        regime_probs        dict[str, float] HMM posterior probabilities
        motor_signals       dict[str, float] normalized signals per engine
        motor_weights       dict[str, float] effective weights per engine
        suppressed          bool             True when regime gate blocked signal
        suppression_reason  str | None       reason for suppression
        latency_ms          float            wall-clock synthesis time
    """

    signal: float = 0.0
    direction: str = Direction.NEUTRAL
    confidence: float = 0.0
    regime: str = Regime.UNKNOWN
    conviction_drivers: list[str] = field(default_factory=list)
    conflict_score: float = 0.0
    regime_alignment: bool = True
    regime_probs: dict[str, float] = field(default_factory=dict)
    motor_signals: dict[str, float] = field(default_factory=dict)
    motor_weights: dict[str, float] = field(default_factory=dict)
    suppressed: bool = False
    suppression_reason: str | None = None
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Regime weight matrix
# ---------------------------------------------------------------------------
# Weights are relative — normalized to sum=1 at runtime.
#
#   BULL_QUIET    → momentum/dealer-flow/gamma engines dominate
#   BEAR_VOLATILE → tail-risk, jump, sentiment, fear-greed dominate
#   CHAOTIC       → uniform weights + aggressive suppression threshold

_WEIGHT_MATRIX: dict[str, dict[str, float]] = {
    "tail_risk": {Regime.BULL_QUIET: 0.05, Regime.BEAR_VOLATILE: 0.20, Regime.CHAOTIC: 0.15},
    "expected_move": {Regime.BULL_QUIET: 0.10, Regime.BEAR_VOLATILE: 0.08, Regime.CHAOTIC: 0.08},
    "squeeze": {Regime.BULL_QUIET: 0.15, Regime.BEAR_VOLATILE: 0.10, Regime.CHAOTIC: 0.08},
    "jump_risk": {Regime.BULL_QUIET: 0.05, Regime.BEAR_VOLATILE: 0.18, Regime.CHAOTIC: 0.12},
    "gamma_flip": {Regime.BULL_QUIET: 0.20, Regime.BEAR_VOLATILE: 0.12, Regime.CHAOTIC: 0.10},
    "vsa_forecast": {Regime.BULL_QUIET: 0.15, Regime.BEAR_VOLATILE: 0.08, Regime.CHAOTIC: 0.08},
    "sentiment": {Regime.BULL_QUIET: 0.08, Regime.BEAR_VOLATILE: 0.12, Regime.CHAOTIC: 0.10},
    "fear_greed": {Regime.BULL_QUIET: 0.07, Regime.BEAR_VOLATILE: 0.07, Regime.CHAOTIC: 0.15},
    "cross_asset": {Regime.BULL_QUIET: 0.08, Regime.BEAR_VOLATILE: 0.05, Regime.CHAOTIC: 0.14},
    "regime": {Regime.BULL_QUIET: 0.10, Regime.BEAR_VOLATILE: 0.10, Regime.CHAOTIC: 0.10},
}

# Suppression thresholds per regime
_REGIME_GATE: dict[str, dict] = {
    Regime.BULL_QUIET: {"conflict_threshold": 0.60, "confidence_min": 0.18},
    Regime.BEAR_VOLATILE: {"conflict_threshold": 0.50, "confidence_min": 0.20},
    Regime.CHAOTIC: {"conflict_threshold": 0.38, "confidence_min": 0.25},
    Regime.UNKNOWN: {"conflict_threshold": 0.30, "confidence_min": 0.28},
}

# Regime-direction consistency penalty
_DIRECTION_BIAS: dict[str, float] = {
    f"{Regime.BULL_QUIET}_{Direction.DOWN}": 0.50,
    f"{Regime.BULL_QUIET}_{Direction.UP}": 1.00,
    f"{Regime.BEAR_VOLATILE}_{Direction.UP}": 0.55,
    f"{Regime.BEAR_VOLATILE}_{Direction.DOWN}": 1.00,
    f"{Regime.CHAOTIC}_{Direction.UP}": 0.70,
    f"{Regime.CHAOTIC}_{Direction.DOWN}": 0.70,
}

# Engines that carry magnitude only (no directional information)
_MAGNITUDE_ONLY = frozenset({"expected_move", "jump_risk"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_engine_output(engine_name: str, raw: Any) -> float:
    """Project raw engine output to [-1, 1].

    fear_greed: [0, 100] → [-1, 1]  (>50 = greed = bullish)
    expected_move, jump_risk: magnitude only → 0 (no directional signal)
    all others: already in [-1, 1], clip defensively
    """
    if raw is None:
        return 0.0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.warning("fusion.normalize non-numeric engine=%s value=%r", engine_name, raw)
        return 0.0

    if engine_name == "fear_greed":
        return float(np.clip((val - 50.0) / 50.0, -1.0, 1.0))
    if engine_name in _MAGNITUDE_ONLY:
        return 0.0
    return float(np.clip(val, -1.0, 1.0))


def _compute_conflict_score(
    signals: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Weighted standard deviation of normalized signals, scaled to [0, 1].

    0 → all engines agree; 1 → maximum divergence (50/50 split ±1).
    """
    if not signals:
        return 0.0

    motors = list(signals.keys())
    s_arr = np.array([signals[m] for m in motors], dtype=float)
    w_arr = np.array([weights.get(m, 1.0) for m in motors], dtype=float)
    w_arr /= w_arr.sum()

    mu_w = float(np.dot(w_arr, s_arr))
    var_w = float(np.dot(w_arr, (s_arr - mu_w) ** 2))
    sigma_w = float(np.sqrt(var_w))

    return round(float(np.clip(sigma_w, 0.0, 1.0)), 4)


def _apply_regime_gate(
    signal: float,
    confidence: float,
    conflict_score: float,
    regime: str,
) -> tuple[float, bool, str | None]:
    """Suppress signal when conflict or confidence fail the regime thresholds."""
    gate = _REGIME_GATE.get(regime, _REGIME_GATE[Regime.UNKNOWN])

    if conflict_score > gate["conflict_threshold"]:
        return (
            0.0,
            True,
            (
                f"conflict_score={conflict_score:.2f} > threshold "
                f"{gate['conflict_threshold']:.2f} in regime {regime}"
            ),
        )
    if confidence < gate["confidence_min"]:
        return (
            0.0,
            True,
            (
                f"confidence={confidence:.2f} < minimum "
                f"{gate['confidence_min']:.2f} in regime {regime}"
            ),
        )
    return signal, False, None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def synthesize_fusion_signal(
    symbol: str,
    engine_outputs: dict[str, Any],
    regime_result: dict[str, Any] | None = None,
) -> dict:
    """Fuse multi-engine probabilistic signals into a single auditable result.

    Parameters
    ----------
    symbol:
        Asset ticker.
    engine_outputs:
        {engine_name: raw_value}.  Missing engines degrade gracefully (weight=0).
    regime_result:
        Output dict from MarkovRegimeEngine.  Expected keys:
          "regime"       str   dominant regime label
          "regime_probs" dict  {BULL_QUIET: p, BEAR_VOLATILE: p, CHAOTIC: p}
        None → UNKNOWN regime with uniform weights.

    Returns
    -------
    dict  — FusionResult.to_dict(), backward-compatible with legacy signal fields.
    """
    t0 = time.perf_counter()

    # ------------------------------------------------------------------
    # 1. Regime + probabilities
    # ------------------------------------------------------------------
    regime_probs: dict[str, float] = {
        Regime.BULL_QUIET: 0.33,
        Regime.BEAR_VOLATILE: 0.33,
        Regime.CHAOTIC: 0.34,
    }
    active_regime = Regime.UNKNOWN

    if regime_result:
        raw_r = regime_result.get("regime", Regime.UNKNOWN)
        active_regime = Regime(raw_r) if raw_r in Regime._value2member_map_ else Regime.UNKNOWN
        raw_probs = regime_result.get("regime_probs")
        if isinstance(raw_probs, dict):
            regime_probs = raw_probs

    logger.debug("fusion.regime symbol=%s regime=%s", symbol, active_regime)

    # ------------------------------------------------------------------
    # 2. Normalize engine outputs → [-1, 1]
    # ------------------------------------------------------------------
    motor_signals: dict[str, float] = {
        name: _normalize_engine_output(name, val)
        for name, val in engine_outputs.items()
        if name != "regime"
    }

    # ------------------------------------------------------------------
    # 3. Effective weights — soft interpolation across regime probabilities
    #    w_i = Σ_r P(regime=r) × w_i(r)
    #    Avoids hard-switching discontinuities near 50/50 regime boundaries.
    # ------------------------------------------------------------------
    regime_map = {
        Regime.BULL_QUIET: regime_probs.get(Regime.BULL_QUIET, 0.33),
        Regime.BEAR_VOLATILE: regime_probs.get(Regime.BEAR_VOLATILE, 0.33),
        Regime.CHAOTIC: regime_probs.get(Regime.CHAOTIC, 0.34),
    }
    motor_weights: dict[str, float] = {}
    for name in motor_signals:
        row = _WEIGHT_MATRIX.get(name, {})
        motor_weights[name] = (
            sum(p * row.get(r, 0.0) for r, p in regime_map.items()) if row else 0.05
        )

    total_w = sum(motor_weights.values())
    if total_w > 0:
        motor_weights = {k: v / total_w for k, v in motor_weights.items()}
    else:
        n = max(len(motor_signals), 1)
        motor_weights = {k: 1.0 / n for k in motor_signals}

    # ------------------------------------------------------------------
    # 4. Aggregate signal
    # ------------------------------------------------------------------
    raw_signal = float(
        np.clip(
            sum(motor_weights.get(m, 0.0) * s for m, s in motor_signals.items()),
            -1.0,
            1.0,
        )
    )

    # ------------------------------------------------------------------
    # 5. Direction
    # ------------------------------------------------------------------
    if raw_signal > 0.10:
        direction = Direction.UP
    elif raw_signal < -0.10:
        direction = Direction.DOWN
    else:
        direction = Direction.NEUTRAL

    # ------------------------------------------------------------------
    # 6. Regime-direction bias penalty
    # ------------------------------------------------------------------
    bias_key = f"{active_regime}_{direction}"
    bias_factor = _DIRECTION_BIAS.get(bias_key, 1.0)
    penalized = float(np.clip(raw_signal * bias_factor, -1.0, 1.0))

    if bias_factor < 1.0:
        logger.info(
            "fusion.bias_penalty symbol=%s raw=%.3f penalized=%.3f " "direction=%s regime=%s",
            symbol,
            raw_signal,
            penalized,
            direction,
            active_regime,
        )

    # ------------------------------------------------------------------
    # 7. Conflict score
    # ------------------------------------------------------------------
    conflict = _compute_conflict_score(motor_signals, motor_weights)

    # ------------------------------------------------------------------
    # 8. Confidence
    #    confidence = |signal| × (1 − conflict) × regime_certainty
    # ------------------------------------------------------------------
    regime_certainty = float(max(regime_probs.values())) if regime_probs else 0.5
    confidence = float(
        np.clip(
            abs(penalized) * (1.0 - conflict) * regime_certainty,
            0.0,
            1.0,
        )
    )

    # ------------------------------------------------------------------
    # 9. Regime gate
    # ------------------------------------------------------------------
    final_signal, suppressed, suppression_reason = _apply_regime_gate(
        penalized,
        confidence,
        conflict,
        active_regime,
    )
    if suppressed:
        final_signal = 0.0
        confidence = 0.0
        direction = Direction.NEUTRAL
        logger.warning("fusion.suppressed symbol=%s reason=%s", symbol, suppression_reason)

    # ------------------------------------------------------------------
    # 10. Conviction drivers — top-3 by absolute weighted contribution
    # ------------------------------------------------------------------
    contributions = {
        m: abs(motor_weights.get(m, 0.0) * motor_signals.get(m, 0.0)) for m in motor_signals
    }
    top_drivers = sorted(contributions, key=contributions.__getitem__, reverse=True)[:3]

    # ------------------------------------------------------------------
    # 11. Regime alignment
    # ------------------------------------------------------------------
    regime_alignment = bias_factor == 1.0 and not suppressed

    # ------------------------------------------------------------------
    # 12. Build result
    # ------------------------------------------------------------------
    latency_ms = (time.perf_counter() - t0) * 1000

    result = FusionResult(
        signal=round(final_signal, 4),
        direction=direction.value,
        confidence=round(confidence, 4),
        regime=active_regime.value,
        conviction_drivers=top_drivers,
        conflict_score=conflict,
        regime_alignment=regime_alignment,
        regime_probs=regime_probs,
        motor_signals={k: round(v, 4) for k, v in motor_signals.items()},
        motor_weights={k: round(v, 4) for k, v in motor_weights.items()},
        suppressed=suppressed,
        suppression_reason=suppression_reason,
        latency_ms=round(latency_ms, 2),
    )

    logger.info(
        "fusion.done symbol=%s signal=%.3f dir=%s conf=%.2f "
        "conflict=%.2f suppressed=%s regime=%s latency=%.1fms",
        symbol,
        result.signal,
        result.direction,
        result.confidence,
        result.conflict_score,
        result.suppressed,
        result.regime,
        result.latency_ms,
    )

    return result.to_dict()
