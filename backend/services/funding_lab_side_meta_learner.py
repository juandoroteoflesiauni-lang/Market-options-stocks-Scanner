from __future__ import annotations
from typing import Any
"""High-conviction heuristic confirmation for Funding Lab.

The original side provider contract is preserved, but the decision is now a
deterministic rules gate derived from the real-data filters that outperformed
the small Meta-Learner experiment.
"""



from backend.config.logger_setup import get_logger

DEFAULT_SIDE_META_THRESHOLD = 1.0

LONG_MIN_TREND_SCORE = 0.75
SHORT_MAX_TREND_SCORE = -0.70
MIN_VOLUME_SCORE = 0.60
MAX_ABS_MEAN_REVERSION = 0.80
MAX_LONG_RETURN_5D = 0.08
MIN_SHORT_VOL_RATIO = 0.60
MAX_SHORT_VOL_RATIO = 1.30

REASON_SIDE_META_INVALID_DIRECTION = "side_meta_invalid_direction"
REASON_SIDE_META_FEATURE_MISSING = "side_meta_feature_missing"
REASON_SIDE_META_TREND_SCORE_TOO_LOW = "side_meta_trend_score_too_low"
REASON_SIDE_META_TREND_SCORE_TOO_HIGH = "side_meta_trend_score_too_high"
REASON_SIDE_META_VOLUME_SCORE_TOO_LOW = "side_meta_volume_score_too_low"
REASON_SIDE_META_OVEREXTENDED = "side_meta_overextended"
REASON_SIDE_META_STRUCTURE_NOT_BEARISH = "side_meta_structure_not_bearish"
REASON_SIDE_META_VWAP_NOT_BEARISH = "side_meta_vwap_not_bearish"
REASON_SIDE_META_VOLATILITY_REGIME_UNCONTROLLED = "side_meta_volatility_regime_uncontrolled"

# Backward-compatible reason kept so existing callers/tests can still classify
# generic side-meta rejections without understanding every heuristic reason.
REASON_SIDE_META_PROBABILITY_TOO_LOW = "side_meta_probability_too_low"

_LONG_DIRECTIONS = {"UP", "LONG", "BUY", "BULLISH"}
_SHORT_DIRECTIONS = {"DOWN", "SHORT", "SELL", "BEARISH"}

logger = get_logger(__name__)


async def get_side_meta_confirmation(
    *,
    scanner_row: dict[str, Any],
    entry_direction: str,
    min_probability: float = DEFAULT_SIDE_META_THRESHOLD,
) -> dict[str, Any]:
    """Evaluate high-conviction heuristic confirmation for the proposed side."""
    del min_probability  # retained for provider protocol compatibility
    side = _side_from_direction(entry_direction)
    if side is None:
        return _result(
            status="FAIL",
            side="unknown",
            score=0.0,
            reasons=[REASON_SIDE_META_INVALID_DIRECTION],
            metrics={},
        )

    features = _features_from_scanner_row(scanner_row)
    if side == "long":
        reasons = _long_reasons(features)
        score = _long_score(features)
    else:
        reasons = _short_reasons(features)
        score = _short_score(features)

    return _result(
        status="PASS" if not reasons else "FAIL",
        side=side,
        score=score,
        reasons=reasons,
        metrics=_audit_metrics(features),
    )


def _side_from_direction(entry_direction: str) -> str | None:
    normalized = str(entry_direction or "").strip().upper()
    if normalized in _LONG_DIRECTIONS:
        return "long"
    if normalized in _SHORT_DIRECTIONS:
        return "short"
    return None


def _long_reasons(features: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    trend = features.get("vsa_forecast__trend_score")
    volume = features.get("vsa_forecast__volume_score")
    mean_rev = features.get("price__mean_rev_signal")
    return_5d = features.get("price__return_5d")

    if trend is None:
        reasons.append(REASON_SIDE_META_FEATURE_MISSING)
    elif trend < LONG_MIN_TREND_SCORE:
        reasons.append(REASON_SIDE_META_TREND_SCORE_TOO_LOW)

    if volume is None:
        reasons.append(REASON_SIDE_META_FEATURE_MISSING)
    elif volume < MIN_VOLUME_SCORE:
        reasons.append(REASON_SIDE_META_VOLUME_SCORE_TOO_LOW)

    if _is_long_overextended(mean_rev=mean_rev, return_5d=return_5d):
        reasons.append(REASON_SIDE_META_OVEREXTENDED)

    return _dedupe(reasons)


def _short_reasons(features: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    trend = features.get("vsa_forecast__trend_score")
    volume = features.get("vsa_forecast__volume_score")
    structure = features.get("technical__market_structure_trend")
    vwap_distance = features.get("technical__vwap_distance")
    mean_rev = features.get("price__mean_rev_signal")
    rsi_14 = features.get("price__rsi_14")
    vol_ratio = features.get("price__vol_ratio_5_20")

    if trend is None:
        reasons.append(REASON_SIDE_META_FEATURE_MISSING)
    elif trend > SHORT_MAX_TREND_SCORE:
        reasons.append(REASON_SIDE_META_TREND_SCORE_TOO_HIGH)

    if volume is None:
        reasons.append(REASON_SIDE_META_FEATURE_MISSING)
    elif volume < MIN_VOLUME_SCORE:
        reasons.append(REASON_SIDE_META_VOLUME_SCORE_TOO_LOW)

    if structure is None:
        reasons.append(REASON_SIDE_META_FEATURE_MISSING)
    elif structure != -1.0:
        reasons.append(REASON_SIDE_META_STRUCTURE_NOT_BEARISH)

    if vwap_distance is None:
        reasons.append(REASON_SIDE_META_FEATURE_MISSING)
    elif vwap_distance >= 0.0:
        reasons.append(REASON_SIDE_META_VWAP_NOT_BEARISH)

    if not _short_has_exhaustion_or_reversion(rsi_14=rsi_14, mean_rev=mean_rev):
        reasons.append(REASON_SIDE_META_OVEREXTENDED)

    if vol_ratio is None:
        reasons.append(REASON_SIDE_META_FEATURE_MISSING)
    elif not MIN_SHORT_VOL_RATIO <= vol_ratio <= MAX_SHORT_VOL_RATIO:
        reasons.append(REASON_SIDE_META_VOLATILITY_REGIME_UNCONTROLLED)

    return _dedupe(reasons)


def _is_long_overextended(*, mean_rev: float | None, return_5d: float | None) -> bool:
    if mean_rev is not None and abs(mean_rev) > MAX_ABS_MEAN_REVERSION:
        return True
    return return_5d is not None and return_5d > MAX_LONG_RETURN_5D


def _short_has_exhaustion_or_reversion(*, rsi_14: float | None, mean_rev: float | None) -> bool:
    if rsi_14 is not None and rsi_14 >= 55.0:
        return True
    return mean_rev is not None and mean_rev < 0.0


def _long_score(features: dict[str, float]) -> float:
    trend = features.get("vsa_forecast__trend_score", 0.0)
    volume = features.get("vsa_forecast__volume_score", 0.0)
    mean_rev = abs(features.get("price__mean_rev_signal", 0.0))
    extension_penalty = min(mean_rev / MAX_ABS_MEAN_REVERSION, 1.0) * 0.15
    return max(0.0, min(1.0, trend * 0.65 + volume * 0.35 - extension_penalty))


def _short_score(features: dict[str, float]) -> float:
    trend = abs(features.get("vsa_forecast__trend_score", 0.0))
    volume = features.get("vsa_forecast__volume_score", 0.0)
    structure = 1.0 if features.get("technical__market_structure_trend") == -1.0 else 0.0
    vwap = 1.0 if features.get("technical__vwap_distance", 1.0) < 0.0 else 0.0
    return max(0.0, min(1.0, trend * 0.45 + volume * 0.25 + structure * 0.20 + vwap * 0.10))


def _features_from_scanner_row(row: dict[str, Any]) -> dict[str, float]:
    features: dict[str, float] = {}
    if not isinstance(row, dict):
        return features

    _flatten_numeric("", row.get("features"), features)
    _flatten_numeric("", row.get("motor_signals"), features)
    _flatten_numeric("", row.get("module_signals"), features)

    for key in (
        "trend_score",
        "volume_score",
        "market_structure_trend",
        "vwap_distance",
        "mean_rev_signal",
        "return_5d",
        "rsi_14",
        "vol_ratio_5_20",
    ):
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            features[key] = float(value)

    _copy_alias(features, "trend_score", "vsa_forecast__trend_score")
    _copy_alias(features, "volume_score", "vsa_forecast__volume_score")
    _copy_alias(features, "mean_rev_signal", "price__mean_rev_signal")
    _copy_alias(features, "return_5d", "price__return_5d")
    _copy_alias(features, "rsi_14", "price__rsi_14")
    _copy_alias(features, "vol_ratio_5_20", "price__vol_ratio_5_20")
    _copy_alias(features, "market_structure_trend", "technical__market_structure_trend")
    _copy_alias(features, "vwap_distance", "technical__vwap_distance")
    return features


def _flatten_numeric(prefix: str, value: Any, out: dict[str, float]) -> None:
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        name = f"{prefix}__{key}" if prefix else str(key)
        if isinstance(item, bool):
            continue
        if isinstance(item, int | float):
            out[name] = float(item)
        elif isinstance(item, dict):
            _flatten_numeric(name, item, out)


def _copy_alias(features: dict[str, float], source: str, target: str) -> None:
    if target not in features and source in features:
        features[target] = features[source]


def _audit_metrics(features: dict[str, float]) -> dict[str, float | None]:
    keys = (
        "vsa_forecast__trend_score",
        "vsa_forecast__volume_score",
        "price__mean_rev_signal",
        "price__return_5d",
        "technical__market_structure_trend",
        "technical__vwap_distance",
        "price__rsi_14",
        "price__vol_ratio_5_20",
    )
    return {key: features.get(key) for key in keys}


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _result(
    *,
    status: str,
    side: str,
    score: float,
    reasons: list[str],
    metrics: dict[str, float | None],
) -> dict[str, Any]:
    return {
        "status": status,
        "side": side,
        "probability": round(float(score), 6),
        "threshold": DEFAULT_SIDE_META_THRESHOLD,
        "model_path": "heuristic",
        "reasons": reasons,
        "heuristic_score": round(float(score), 6),
        "metrics": metrics,
    }
