"""signal_filter.py
====================
Selectivity layer that decides when a probabilistic signal is high-enough
quality to trade.

Real edge in quantitative trading lives in selectivity, not in firing on every
generated signal. This module sits between the synthesised fusion signal and
order generation, applying:

  · BLOCKING filters       — any one ⇒ should_trade=False
  · REDUCTION filters      — multiplicatively scale position_size_multiplier
  · COMPOSITE filter_score — weighted [0, 1] quality metric for monitoring

Public API
----------
- FilterResult                                  dataclass
- apply_signal_filters(signal_result, quality, market_context) -> FilterResult
- get_filter_statistics(filter_history) -> dict
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from backend.config.logger_setup import get_logger
from backend.services.signal_quality import ConvictionLevel, SignalQuality

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Filter thresholds (single source of truth)
# ---------------------------------------------------------------------------

# Blocking thresholds
_CONFLICT_BLOCK_THRESHOLD = 0.65  # > this ⇒ block
_LOW_CONF_BLOCK_THRESHOLD = 0.40  # combined with regime misalignment
_MIN_CONVICTION_DRIVERS = 2  # need at least this many active motors

# Reduction zones
_CONFLICT_REDUCE_LOW = 0.40
_CONFLICT_REDUCE_HIGH = 0.65
_CONF_REDUCE_LOW = 0.20
_CONF_REDUCE_HIGH = 0.35
_HIGH_VIX_THRESHOLD = 30.0
_LOW_VOLUME_RATIO = 0.50

# Reduction multipliers
_MULT_CONFLICT_MID = 0.60
_MULT_LOW_CONFIDENCE = 0.70
_MULT_HIGH_VIX = 0.75
_MULT_LOW_LIQUIDITY = 0.80
_MULT_ONE_CONTRADICTION = 0.85

# filter_score weights (must sum to 1.0)
_WEIGHT_CONFIDENCE = 0.35
_WEIGHT_INV_CONFLICT = 0.30
_WEIGHT_CONVICTION_NORM = 0.20
_WEIGHT_REGIME_CERTAINTY = 0.15

# Conviction → [0, 1] for the composite score
_CONVICTION_NORMALISED: dict[ConvictionLevel, float] = {
    ConvictionLevel.VERY_HIGH: 1.00,
    ConvictionLevel.HIGH: 0.75,
    ConvictionLevel.MEDIUM: 0.50,
    ConvictionLevel.LOW: 0.25,
    ConvictionLevel.INSUFFICIENT: 0.00,
}


# ---------------------------------------------------------------------------
# FilterResult
# ---------------------------------------------------------------------------


@dataclass
class FilterResult:
    """Outcome of running the signal-quality filter chain."""

    should_trade: bool
    filter_reason: str | None = None
    filter_score: float = 0.0
    warnings: list[str] = field(default_factory=list)
    position_size_multiplier: float = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _market_hours_value(ctx: dict[str, Any]) -> str:
    raw = ctx.get("market_hours", "regular")
    return str(raw).lower().strip() if raw is not None else "regular"


def _safe_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _composite_filter_score(
    confidence: float,
    conflict_score: float,
    conviction_level: ConvictionLevel,
    regime_certainty: float,
) -> float:
    """Weighted average of four [0, 1] quality terms — see module docstring."""
    confidence = max(0.0, min(1.0, confidence))
    conflict_score = max(0.0, min(1.0, conflict_score))
    regime_certainty = max(0.0, min(1.0, regime_certainty))
    conviction_norm = _CONVICTION_NORMALISED.get(conviction_level, 0.0)

    score = (
        _WEIGHT_CONFIDENCE * confidence
        + _WEIGHT_INV_CONFLICT * (1.0 - conflict_score)
        + _WEIGHT_CONVICTION_NORM * conviction_norm
        + _WEIGHT_REGIME_CERTAINTY * regime_certainty
    )
    return float(max(0.0, min(1.0, score)))


def _blocking_reason(
    signal_result: dict[str, Any],
    quality: SignalQuality,
    market_context: dict[str, Any],
) -> str | None:
    """Return the first blocking reason, or None if signal is tradeable."""
    if bool(signal_result.get("suppressed", False)):
        return signal_result.get("suppression_reason") or "signal_suppressed"

    conflict = _safe_float(signal_result.get("conflict_score"), 0.0)
    if conflict > _CONFLICT_BLOCK_THRESHOLD:
        return f"conflict_score>{_CONFLICT_BLOCK_THRESHOLD:.2f}"

    if quality.conviction_level == ConvictionLevel.INSUFFICIENT:
        return "conviction_insufficient"

    confidence = _safe_float(signal_result.get("confidence"), 0.0)
    regime_aligned = bool(signal_result.get("regime_alignment", True))
    if (not regime_aligned) and confidence < _LOW_CONF_BLOCK_THRESHOLD:
        return "regime_misaligned_and_low_confidence"

    if _market_hours_value(market_context) != "regular":
        return f"market_hours={_market_hours_value(market_context)}"

    drivers = signal_result.get("conviction_drivers") or []
    if not isinstance(drivers, (list, tuple)) or len(drivers) < _MIN_CONVICTION_DRIVERS:
        return f"too_few_active_motors(<{_MIN_CONVICTION_DRIVERS})"

    return None


def _apply_reductions(
    signal_result: dict[str, Any],
    quality: SignalQuality,
    market_context: dict[str, Any],
) -> tuple[float, list[str]]:
    """
    Apply multiplicative reduction filters.
    Returns (final_multiplier, warnings).
    """
    warnings: list[str] = []
    multiplier = 1.0

    conflict = _safe_float(signal_result.get("conflict_score"), 0.0)
    confidence = _safe_float(signal_result.get("confidence"), 0.0)
    vix = _safe_float(market_context.get("current_vix"), 0.0)
    vol_ratio = _safe_float(market_context.get("avg_volume_ratio"), 1.0)

    if _CONFLICT_REDUCE_LOW <= conflict <= _CONFLICT_REDUCE_HIGH:
        multiplier *= _MULT_CONFLICT_MID
        warnings.append(f"conflict_score={conflict:.2f} in mid-range → ×{_MULT_CONFLICT_MID}")

    if _CONF_REDUCE_LOW <= confidence <= _CONF_REDUCE_HIGH:
        multiplier *= _MULT_LOW_CONFIDENCE
        warnings.append(f"confidence={confidence:.2f} low → ×{_MULT_LOW_CONFIDENCE}")

    if vix > _HIGH_VIX_THRESHOLD:
        multiplier *= _MULT_HIGH_VIX
        warnings.append(f"VIX={vix:.1f} > {_HIGH_VIX_THRESHOLD} → ×{_MULT_HIGH_VIX}")

    if vol_ratio < _LOW_VOLUME_RATIO:
        multiplier *= _MULT_LOW_LIQUIDITY
        warnings.append(
            f"avg_volume_ratio={vol_ratio:.2f} < {_LOW_VOLUME_RATIO} → ×{_MULT_LOW_LIQUIDITY}"
        )

    contradictions = list(quality.contradicting_motors or [])
    if len(contradictions) == 1:
        multiplier *= _MULT_ONE_CONTRADICTION
        warnings.append(f"1 contradicting motor ({contradictions[0]}) → ×{_MULT_ONE_CONTRADICTION}")

    multiplier = float(max(0.0, min(1.0, multiplier)))
    return multiplier, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_signal_filters(
    signal_result: dict[str, Any],
    quality: SignalQuality,
    market_context: dict[str, Any],
) -> FilterResult:
    """
    Decide whether to trade the signal and how to size the position.

    Parameters
    ──────────
    signal_result : output of synthesize_fusion_signal(); must include at
        minimum {signal, confidence, suppressed, regime_alignment,
                 conviction_drivers, conflict_score}.
    quality       : SignalQuality from assess_signal_quality().
    market_context: {current_vix, market_hours, days_to_expiry,
                     avg_volume_ratio, regime_certainty}.

    Returns
    ───────
    FilterResult with should_trade, filter_reason, filter_score,
    warnings, position_size_multiplier.
    """
    if not isinstance(signal_result, dict):
        raise TypeError("signal_result must be a dict")
    if not isinstance(market_context, dict):
        raise TypeError("market_context must be a dict")
    if not isinstance(quality, SignalQuality):
        raise TypeError("quality must be a SignalQuality instance")

    # Composite score is computed regardless of blocking decisions for monitoring.
    confidence = _safe_float(signal_result.get("confidence"), 0.0)
    conflict_score = _safe_float(signal_result.get("conflict_score"), 0.0)
    regime_certainty = _safe_float(market_context.get("regime_certainty"), 1.0)
    score = _composite_filter_score(
        confidence,
        conflict_score,
        quality.conviction_level,
        regime_certainty,
    )

    # Blocking filters
    block = _blocking_reason(signal_result, quality, market_context)
    if block is not None:
        logger.debug("Signal blocked: %s", block)
        return FilterResult(
            should_trade=False,
            filter_reason=block,
            filter_score=score,
            warnings=[],
            position_size_multiplier=0.0,
        )

    # Reduction filters
    multiplier, warnings = _apply_reductions(signal_result, quality, market_context)

    return FilterResult(
        should_trade=True,
        filter_reason=None,
        filter_score=score,
        warnings=warnings,
        position_size_multiplier=multiplier,
    )


def get_filter_statistics(filter_history: list[FilterResult]) -> dict[str, Any]:
    """
    Aggregate statistics over a series of FilterResult outcomes.

    Returns
    ───────
    dict with:
        total                   : int
        passed                  : int
        pass_rate               : float ∈ [0, 1]
        mean_filter_score       : float
        mean_position_multiplier: float (passed signals only; nan if none)
        reasons                 : {reason: count} for blocked signals
        top_reason              : str | None — most common block reason
    """
    if not filter_history:
        return {
            "total": 0,
            "passed": 0,
            "pass_rate": 0.0,
            "mean_filter_score": 0.0,
            "mean_position_multiplier": float("nan"),
            "reasons": {},
            "top_reason": None,
        }

    if not all(isinstance(r, FilterResult) for r in filter_history):
        raise TypeError("filter_history must contain only FilterResult instances")

    total = len(filter_history)
    passed = sum(1 for r in filter_history if r.should_trade)
    pass_rate = passed / total if total else 0.0

    mean_score = sum(r.filter_score for r in filter_history) / total

    multipliers = [r.position_size_multiplier for r in filter_history if r.should_trade]
    mean_mult = (sum(multipliers) / len(multipliers)) if multipliers else float("nan")

    reasons_counter: Counter[str] = Counter(
        r.filter_reason for r in filter_history if (not r.should_trade) and r.filter_reason
    )
    top_reason = reasons_counter.most_common(1)[0][0] if reasons_counter else None

    return {
        "total": total,
        "passed": passed,
        "pass_rate": float(pass_rate),
        "mean_filter_score": float(mean_score),
        "mean_position_multiplier": float(mean_mult) if multipliers else float("nan"),
        "reasons": dict(reasons_counter),
        "top_reason": top_reason,
    }
