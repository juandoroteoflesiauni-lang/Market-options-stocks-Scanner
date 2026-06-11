"""Institutional scoring layer: effective weights, regime modulation, concentration guard."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    SCANNER_WEIGHT_MAX,
    MarketScannerRow,
    ScannerCustomization,
    ScannerIndicatorDefinition,
    ScannerModuleKey,
)
from backend.services.market_scanner_indicator_catalog import list_indicator_definitions

logger = get_logger(__name__)

SCORING_SCHEMA_VERSION = "institutional-v2"
SCORING_SCHEMA_VERSION_LEGACY = "institutional-v1"
WEIGHT_SOFT_CAP = SCANNER_WEIGHT_MAX

# Legacy regime keys → catalog indicator keys
REGIME_INDICATOR_ALIASES: dict[str, str] = {
    "ema_trend": "ema_21_42",
    "adx": "market_structure",
    "atr_band": "prf",
    "vwap_distance": "avwap_vwap",
    "volume_trend": "volume",
}

DEFAULT_MODULE_BLEND_WEIGHTS: dict[str, float] = {
    "technical": 1.0,
    "probabilistic": 0.85,
    "options_gex": 1.0,
    "fundamentals": 0.75,
    "macro_micro": 0.65,
}

# Technical engine key → catalog indicator key
TECHNICAL_ENGINE_TO_INDICATOR: dict[str, str] = {
    "smc": "smc",
    "fvg": "fvg",
    "vsa": "vsa",
    "market_structure": "market_structure",
    "order_flow_delta": "order_flow_delta",
    "volume_profile": "volume_profile",
    "hmm_regime": "hmm_regime",
}

PHASE_A_INDICATOR_KEYS: tuple[str, ...] = (
    "ema_7_14",
    "ema_21_42",
    "ema_100_200",
    "macd",
    "rsi",
    "rsi_hist",
    "avwap_vwap",
    "volume",
    "relative_strength",
    "supertrend",
    "bbp",
    "prf",
    "vix",
)

PROB_ENGINE_TO_INDICATOR: dict[str, str] = {
    "tail_risk": "tail_risk",
    "expected_move": "expected_move",
    "squeeze": "squeeze",
    "jump_risk": "jump_risk",
    "regime": "regime",
    "fear_greed": "regime",
}


def institutional_scoring_enabled() -> bool:
    raw = os.getenv("SCANNER_INSTITUTIONAL_SCORING", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def scoring_version_label() -> str:
    if institutional_scoring_enabled():
        return "market-scanner-v4-institutional"
    return "market-scanner-v3"


def migrate_customization_scoring_schema(
    customization: ScannerCustomization,
) -> ScannerCustomization:
    """Ensure customization carries the institutional schema marker."""
    current = getattr(customization, "scoring_schema_version", None)
    if current in {SCORING_SCHEMA_VERSION, SCORING_SCHEMA_VERSION_LEGACY}:
        if current != SCORING_SCHEMA_VERSION and institutional_scoring_enabled():
            return customization.model_copy(
                update={"scoring_schema_version": SCORING_SCHEMA_VERSION}
            )
        return customization
    return customization.model_copy(update={"scoring_schema_version": SCORING_SCHEMA_VERSION})


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def _score_from_delta(delta: float) -> float:
    """Map legacy +/- point deltas to 0-100 scale (50 = neutral)."""
    return _clamp_score(50.0 + delta)


@dataclass(frozen=True)
class PhaseAMetricsInput:
    """OHLCV-derived metrics for per-indicator Phase-A decomposition."""

    ema7: float | None
    ema14: float | None
    ema21: float | None
    ema42: float | None
    ema100: float | None
    ema200: float | None
    macd_hist: float | None
    rsi: float | None
    rsi_hist: float | None
    price: float
    vwap: float | None
    relative_volume: float
    period_change_pct: float
    supertrend_dir: float | None
    bbp: float | None
    atr_pct: float
    vix: float | None


def _ema_pair_score(fast: float | None, slow: float | None) -> float | None:
    if fast is None or slow is None or not (math.isfinite(fast) and math.isfinite(slow)):
        return None
    if fast > slow:
        return _score_from_delta(7.0)
    if fast < slow:
        return _score_from_delta(-7.0)
    return 50.0


def decompose_timeframe_signal(
    metrics: PhaseAMetricsInput,
    *,
    weights: dict[str, float] | None = None,
) -> tuple[dict[str, float], list[str], list[str], int, int]:
    """Return per-indicator 0-100 scores, reasons, warnings, bullish_votes, bearish_votes."""
    scores: dict[str, float] = {}
    reasons: list[str] = []
    warnings: list[str] = []
    bullish_votes = 0
    bearish_votes = 0

    def _include(key: str) -> bool:
        if weights is None:
            return True
        return weights.get(key, 1.0) > 0

    ema_pairs = (
        ("ema_7_14", metrics.ema7, metrics.ema14, "EMA 7/14"),
        ("ema_21_42", metrics.ema21, metrics.ema42, "EMA 21/42"),
        ("ema_100_200", metrics.ema100, metrics.ema200, "EMA 100/200"),
    )
    for key, fast, slow, label in ema_pairs:
        if not _include(key):
            continue
        s = _ema_pair_score(fast, slow)
        if s is None:
            continue
        scores[key] = s
        if s > 52:
            bullish_votes += 1
            reasons.append(f"{label} bullish")
        elif s < 48:
            bearish_votes += 1

    if _include("macd") and metrics.macd_hist is not None and math.isfinite(metrics.macd_hist):
        if metrics.macd_hist > 0:
            scores["macd"] = _score_from_delta(8.0)
            bullish_votes += 1
            reasons.append("MACD bull")
        elif metrics.macd_hist < 0:
            scores["macd"] = _score_from_delta(-8.0)
            bearish_votes += 1

    if _include("rsi") and metrics.rsi is not None and math.isfinite(metrics.rsi):
        rsi = metrics.rsi
        if 50 <= rsi <= 72:
            scores["rsi"] = _score_from_delta(8.0)
            reasons.append("RSI momentum zone")
        elif rsi > 82:
            scores["rsi"] = _score_from_delta(-8.0)
            warnings.append("WARN_RSI_EXTENDED")
        elif rsi < 35:
            scores["rsi"] = _score_from_delta(-7.0)

    if _include("rsi_hist") and metrics.rsi_hist is not None and math.isfinite(metrics.rsi_hist):
        if metrics.rsi_hist > 2.0:
            scores["rsi_hist"] = _score_from_delta(5.0)
            reasons.append("RSI momentum bullish")
        elif metrics.rsi_hist < -2.0:
            scores["rsi_hist"] = _score_from_delta(-5.0)
            reasons.append("RSI momentum bearish")

    if _include("avwap_vwap") and metrics.vwap is not None and math.isfinite(metrics.vwap):
        if metrics.price > metrics.vwap:
            scores["avwap_vwap"] = _score_from_delta(7.0)
            bullish_votes += 1
            reasons.append("Price above VWAP")
        elif metrics.price < metrics.vwap:
            scores["avwap_vwap"] = _score_from_delta(-7.0)
            bearish_votes += 1

    if _include("volume"):
        if metrics.relative_volume >= 1.5:
            scores["volume"] = _score_from_delta(8.0)
            reasons.append("Relative volume expansion")
        elif metrics.relative_volume < 0.2:
            scores["volume"] = _score_from_delta(-5.0)
            warnings.append("WARN_LOW_RVOL")

    if _include("relative_strength"):
        if metrics.period_change_pct > 2.0:
            scores["relative_strength"] = _score_from_delta(6.0)
            reasons.append("Positive period momentum")
        elif metrics.period_change_pct < -2.0:
            scores["relative_strength"] = _score_from_delta(-6.0)
            reasons.append("Negative period momentum")

    if (
        _include("supertrend")
        and metrics.supertrend_dir is not None
        and math.isfinite(metrics.supertrend_dir)
    ):
        if metrics.supertrend_dir > 0:
            scores["supertrend"] = _score_from_delta(5.0)
            bullish_votes += 1
            reasons.append("SuperTrend bull")
        elif metrics.supertrend_dir < 0:
            scores["supertrend"] = _score_from_delta(-5.0)
            bearish_votes += 1

    if _include("bbp") and metrics.bbp is not None and math.isfinite(metrics.bbp):
        if metrics.bbp > 0.8:
            scores["bbp"] = _score_from_delta(3.0)
            reasons.append("BBP strong upper")
        elif metrics.bbp < 0.2:
            scores["bbp"] = _score_from_delta(-3.0)

    if _include("prf"):
        if metrics.atr_pct < 0.05:
            warnings.append("atr_too_compressed")
            scores["prf"] = _score_from_delta(-10.0)
        elif metrics.atr_pct > 12.0:
            warnings.append("atr_too_extended")
            scores["prf"] = _score_from_delta(-10.0)
        elif 0.2 <= metrics.atr_pct <= 5.0:
            scores["prf"] = _score_from_delta(4.0)

    if _include("vix") and metrics.vix is not None and math.isfinite(metrics.vix):
        if metrics.vix > 30:
            scores["vix"] = _score_from_delta(-10.0)
            reasons.append(f"High broad volatility (VIX={metrics.vix:.1f})")
        elif metrics.vix < 15:
            scores["vix"] = _score_from_delta(5.0)
            reasons.append(f"Low broad volatility (VIX={metrics.vix:.1f})")

    return scores, reasons, warnings, bullish_votes, bearish_votes


def weighted_indicator_composite(
    indicator_scores: dict[str, float],
    weights: dict[str, float] | None,
    *,
    neutral_prior: float = 50.0,
) -> float:
    """Combine per-indicator 0-100 scores into a timeframe score.

    * No user weights: active-return **sum** (50 + Σ(s_i − 50)) — preserves
      multi-factor confluence stacking like the legacy heuristic.
    * With user weights: weighted average Σ(w_i × s_i) / Σ w_i; keys in the
      weight matrix but absent from decomposition use *neutral_prior*.
    """
    if not indicator_scores and not weights:
        return neutral_prior

    if weights is None:
        excess = sum(float(s) - neutral_prior for s in indicator_scores.values())
        return _clamp_score(neutral_prior + excess)

    active = {k: float(v) for k, v in weights.items() if float(v) > 0}
    if not active:
        return neutral_prior

    total_w = 0.0
    weighted = 0.0
    for key, w in active.items():
        score_val = float(indicator_scores.get(key, neutral_prior))
        total_w += w
        weighted += score_val * w
    if total_w <= 0:
        return neutral_prior
    return _clamp_score(weighted / total_w)


def composite_base_score_from_signals(
    signals: list[Any],
    effective_weights: dict[str, dict[str, float]] | None,
    primary_timeframe: str | None,
) -> tuple[float, dict[str, float]]:
    """Aggregate per-indicator scores across timeframes (primary TF preferred)."""
    merged: dict[str, list[float]] = {}
    for signal in signals:
        if not getattr(signal, "ok", False):
            continue
        tf = str(getattr(signal, "timeframe", ""))
        ind_scores = getattr(signal, "indicator_scores", None) or {}
        for key, val in ind_scores.items():
            merged.setdefault(key, []).append(float(val))

    if not merged:
        usable = [s for s in signals if getattr(s, "ok", False)]
        if not usable:
            return 0.0, {}
        return float(sum(s.score for s in usable) / len(usable)), {}

    tf_for_weights = primary_timeframe or (
        str(getattr(signals[0], "timeframe", "15m")) if signals else "15m"
    )
    weight_map: dict[str, float] | None = None
    if effective_weights:
        weight_map = {
            key: effective_weight_for_timeframe(
                effective_weights, key, tf_for_weights, catalog_default=1.0
            )
            for key in merged
        }

    averaged: dict[str, float] = {key: sum(vals) / len(vals) for key, vals in merged.items()}
    return weighted_indicator_composite(averaged, weight_map), averaged


def compute_universe_stats(rows: list[MarketScannerRow]) -> dict[str, float]:
    scores = [float(row.scanner_score) for row in rows if row.scanner_score is not None]
    if not scores:
        return {"count": 0.0, "mean": 50.0, "std": 0.0, "min": 0.0, "max": 100.0}
    mean = sum(scores) / len(scores)
    if len(scores) < 2:
        std = 0.0
    else:
        var = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
        std = float(math.sqrt(var))
    return {
        "count": float(len(scores)),
        "mean": round(mean, 4),
        "std": round(std, 4),
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
    }


def attach_cross_sectional_scores(rows: list[MarketScannerRow]) -> dict[str, float]:
    """Attach z-score, percentile (0-100), and rank to each row in-place."""
    stats = compute_universe_stats(rows)
    scores = [float(row.scanner_score) for row in rows]
    if not scores:
        return stats

    n = len(scores)
    mean = stats["mean"]
    std = stats["std"]

    for row in rows:
        s = float(row.scanner_score)
        if std > 1e-9:
            row.universe_z_score = round((s - mean) / std, 4)
        else:
            row.universe_z_score = 0.0

        # Percentile: share of universe with score <= s (higher = better)
        rank_below = sum(1 for other in scores if other < s)
        row.universe_percentile = round(100.0 * rank_below / max(n - 1, 1), 2) if n > 1 else 50.0

        higher = sum(1 for other in scores if other > s)
        row.universe_rank = int(higher + 1)

    return stats


def _resolve_regime_multiplier(
    indicator_key: str,
    regime_multipliers: dict[str, float] | None,
) -> float:
    if not regime_multipliers:
        return 1.0
    if indicator_key in regime_multipliers:
        return float(regime_multipliers[indicator_key])
    for legacy_key, catalog_key in REGIME_INDICATOR_ALIASES.items():
        if catalog_key == indicator_key and legacy_key in regime_multipliers:
            return float(regime_multipliers[legacy_key])
    return 1.0


def _raw_weight_for_indicator(
    customization: ScannerCustomization,
    indicator: ScannerIndicatorDefinition,
    timeframe: str,
) -> float:
    custom = customization.weight_matrix.get(indicator.key, {}).get(timeframe)
    if custom is not None:
        return float(custom)
    if indicator.default_enabled:
        return float(indicator.weight_by_timeframe.get(timeframe, 0.0))  # type: ignore[arg-type]
    return 0.0


def compute_effective_weights(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition] | None = None,
    *,
    regime_multipliers: dict[str, float] | None = None,
    apply_data_tier: bool = True,
) -> dict[str, dict[str, float]]:
    """User weights × regime multipliers, capped at WEIGHT_SOFT_CAP."""
    from backend.services.market_scanner_data_tier_policy import (
        apply_data_tier_to_effective_weights,
    )

    catalog = indicators or list_indicator_definitions()
    enabled = set(customization.enabled_indicators or [])
    use_enabled_filter = customization.enabled_indicators is not None

    effective: dict[str, dict[str, float]] = {}
    for indicator in catalog:
        if use_enabled_filter and indicator.key not in enabled:
            continue
        tf_map: dict[str, float] = {}
        for timeframe in indicator.supports_timeframes:
            raw = _raw_weight_for_indicator(customization, indicator, timeframe)
            if raw <= 0:
                continue
            mult = _resolve_regime_multiplier(indicator.key, regime_multipliers)
            tf_map[timeframe] = round(min(raw * mult, WEIGHT_SOFT_CAP), 4)
        if tf_map:
            effective[indicator.key] = tf_map
    if apply_data_tier and institutional_scoring_enabled():
        effective, _ = apply_data_tier_to_effective_weights(effective, catalog)
    return effective


def compute_effective_weights_with_audit(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition] | None = None,
    *,
    regime_multipliers: dict[str, float] | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Like ``compute_effective_weights`` but returns data-tier audit metadata."""
    from backend.services.market_scanner_data_tier_policy import (
        apply_data_tier_to_effective_weights,
        data_tier_policy_enabled,
    )

    catalog = indicators or list_indicator_definitions()
    raw = compute_effective_weights(
        customization,
        catalog,
        regime_multipliers=regime_multipliers,
        apply_data_tier=False,
    )
    if institutional_scoring_enabled() and data_tier_policy_enabled():
        return apply_data_tier_to_effective_weights(raw, catalog)
    return raw, {"enabled": False}


def effective_weight_for_timeframe(
    effective_weights: dict[str, dict[str, float]],
    indicator_key: str,
    timeframe: str,
    *,
    catalog_default: float = 0.0,
) -> float:
    return float(effective_weights.get(indicator_key, {}).get(timeframe, catalog_default))


def timeframe_weight_sum(
    effective_weights: dict[str, dict[str, float]],
    timeframe: str,
) -> float:
    return sum(tf_map.get(timeframe, 0.0) for tf_map in effective_weights.values())


def normalize_engine_weights(
    base_engine_weights: dict[str, float],
    engine_to_indicator: dict[str, str],
    effective_weights: dict[str, dict[str, float]],
    primary_timeframe: str,
) -> dict[str, float]:
    """Multiply engine base weights by user effective weights (normalized)."""
    scaled: dict[str, float] = {}
    for engine_key, base_w in base_engine_weights.items():
        ind_key = engine_to_indicator.get(engine_key, engine_key)
        user_w = effective_weight_for_timeframe(effective_weights, ind_key, primary_timeframe)
        if user_w <= 0:
            continue
        scaled[engine_key] = base_w * user_w
    total = sum(scaled.values())
    if total <= 0:
        # Fallback: equal weight among engines with positive base weight
        active = {k: v for k, v in base_engine_weights.items() if v > 0}
        if not active:
            return dict(base_engine_weights)
        eq = 1.0 / len(active)
        return {k: eq for k in active}
    return {k: v / total for k, v in scaled.items()}


def module_blend_weight(
    module: str,
    effective_weights: dict[str, dict[str, float]],
    indicators: list[ScannerIndicatorDefinition],
    primary_timeframe: str,
) -> float:
    """Desk weight for a Phase-B module from enabled indicators on primary TF."""
    module_indicators = [
        i
        for i in indicators
        if i.module == module or (module == "technical" and i.module == "core")
    ]
    if module == "options_gex":
        module_indicators = [i for i in indicators if i.module == "options_gex"]
    elif module == "probabilistic":
        module_indicators = [i for i in indicators if i.module == "probabilistic"]
    elif module == "technical":
        module_indicators = [
            i
            for i in indicators
            if i.module in {"technical", "core"} and i.key in TECHNICAL_ENGINE_TO_INDICATOR.values()
        ]

    weights_on_tf = [
        effective_weights.get(ind.key, {}).get(primary_timeframe, 0.0)
        for ind in module_indicators
        if primary_timeframe in ind.supports_timeframes
    ]
    if not weights_on_tf:
        return DEFAULT_MODULE_BLEND_WEIGHTS.get(module, 1.0)
    mean_w = sum(weights_on_tf) / len(weights_on_tf)
    base = DEFAULT_MODULE_BLEND_WEIGHTS.get(module, 1.0)
    return max(0.05, min(2.5, base * (mean_w / 3.0)))


def weight_scale_factor(weight: float, *, reference: float = 3.0) -> float:
    """Scale fixed score deltas by effective weight / reference (catalog mid-weight)."""
    if weight <= 0:
        return 0.0
    return max(0.0, min(2.0, weight / reference))


def weight_concentration_audit(
    effective_weights: dict[str, dict[str, float]],
    *,
    top_n: int = 3,
    threshold: float = 0.80,
) -> dict[str, Any]:
    """L2-style concentration guard: flag when top-N indicators dominate total weight."""
    totals: list[tuple[str, float]] = []
    for key, tf_map in effective_weights.items():
        totals.append((key, sum(tf_map.values())))
    grand = sum(w for _, w in totals)
    if grand <= 0:
        return {"weight_concentration_warning": False, "top_share": 0.0, "top_indicators": []}
    totals.sort(key=lambda item: item[1], reverse=True)
    top = totals[:top_n]
    top_share = sum(w for _, w in top) / grand
    warning = top_share >= threshold
    return {
        "weight_concentration_warning": warning,
        "top_share": round(top_share, 4),
        "top_indicators": [k for k, _ in top],
        "concentration_penalty": (
            round(min(3.0, (top_share - threshold) * 15.0), 2) if warning else 0.0
        ),
    }


def apply_concentration_penalty(score: float, audit: dict[str, Any]) -> float:
    penalty = float(audit.get("concentration_penalty") or 0.0)
    if penalty <= 0:
        return score
    return max(0.0, min(100.0, score - penalty))


def enabled_modules_from_weights(
    effective_weights: dict[str, dict[str, float]],
    indicators: list[ScannerIndicatorDefinition],
) -> dict[ScannerModuleKey, float]:
    """Aggregate effective weight mass per module (for blend)."""
    out: dict[str, float] = {}
    ind_by_key = {i.key: i for i in indicators}
    for key, tf_map in effective_weights.items():
        ind = ind_by_key.get(key)
        if ind is None:
            continue
        mod = ind.module
        mass = sum(tf_map.values())
        out[mod] = out.get(mod, 0.0) + mass
    return out  # type: ignore[return-value]
