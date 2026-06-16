from __future__ import annotations
from typing import Any
"""signal_quality.py
====================
Standardises evaluation and communication of the final probabilistic signal quality.

Public API
----------
- ConvictionLevel           enum
- SignalQuality             dataclass
- assess_signal_quality(signal_result) -> SignalQuality
- format_signal_for_scanner(signal_result, quality) -> dict
"""


from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# ConvictionLevel
# ---------------------------------------------------------------------------


class ConvictionLevel(str, Enum):
    VERY_HIGH = "VERY_HIGH"  # confidence > 0.75
    HIGH = "HIGH"  # 0.55 – 0.75
    MEDIUM = "MEDIUM"  # 0.35 – 0.55
    LOW = "LOW"  # 0.15 – 0.35
    INSUFFICIENT = "INSUFFICIENT"  # < 0.15

    @property
    def position_size_factor(self) -> float:
        return _POSITION_SIZE_FACTOR[self]

    @property
    def tradeable(self) -> bool:
        return self in (ConvictionLevel.VERY_HIGH, ConvictionLevel.HIGH, ConvictionLevel.MEDIUM)


# Thresholds are lower-inclusive: level applies when confidence >= threshold.
# Order matters — first match wins (descending).
_CONVICTION_THRESHOLDS: list[tuple[float, ConvictionLevel]] = [
    (0.75, ConvictionLevel.VERY_HIGH),
    (0.55, ConvictionLevel.HIGH),
    (0.35, ConvictionLevel.MEDIUM),
    (0.15, ConvictionLevel.LOW),
    (0.0, ConvictionLevel.INSUFFICIENT),
]

_POSITION_SIZE_FACTOR: dict[ConvictionLevel, float] = {
    ConvictionLevel.VERY_HIGH: 1.00,
    ConvictionLevel.HIGH: 0.75,
    ConvictionLevel.MEDIUM: 0.50,
    ConvictionLevel.LOW: 0.25,
    ConvictionLevel.INSUFFICIENT: 0.00,
}


def _classify_conviction(confidence: float) -> ConvictionLevel:
    for threshold, level in _CONVICTION_THRESHOLDS:
        if confidence >= threshold:
            return level
    return ConvictionLevel.INSUFFICIENT


# ---------------------------------------------------------------------------
# SignalQuality dataclass
# ---------------------------------------------------------------------------


@dataclass
class SignalQuality:
    conviction_level: ConvictionLevel
    should_trade: bool
    position_size_factor: float
    top_drivers: list[tuple[str, float]] = field(default_factory=list)
    contradicting_motors: list[str] = field(default_factory=list)
    quality_summary: str = ""


# ---------------------------------------------------------------------------
# assess_signal_quality
# ---------------------------------------------------------------------------


def assess_signal_quality(signal_result: dict[str, Any]) -> SignalQuality:
    """Assess quality of a synthesize_fusion_signal() output dict.

    Parameters
    ----------
    signal_result : dict returned by synthesize_fusion_signal()
        Required keys: signal, confidence, suppressed, regime_alignment,
                       motor_signals, motor_weights, conviction_drivers
        Optional: direction, regime, conflict_score, suppression_reason
    """
    confidence = float(signal_result.get("confidence", 0.0))
    suppressed = bool(signal_result.get("suppressed", False))
    regime_aligned = bool(signal_result.get("regime_alignment", True))
    signal_val = float(signal_result.get("signal", 0.0))
    direction = str(signal_result.get("direction", "NEUTRAL"))
    regime = str(signal_result.get("regime", "UNKNOWN"))
    conflict = float(signal_result.get("conflict_score", 0.0))

    motor_signals: dict[str, float] = signal_result.get("motor_signals", {}) or {}
    motor_weights: dict[str, float] = signal_result.get("motor_weights", {}) or {}

    # 1. Conviction level
    level = _classify_conviction(confidence)

    # 2. Trade gate
    should_trade = level.tradeable and not suppressed and regime_aligned

    # 3. Position size factor
    size_factor = 0.0 if suppressed else level.position_size_factor

    # 4. Top drivers — absolute weighted contribution, descending
    contributions: dict[str, float] = {
        m: abs(motor_weights.get(m, 0.0) * s) for m, s in motor_signals.items()
    }
    top_drivers: list[tuple[str, float]] = sorted(
        contributions.items(), key=lambda kv: kv[1], reverse=True
    )[:3]

    # 5. Contradicting motors — vote opposite to net signal direction
    contradicting: list[str] = []
    if signal_val > 0:
        contradicting = [m for m, s in motor_signals.items() if s < -0.05]
    elif signal_val < 0:
        contradicting = [m for m, s in motor_signals.items() if s > 0.05]

    # 6. Quality summary
    quality_summary = _build_summary(
        level,
        direction,
        regime,
        conflict,
        suppressed,
        regime_aligned,
        top_drivers,
        contradicting,
        signal_result,
    )

    return SignalQuality(
        conviction_level=level,
        should_trade=should_trade,
        position_size_factor=round(size_factor, 4),
        top_drivers=[(m, round(c, 4)) for m, c in top_drivers],
        contradicting_motors=contradicting,
        quality_summary=quality_summary,
    )


# ---------------------------------------------------------------------------
# format_signal_for_scanner
# ---------------------------------------------------------------------------


def format_signal_for_scanner(
    signal_result: dict[str, Any],
    quality: SignalQuality,
) -> dict[str, Any]:
    """Build final frontend payload from fusion result + quality assessment.

    Returns a flat dict ready for the Market Scanner renderer.
    """
    return {
        # Core signal
        "signal": signal_result.get("signal", 0.0),
        "direction": signal_result.get("direction", "NEUTRAL"),
        "confidence": signal_result.get("confidence", 0.0),
        "regime": signal_result.get("regime", "UNKNOWN"),
        # Quality
        "conviction_level": quality.conviction_level.value,
        "should_trade": quality.should_trade,
        "position_size_factor": quality.position_size_factor,
        "quality_summary": quality.quality_summary,
        # Drivers & conflict
        "top_drivers": quality.top_drivers,
        "contradicting_motors": quality.contradicting_motors,
        "conflict_score": signal_result.get("conflict_score", 0.0),
        "regime_alignment": signal_result.get("regime_alignment", True),
        # Suppression
        "suppressed": signal_result.get("suppressed", False),
        "suppression_reason": signal_result.get("suppression_reason"),
        # Audit
        "motor_signals": signal_result.get("motor_signals", {}),
        "motor_weights": signal_result.get("motor_weights", {}),
        "regime_probs": signal_result.get("regime_probs", {}),
        "conviction_drivers": signal_result.get("conviction_drivers", []),
        "latency_ms": signal_result.get("latency_ms", 0.0),
    }


# ---------------------------------------------------------------------------
# Internal summary builder
# ---------------------------------------------------------------------------

_DIR_ES: dict[str, str] = {"UP": "alcista", "DOWN": "bajista", "NEUTRAL": "neutral"}
_REGIME_ES: dict[str, str] = {
    "BULL_QUIET": "tendencia alcista estable",
    "BEAR_VOLATILE": "tendencia bajista volátil",
    "CHAOTIC": "régimen caótico",
    "UNKNOWN": "régimen desconocido",
}
_LEVEL_ES: dict[ConvictionLevel, str] = {
    ConvictionLevel.VERY_HIGH: "Muy alta confianza",
    ConvictionLevel.HIGH: "Alta confianza",
    ConvictionLevel.MEDIUM: "Confianza media",
    ConvictionLevel.LOW: "Confianza baja",
    ConvictionLevel.INSUFFICIENT: "Confianza insuficiente",
}


def _build_summary(
    level: ConvictionLevel,
    direction: str,
    regime: str,
    conflict: float,
    suppressed: bool,
    regime_aligned: bool,
    top_drivers: list[tuple[str, float]],
    contradicting: list[str],
    signal_result: dict[str, Any],
) -> str:
    parts: list[str] = []

    if suppressed:
        reason = signal_result.get("suppression_reason") or "umbral de régimen"
        parts.append(f"Señal suprimida: {reason}.")
        return " ".join(parts)

    dir_es = _DIR_ES.get(direction, direction.lower())
    regime_es = _REGIME_ES.get(regime, regime)
    level_es = _LEVEL_ES.get(level, level.value)

    parts.append(f"{level_es} {dir_es} en {regime_es}.")

    if top_drivers:
        driver_names = ", ".join(m for m, _ in top_drivers)
        parts.append(f"Motores clave: {driver_names}.")

    if not regime_aligned:
        parts.append("Desalineación de régimen aplica penalización.")

    if conflict > 0.50:
        parts.append(f"Conflicto elevado ({conflict:.2f}).")
    elif conflict < 0.15:
        parts.append("Consenso sólido entre motores.")

    if contradicting:
        n = len(contradicting)
        names = ", ".join(contradicting[:3])
        parts.append(f"{n} motor{'es' if n > 1 else ''} en contra: {names}.")

    return " ".join(parts)
