"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              ENSEMBLE META-LEARNER  —  Top-Layer Directional Predictor       ║
║                                                                              ║
║  Consumes the outputs of every other engine as features, predicts the        ║
║  forward direction of the underlying with calibrated probabilities.          ║
║                                                                              ║
║  Pipeline:                                                                   ║
║    1.  build_feature_matrix()   — engine outputs → tabular features          ║
║    2.  create_targets()         — forward returns → categorical targets       ║
║    3.  temporal_split()         — strict walk-forward CV indices             ║
║    4.  EnsembleMetaLearner.fit  — gradient boosting + isotonic calibration   ║
║    5.  predict_proba()          — calibrated {UP, DOWN, NEUTRAL} dict        ║
║                                                                              ║
║  Backend selection (in priority order):                                      ║
║    - lightgbm   (preferred, fast on tabular data)                            ║
║    - xgboost    (fallback)                                                   ║
║    - sklearn GradientBoostingClassifier (last resort, always available)      ║
║                                                                              ║
║  Calibration:                                                                ║
║    Isotonic regression per class on a held-out fold to map raw model         ║
║    probabilities onto well-calibrated probabilities (Brier-score optimised). ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# ruff: noqa: ANN101, ANN102, ANN401, N803, N806
from __future__ import annotations

import importlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import joblib  # type: ignore[import-not-found, import-untyped]
import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]
from sklearn.ensemble import (
    HistGradientBoostingClassifier,  # type: ignore[import-not-found, import-untyped]
)
from sklearn.linear_model import (
    LogisticRegression,  # type: ignore[import-not-found, import-untyped]
)
from sklearn.metrics import (  # type: ignore[import-not-found, import-untyped]
    accuracy_score,
    log_loss,
    precision_score,
)

from backend.config.logger_setup import get_logger
from backend.layer_3_specialists.ia_probabilistico.engines.ensemble_training import (
    _compute_sample_weights,
    evaluate_fold,
    train_meta_learner,
    validate_no_leakage,
)
from backend.layer_3_specialists.ia_probabilistico.engines.ensemble_training_models import (
    TrainingConfig,
    TrainingResult,
)

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Soft imports — gracefully degrade when optional ML stacks are absent
# ──────────────────────────────────────────────────────────────────────────────


def _optional_ml_import(module_name: str, label: str) -> tuple[Any | None, bool]:
    try:
        return importlib.import_module(module_name), True
    except Exception as exc:
        logger.warning(
            "%s unavailable at import time; falling back when possible. reason=%s",
            label,
            exc,
        )
        return None, False


lgb, _LGB_AVAILABLE = _optional_ml_import("lightgbm", "lightgbm")
xgb, _XGB_AVAILABLE = _optional_ml_import("xgboost", "xgboost")
shap, _SHAP_AVAILABLE = _optional_ml_import("shap", "shap")

__all__ = [
    "EnsembleMetaLearner",
    "TrainingConfig",
    "TrainingResult",
    "_compute_sample_weights",
    "build_feature_matrix",
    "calibrate_model",
    "create_targets",
    "evaluate_fold",
    "get_calibration_curve",
    "temporal_split",
    "train_meta_learner",
    "validate_no_leakage",
]


# ──────────────────────────────────────────────────────────────────────────────
# Constants — feature schema
# ──────────────────────────────────────────────────────────────────────────────

# Engines whose top-level numeric scalar fields become features.
# Listed here as documentation and to drive build_feature_matrix() selection.
_ENGINE_FEATURE_FIELDS: dict[str, list[str]] = {
    "price": [
        "return_1d",
        "return_5d",
        "return_10d",
        "return_20d",
        "realized_vol_5d",
        "realized_vol_20d",
        "price_vs_ma20",
        "price_vs_ma50",
        "price_vs_ma200",
        "rsi_14_normalized",
        "rsi_14",
        "vol_ratio_5_20",
        "mean_rev_signal",
    ],
    "tail_risk": [
        "skew_25d",
        "convexity_25d",
        "iv_atm",
        "convexity_percentile",
        "q_skewness",
        "q_kurtosis",
        "implied_skew_signal",
        "tail_asymmetry",
        "tail_score",
        "directional_signal",
    ],
    "gamma_flip": [
        "flip_point",
        "flip_signal",
        "dex_net",
        "gex_net",
        "ndde",
        "directional_signal",
    ],
    "vsa_forecast": [
        "trend_score",
        "volume_score",
        "absorption",
        "exhaustion",
        "vsa_signal",
    ],
    "sentiment": [
        "sentiment_score",
        "sentiment_zscore",
        "news_volume_norm",
    ],
    "fear_greed": [
        "fear_greed_normalized",  # already in [-1, 1] or [0, 1]
    ],
    "cross_asset": [
        "correlation_signal",
        "regime_divergence",
        "cross_asset_signal",
    ],
    "squeeze": [
        "squeeze_intensity",
        "bb_kc_ratio",
        "squeeze_score",
    ],
    "shadow_delta": [
        "shadow_delta",
        "shadow_delta_zscore",
    ],
    "zomma": [
        "zomma_total",
        "zomma_signal",
    ],
    "speed_instability": [
        "speed_score",
        "instability_index",
    ],
    "volatility_skew": [
        "rr_25d",
        "fly_25d",
        "skew_slope",
    ],
    "rnd": [
        "q_skewness",
        "q_kurtosis",
        "modal_price_pct",
        "modal_price_pct_diff",
        "is_bimodal",
    ],
    "dealer_flow": [
        "ndde_normalized",
        "charm_flow_net",
        "vanna_pressure",
        "pinning_probability",
    ],
    "hmm": [
        "prob_bull_quiet",
        "prob_bear_volatile",
        "prob_chaotic",
    ],
    "macro_regime": [
        "macro_confidence",
        "macro_bull_prior",
        "macro_bear_prior",
    ],
    "orchestrator": [
        "conflict_score",
        "regime_prob_bull",
        "regime_prob_bear",
        "regime_prob_neutral",
        "confidence",
        "signal",
    ],
}

_DTE_CATEGORIES = ("zero_dte", "weekly", "monthly")

REQUIRED_META_FEATURES: tuple[str, ...] = (
    "return_1d",
    "return_5d",
    "realized_vol_5d",
    "realized_vol_20d",
    "conflict_score",
    "confidence",
    "signal",
    "prob_bull_quiet",
    "prob_bear_volatile",
    "prob_chaotic",
    "q_skewness",
    "q_kurtosis",
    "modal_price_pct",
    "ndde_normalized",
    "vanna_pressure",
    "pinning_probability",
    "tail_score",
    "implied_skew_signal",
    "tail_asymmetry",
    "flip_signal",
    "gex_net",
    "squeeze_score",
    "sentiment_score",
    "fear_greed_normalized",
    "vsa_signal",
    "cross_asset_signal",
    "macro_confidence",
    "macro_bull_prior",
    "macro_bear_prior",
    "hour_sin",
    "hour_cos",
    "day_of_week",
)

_META_FEATURE_ALIASES: dict[str, tuple[str, str]] = {
    "return_1d": ("price", "return_1d"),
    "return_5d": ("price", "return_5d"),
    "return_20d": ("price", "return_20d"),
    "realized_vol_5d": ("price", "realized_vol_5d"),
    "realized_vol_20d": ("price", "realized_vol_20d"),
    "conflict_score": ("orchestrator", "conflict_score"),
    "confidence": ("orchestrator", "confidence"),
    "signal": ("orchestrator", "signal"),
    "prob_bull_quiet": ("hmm", "prob_bull_quiet"),
    "prob_bear_volatile": ("hmm", "prob_bear_volatile"),
    "prob_chaotic": ("hmm", "prob_chaotic"),
    "q_skewness": ("rnd", "q_skewness"),
    "q_kurtosis": ("rnd", "q_kurtosis"),
    "modal_price_pct": ("rnd", "modal_price_pct"),
    "ndde_normalized": ("dealer_flow", "ndde_normalized"),
    "vanna_pressure": ("dealer_flow", "vanna_pressure"),
    "pinning_probability": ("dealer_flow", "pinning_probability"),
    "tail_score": ("tail_risk", "tail_score"),
    "implied_skew_signal": ("tail_risk", "implied_skew_signal"),
    "tail_asymmetry": ("tail_risk", "tail_asymmetry"),
    "flip_signal": ("gamma_flip", "flip_signal"),
    "gex_net": ("gamma_flip", "gex_net"),
    "squeeze_score": ("squeeze", "squeeze_score"),
    "sentiment_score": ("sentiment", "sentiment_score"),
    "fear_greed_normalized": ("fear_greed", "fear_greed_normalized"),
    "vsa_signal": ("vsa_forecast", "vsa_signal"),
    "cross_asset_signal": ("cross_asset", "cross_asset_signal"),
    "macro_confidence": ("macro_regime", "macro_confidence"),
    "macro_bull_prior": ("macro_regime", "macro_bull_prior"),
    "macro_bear_prior": ("macro_regime", "macro_bear_prior"),
}

# Target thresholds
_DIRECTION_UP_THRESHOLD = 0.005  # 0.5 %
_DIRECTION_DOWN_THRESHOLD = -0.005

# Defaults
_DEFAULT_MODEL_TYPE = "lightgbm"
_RANDOM_STATE = 42

_LABEL_TO_IDX = {-1: 0, 0: 1, 1: 2}
_IDX_TO_LABEL = {0: "DOWN", 1: "NEUTRAL", 2: "UP"}


# ──────────────────────────────────────────────────────────────────────────────
# Feature → Motor (engine) reverse mapping for SHAP attribution
# ──────────────────────────────────────────────────────────────────────────────


def _build_feature_to_motor_map() -> dict[str, str]:
    """
    Auto-derive feature → originating motor mapping from _ENGINE_FEATURE_FIELDS.
    Adds entries for temporal & DTE features under the "context" pseudo-motor.
    """
    mapping: dict[str, str] = {}
    for engine_name, fields in _ENGINE_FEATURE_FIELDS.items():
        for fld in fields:
            mapping[f"{engine_name}__{fld}"] = engine_name
    for ctx in ("hour_of_day", "hour_sin", "hour_cos", "day_of_week"):
        mapping[ctx] = "context"
    for alias, (engine_name, _) in _META_FEATURE_ALIASES.items():
        mapping[alias] = engine_name
    for dte in _DTE_CATEGORIES:
        mapping[f"dte_{dte}"] = "context"
    return mapping


FEATURE_TO_MOTOR: dict[str, str] = _build_feature_to_motor_map()
KNOWN_MOTORS: set[str] = set(FEATURE_TO_MOTOR.values())


# ──────────────────────────────────────────────────────────────────────────────
# Public utility — feature-matrix builder
# ──────────────────────────────────────────────────────────────────────────────


def _flatten_engine_block(
    block: dict[str, Any] | None,
    engine_name: str,
    fields: list[str],
) -> dict[str, float]:
    """Extract `fields` from a single engine's output dict; missing → NaN."""
    out: dict[str, float] = {}
    for fld in fields:
        col = f"{engine_name}__{fld}"
        if not isinstance(block, dict):
            out[col] = np.nan
            continue
        val = block.get(fld)
        if val is None:
            out[col] = np.nan
        elif isinstance(val, bool | int | float | np.integer | np.floating):
            out[col] = float(val)
        else:
            out[col] = np.nan
    return out


def _temporal_features(timestamp: Any) -> dict[str, float]:
    """Extract hour-of-day, day-of-week from a timestamp-like value."""
    empty = {
        "hour_of_day": np.nan,
        "hour_sin": np.nan,
        "hour_cos": np.nan,
        "day_of_week": np.nan,
    }
    if timestamp is None:
        return empty
    try:
        ts = pd.Timestamp(timestamp)
    except (ValueError, TypeError):
        return empty
    hour_angle = 2.0 * np.pi * (ts.hour + ts.minute / 60.0) / 24.0
    return {
        "hour_of_day": float(ts.hour),
        "hour_sin": float(np.sin(hour_angle)),
        "hour_cos": float(np.cos(hour_angle)),
        "day_of_week": float(ts.dayofweek),
    }


def _dte_dummies(dte_category: str | None) -> dict[str, float]:
    """One-hot encode DTE category (zero_dte / weekly / monthly)."""
    cat = (dte_category or "").lower().strip()
    return {f"dte_{c}": float(cat == c) for c in _DTE_CATEGORIES}


def _meta_feature_aliases(row: dict[str, float]) -> dict[str, float]:
    """Expose canonical model-facing names in addition to engine namespaces."""
    aliases: dict[str, float] = {}
    for alias, (engine_name, field_name) in _META_FEATURE_ALIASES.items():
        aliases[alias] = row.get(f"{engine_name}__{field_name}", np.nan)
    return aliases


def _apply_price_directional_prior(
    X_aligned: pd.DataFrame,
    probabilities: np.ndarray[Any, np.dtype[Any]],
) -> np.ndarray[Any, np.dtype[Any]]:
    """Use explicit price momentum features as a small anti-inversion prior."""
    components: list[np.ndarray[Any, np.dtype[Any]]] = []
    feature_scales = {
        "price__return_5d": 0.05,
        "price__return_20d": 0.10,
        "price__rsi_14_normalized": 1.00,
        "price__price_vs_ma20": 0.05,
    }
    for col, scale in feature_scales.items():
        if col not in X_aligned:
            continue
        values = X_aligned[col].to_numpy(dtype=float)
        components.append(np.nan_to_num(values / scale, nan=0.0, posinf=0.0, neginf=0.0))
    if not components:
        return probabilities

    score = np.clip(np.mean(np.vstack(components), axis=0), -1.0, 1.0)
    if np.allclose(score, 0.0):
        return probabilities

    out = probabilities.copy()
    adjustment = 0.35 * score
    bull_mask = adjustment > 0
    bear_mask = adjustment < 0

    out[bull_mask, 2] += adjustment[bull_mask]
    out[bull_mask, 0] *= 1.0 - adjustment[bull_mask] * 0.5

    bear_adj = -adjustment[bear_mask]
    out[bear_mask, 0] += bear_adj
    out[bear_mask, 2] *= 1.0 - bear_adj * 0.5

    out = np.clip(out, 0.0, None)
    row_sums = out.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    return cast(np.ndarray[Any, np.dtype[Any]], out / row_sums)


def build_feature_matrix(engine_outputs_history: list[dict[str, Any]]) -> pd.DataFrame:
    """
    Convert a history of per-timestamp engine output dicts into a tabular feature matrix.

    Each entry of `engine_outputs_history` is expected to be a dict with shape:
        {
            "timestamp":      pd.Timestamp | str | None,
            "dte_category":   "zero_dte" | "weekly" | "monthly" | None,
            "tail_risk":      { ... engine output dict ... },
            "gamma_flip":     { ... },
            "vsa_forecast":   { ... },
            ... (any of the engines listed in _ENGINE_FEATURE_FIELDS)
        }

    Missing engines or fields are filled with NaN — downstream models handle it.

    Returns
    ───────
    pd.DataFrame
        One row per history entry, ~45-55 numeric columns.
        Index = timestamps when present, otherwise integer positional.
    """
    if not engine_outputs_history:
        return pd.DataFrame()

    rows: list[dict[str, float]] = []
    index: list[Any] = []

    for entry in engine_outputs_history:
        row: dict[str, float] = {}

        for engine_name, fields in _ENGINE_FEATURE_FIELDS.items():
            block = entry if engine_name == "price" else entry.get(engine_name)
            row.update(_flatten_engine_block(block, engine_name, fields))

        row.update(_temporal_features(entry.get("timestamp")))
        row.update(_dte_dummies(entry.get("dte_category")))
        row.update(_meta_feature_aliases(row))

        rows.append(row)
        index.append(entry.get("timestamp", len(index)))

    df = pd.DataFrame(rows, index=pd.Index(index, name="timestamp"))
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Public utility — target encoding
# ──────────────────────────────────────────────────────────────────────────────


def create_targets(
    price_series: pd.Series,
    forward_periods: Iterable[int] = (1, 5, 10),
) -> pd.DataFrame:
    """
    Build forward-looking targets for classification + regression.

    For each N in `forward_periods`:
        target_return_N    : forward log-return over N bars
        target_direction_N : {1=UP if return > +0.5%, -1=DOWN if < -0.5%, 0=NEUTRAL}

    The last N rows of each column will be NaN (no forward data).
    """
    if not isinstance(price_series, pd.Series):
        raise TypeError("price_series must be a pandas Series of prices.")

    out = pd.DataFrame(index=price_series.index)

    for n in forward_periods:
        if n <= 0:
            raise ValueError(f"forward_periods must be positive, got {n}")
        fwd = price_series.shift(-n) / price_series - 1.0
        direction = pd.Series(0, index=price_series.index, dtype="float64")
        direction[fwd > _DIRECTION_UP_THRESHOLD] = 1
        direction[fwd < _DIRECTION_DOWN_THRESHOLD] = -1
        # Tail rows have no forward price → NaN target
        direction.iloc[-n:] = np.nan
        out[f"target_return_{n}"] = fwd
        out[f"target_direction_{n}"] = direction

    return out


# ──────────────────────────────────────────────────────────────────────────────
# Public utility — walk-forward temporal split
# ──────────────────────────────────────────────────────────────────────────────


def temporal_split(
    df: pd.DataFrame,
    n_splits: int = 5,
    test_size: float = 0.20,
) -> list[tuple[np.ndarray[Any, np.dtype[Any]], np.ndarray[Any, np.dtype[Any]]]]:
    """
    Strict walk-forward cross-validation indices.

    The data is partitioned along the row order (assumed time-sorted). For each
    split the test window covers `test_size` × len(df) rows immediately after
    the training window. Training windows are expanding (cumulative).

    NEVER mixes future data with past — train_idx are always strictly before
    test_idx in row order.
    """
    n = len(df)
    if n < n_splits + 1:
        raise ValueError(f"DataFrame too small for {n_splits} splits (got {n} rows).")
    if not 0.0 < test_size < 1.0:
        raise ValueError("test_size must be in (0, 1).")

    test_window = max(1, int(round(n * test_size / n_splits)))
    splits: list[tuple[np.ndarray[Any, np.dtype[Any]], np.ndarray[Any, np.dtype[Any]]]] = []

    # First train window starts at min_train; subsequent splits step forward by test_window
    min_train = max(test_window, n - n_splits * test_window)
    if min_train <= 0:
        min_train = max(1, n // (n_splits + 1))

    for i in range(n_splits):
        train_end = min_train + i * test_window
        test_start = train_end
        test_end = min(test_start + test_window, n)
        if test_end <= test_start or train_end <= 0:
            continue
        train_idx = np.arange(0, train_end)
        test_idx = np.arange(test_start, test_end)
        splits.append((train_idx, test_idx))

    if not splits:
        raise ValueError("Could not build any walk-forward split with the given parameters.")
    return splits


# ──────────────────────────────────────────────────────────────────────────────
# Calibration container
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _CalibratorBundle:
    """Multiclass Platt calibrator fitted on held-out raw probabilities."""

    calibrator: LogisticRegression | None = None
    calibrators: dict[int, LogisticRegression] = field(default_factory=dict)
    is_fitted: bool = False
    method: str = "sigmoid"

    def fit(
        self, raw_probs: np.ndarray[Any, np.dtype[Any]], y_true: np.ndarray[Any, np.dtype[Any]]
    ) -> None:
        """raw_probs shape (n, 3); y_true shape (n,) with values in {0, 1, 2}."""
        y_arr = np.asarray(y_true, dtype=int)
        if len(np.unique(y_arr)) < 2:
            logger.warning("Platt calibration skipped: calibration fold has one class.")
            return

        lr = LogisticRegression(
            class_weight="balanced",
            max_iter=1000,
            random_state=_RANDOM_STATE,
        )
        lr.fit(np.asarray(raw_probs, dtype=float), y_arr)
        self.calibrator = lr
        self.calibrators = {cls_idx: lr for cls_idx in range(raw_probs.shape[1])}
        self.is_fitted = True

    def transform(
        self, raw_probs: np.ndarray[Any, np.dtype[Any]]
    ) -> np.ndarray[Any, np.dtype[Any]]:
        if not self.is_fitted or self.calibrator is None:
            return raw_probs
        proba = self.calibrator.predict_proba(np.asarray(raw_probs, dtype=float))
        classes = getattr(self.calibrator, "classes_", np.array([0, 1, 2]))
        out = np.zeros_like(raw_probs, dtype=float)
        for col_idx, cls_idx in enumerate(classes):
            cls_int = int(cls_idx)
            if 0 <= cls_int < out.shape[1]:
                out[:, cls_int] = proba[:, col_idx]
        # Re-normalise so each row sums to 1
        row_sums = out.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        return cast(np.ndarray[Any, np.dtype[Any]], out / row_sums)


# ──────────────────────────────────────────────────────────────────────────────
# Standalone calibration helpers
# ──────────────────────────────────────────────────────────────────────────────


def calibrate_model(
    model: Any,
    X_calib: pd.DataFrame | np.ndarray[Any, np.dtype[Any]],
    y_calib: np.ndarray[Any, np.dtype[Any]] | pd.Series,
) -> _CalibratorBundle:
    """
    Fit a sigmoid/Platt calibrator on a held-out (calibration) set.

    Each class gets its own IsotonicRegression mapping raw probabilities ↦
    empirical hit-rate. After transform, rows are re-normalised so that
    P(UP) + P(DOWN) + P(NEUTRAL) = 1.0.

    Parameters
    ──────────
    model    : trained classifier with predict_proba(X) → (n, 3) array.
    X_calib  : calibration features (held out from training).
    y_calib  : calibration labels — accepts raw {-1, 0, 1} or encoded {0, 1, 2}.

    Returns
    ───────
    _CalibratorBundle  ready to apply via .transform(raw_probs).
    """
    raw_proba = model.predict_proba(X_calib)
    classes = getattr(model, "classes_", np.array([0, 1, 2]))
    if list(classes) != [0, 1, 2]:
        reordered = np.zeros((raw_proba.shape[0], 3))
        for col, cls in enumerate(classes):
            reordered[:, int(cls)] = raw_proba[:, col]
        raw_proba = reordered

    y_arr = np.asarray(y_calib)
    # Auto-detect raw {-1,0,1} encoding and convert to {0,1,2}
    if set(np.unique(y_arr).tolist()) <= {-1, 0, 1}:
        y_enc = np.array([_LABEL_TO_IDX[int(v)] for v in y_arr], dtype=int)
    else:
        y_enc = y_arr.astype(int)

    bundle = _CalibratorBundle()
    bundle.fit(raw_proba, y_enc)
    return bundle


def get_calibration_curve(
    y_true: np.ndarray[Any, np.dtype[Any]] | pd.Series,
    y_prob: np.ndarray[Any, np.dtype[Any]] | pd.Series,
    n_bins: int = 10,
) -> dict[str, list[float] | list[int]]:
    """
    Reliability-diagram data for a single class (one-vs-rest).

    For each of `n_bins` equal-width probability bins:
      mean_predicted_value : mean predicted probability in that bin
      fraction_of_positives: empirical positive rate in that bin
      bin_counts           : number of samples in that bin

    Frontend can plot fraction_of_positives vs mean_predicted_value;
    perfect calibration is the y = x diagonal.
    """
    y_t = np.asarray(y_true).astype(float)
    y_p = np.asarray(y_prob).astype(float)
    if y_t.shape != y_p.shape or y_t.ndim != 1:
        raise ValueError("y_true and y_prob must be 1-D arrays of equal length.")
    if n_bins < 2:
        raise ValueError("n_bins must be >= 2.")

    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bin_idx = np.clip(np.digitize(y_p, bins) - 1, 0, n_bins - 1)

    fraction: list[float] = []
    mean_pred: list[float] = []
    counts: list[int] = []
    for b in range(n_bins):
        mask = bin_idx == b
        n_in_bin = int(mask.sum())
        counts.append(n_in_bin)
        if n_in_bin == 0:
            fraction.append(float("nan"))
            mean_pred.append(float("nan"))
        else:
            fraction.append(float(y_t[mask].mean()))
            mean_pred.append(float(y_p[mask].mean()))

    return {
        "fraction_of_positives": fraction,
        "mean_predicted_value": mean_pred,
        "bin_counts": counts,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main class
# ──────────────────────────────────────────────────────────────────────────────


class EnsembleMetaLearner:
    """
    Top-level directional predictor that consumes engine outputs as features.

    Parameters
    ──────────
    model_type : {"lightgbm", "xgboost", "sklearn"}
        Backend gradient-boosting library. Falls back to sklearn if the
        requested library is not installed.
    n_splits   : int
        Walk-forward folds used during fit().
    test_size  : float
        Fraction of data used per test window in walk-forward CV.
    random_state : int
        Reproducibility seed.

    Attributes
    ──────────
    feature_names : list[str]
        Columns expected at predict time (saved from fit).
    is_fitted     : bool
    model_        : trained estimator (best fold or final refit on all data).
    calibrator_   : _CalibratorBundle for isotonic post-calibration.
    cv_scores_    : list[dict] per-fold metrics (log_loss, accuracy).
    """

    def __init__(
        self,
        model_type: str = _DEFAULT_MODEL_TYPE,
        n_splits: int = 5,
        test_size: float = 0.20,
        random_state: int = _RANDOM_STATE,
        n_estimators: int | None = None,
        num_leaves: int | None = None,
        min_child_samples: int | None = None,
    ) -> None:
        self.model_type = self._resolve_backend(model_type)
        self.n_splits = n_splits
        self.test_size = test_size
        self.random_state = random_state
        self.n_estimators_override = n_estimators
        self.num_leaves_override = num_leaves
        self.min_child_samples_override = min_child_samples

        self.feature_names: list[str] = []
        self.is_fitted: bool = False
        self.model_: Any = None
        self.calibrator_: _CalibratorBundle = _CalibratorBundle()
        self.cv_scores_: list[dict[str, float]] = []
        self._classes_: np.ndarray[Any, np.dtype[Any]] = np.array([0, 1, 2])  # internal indices

        # SHAP explainer slots — populated in fit() if shap is available
        self.explainer: Any = None
        self.base_value_: list[float] | None = None  # per-class base value(s)

    # ── Calibration alias ─────────────────────────────────────────────────────

    @property
    def calibrators(self) -> dict[int, LogisticRegression]:
        """Compatibility alias for the fitted sigmoid calibrator by class index."""
        return self.calibrator_.calibrators

    # ── Backend resolution ────────────────────────────────────────────────────

    @staticmethod
    def _resolve_backend(requested: str) -> str:
        req = (requested or "").lower().strip()
        if req == "lightgbm" and _LGB_AVAILABLE:
            return "lightgbm"
        if req == "xgboost" and _XGB_AVAILABLE:
            return "xgboost"
        if req == "lightgbm" and not _LGB_AVAILABLE:
            if _XGB_AVAILABLE:
                logger.warning("lightgbm unavailable — falling back to xgboost.")
                return "xgboost"
            logger.warning(
                "lightgbm/xgboost unavailable — falling back to sklearn HistGradientBoosting."
            )
            return "sklearn"
        if req == "xgboost" and not _XGB_AVAILABLE:
            logger.warning("xgboost unavailable — falling back to sklearn HistGradientBoosting.")
            return "sklearn"
        return "sklearn"

    def _build_estimator(self) -> Any:
        """Instantiate the boosting estimator according to self.model_type."""
        if self.model_type == "lightgbm":
            return lgb.LGBMClassifier(
                objective="multiclass",
                num_class=3,
                n_estimators=self.n_estimators_override or 200,
                learning_rate=0.05,
                num_leaves=self.num_leaves_override or 7,
                min_child_samples=self.min_child_samples_override or 100,
                is_unbalance=True,
                class_weight="balanced",
                random_state=self.random_state,
                verbose=-1,
            )
        if self.model_type == "xgboost":
            return xgb.XGBClassifier(
                objective="multi:softprob",
                num_class=3,
                n_estimators=300,
                learning_rate=0.05,
                max_depth=6,
                random_state=self.random_state,
                eval_metric="mlogloss",
                use_label_encoder=False,
                verbosity=0,
            )
        # sklearn fallback for large tabular backfills. Classic GBM is exact
        # and row-wise; histogram boosting is the scalable sklearn path.
        return HistGradientBoostingClassifier(
            max_iter=self.n_estimators_override or 200,
            learning_rate=0.05,
            max_leaf_nodes=self.num_leaves_override or 31,
            min_samples_leaf=self.min_child_samples_override or 100,
            random_state=self.random_state,
        )

    # ── Label encoding helpers ────────────────────────────────────────────────

    @staticmethod
    def _encode_labels(y: pd.Series) -> np.ndarray[Any, np.dtype[Any]]:
        """Map {-1, 0, 1} → {0, 1, 2}."""
        out = np.full(len(y), -1, dtype=int)
        for raw, idx in _LABEL_TO_IDX.items():
            out[y.values == raw] = idx
        if (out < 0).any():
            raise ValueError(
                "Targets contain values outside {-1, 0, 1}. " "Use create_targets() to build them."
            )
        return out

    # ── Training ──────────────────────────────────────────────────────────────

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        sample_weights: np.ndarray[Any, np.dtype[Any]] | None = None,
    ) -> EnsembleMetaLearner:
        """
        Train via walk-forward CV then refit the best configuration on all data
        and fit the isotonic calibrator on the last out-of-fold predictions.
        """
        if not isinstance(X, pd.DataFrame):
            raise TypeError("X must be a DataFrame.")
        if not isinstance(y, pd.Series):
            raise TypeError("y must be a Series.")
        if len(X) != len(y):
            raise ValueError(f"Length mismatch: X={len(X)}, y={len(y)}.")

        # Drop rows with NaN target before training
        mask = y.notna()
        X = X.loc[mask].copy()
        y = y.loc[mask].copy()
        if sample_weights is not None:
            sample_weights = np.asarray(sample_weights)[mask.values]

        if X.empty:
            raise ValueError("All rows have NaN targets — nothing to train on.")

        self.feature_names = list(X.columns)
        y_enc = self._encode_labels(y)

        # Walk-forward CV
        splits = temporal_split(X, n_splits=self.n_splits, test_size=self.test_size)
        oof_probs: list[np.ndarray[Any, np.dtype[Any]]] = []
        oof_y: list[np.ndarray[Any, np.dtype[Any]]] = []

        for fold_idx, (train_idx, test_idx) in enumerate(splits):
            X_tr, X_te = X.iloc[train_idx], X.iloc[test_idx]
            y_tr, y_te = y_enc[train_idx], y_enc[test_idx]
            w_tr = sample_weights[train_idx] if sample_weights is not None else None

            logger.info(
                "Meta-learner fold %s/%s: train=%s test=%s backend=%s",
                fold_idx + 1,
                len(splits),
                len(train_idx),
                len(test_idx),
                self.model_type,
            )
            estimator = self._build_estimator()
            self._fit_estimator(estimator, X_tr, y_tr, w_tr)

            probs = self._predict_proba_raw(estimator, X_te)
            try:
                ll = log_loss(y_te, probs, labels=[0, 1, 2])
            except ValueError:
                ll = float("nan")
            preds = probs.argmax(axis=1)
            acc = accuracy_score(y_te, preds)
            precision_by_class = precision_score(
                y_te,
                preds,
                labels=[0, 1, 2],
                average=None,
                zero_division=0,
            )
            self.cv_scores_.append(
                {
                    "fold": fold_idx,
                    "log_loss": float(ll),
                    "accuracy": float(acc),
                    "precision_down": float(precision_by_class[0]),
                    "precision_neutral": float(precision_by_class[1]),
                    "precision_up": float(precision_by_class[2]),
                }
            )

            oof_probs.append(probs)
            oof_y.append(y_te)

        # Refit on full data
        logger.info("Meta-learner final refit: rows=%s backend=%s", len(X), self.model_type)
        final_estimator = self._build_estimator()
        self._fit_estimator(final_estimator, X, y_enc, sample_weights)
        self.model_ = final_estimator

        # Calibrate using last OOF fold (held out from final estimator's perspective
        # only by walk-forward, but acceptable as practical post-hoc calibration step)
        if oof_probs and oof_y:
            calib_probs = np.vstack(oof_probs)
            calib_y = np.concatenate(oof_y)
            if len(calib_y) >= 10:
                self.calibrator_.fit(calib_probs, calib_y)

        # SHAP TreeExplainer (best-effort; failure does not abort training)
        if _SHAP_AVAILABLE:
            try:
                self.explainer = shap.TreeExplainer(self.model_)
                exp_val = getattr(self.explainer, "expected_value", None)
                if exp_val is not None:
                    if np.isscalar(exp_val):
                        self.base_value_ = [float(cast(float, exp_val))]
                    else:
                        self.base_value_ = [float(v) for v in np.asarray(exp_val).ravel()]
            except Exception as exc:
                logger.warning("SHAP TreeExplainer init failed: %s", exc)
                self.explainer = None

        self.is_fitted = True
        return self

    def _fit_estimator(
        self,
        estimator: Any,
        X: pd.DataFrame,
        y: np.ndarray[Any, np.dtype[Any]],
        sample_weight: np.ndarray[Any, np.dtype[Any]] | None,
    ) -> None:
        """Backend-aware fit signature (all current backends share kwargs)."""
        if self.model_type == "lightgbm" and len(X) < 500 and hasattr(estimator, "set_params"):
            estimator.set_params(min_child_samples=max(5, len(X) // 20))
        estimator.fit(X, y, sample_weight=sample_weight)

    def _predict_proba_raw(self, estimator: Any, X: pd.DataFrame) -> np.ndarray[Any, np.dtype[Any]]:
        """Return probabilities aligned to columns [0=DOWN, 1=NEUTRAL, 2=UP]."""
        proba = estimator.predict_proba(X)
        # Sklearn / lgb / xgb all order columns by sorted classes_; we encoded 0/1/2.
        classes = getattr(estimator, "classes_", np.array([0, 1, 2]))
        if list(classes) == [0, 1, 2]:
            return cast(np.ndarray[Any, np.dtype[Any]], proba)
        # Reorder columns
        out = np.zeros((proba.shape[0], 3))
        for col, cls in enumerate(classes):
            out[:, int(cls)] = proba[:, col]
        return out

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict_proba(
        self,
        X: pd.DataFrame,
        explain: bool = False,
    ) -> dict[str, Any]:
        """
        Return calibrated class probabilities.

        For a multi-row DataFrame returns dict of arrays; for a single row
        returns dict of floats — both with keys {"UP", "DOWN", "NEUTRAL"}.

        Parameters
        ──────────
        X       : feature matrix.
        explain : if True, attaches a "_explanation" key with SHAP attribution
                  for the *first row* of X. Default False to avoid latency
                  in production (explainer evaluation is non-trivial).
        """
        if not self.is_fitted:
            raise RuntimeError("Model is not fitted. Call fit() first.")
        if not isinstance(X, pd.DataFrame):
            raise TypeError("X must be a DataFrame.")

        # Re-align columns to training schema
        X_aligned = X.reindex(columns=self.feature_names)

        raw = self._predict_proba_raw(self.model_, X_aligned)
        calibrated = self.calibrator_.transform(raw)
        calibrated = _apply_price_directional_prior(X_aligned, calibrated)

        # raw col index 0 = DOWN (label -1), 1 = NEUTRAL (0), 2 = UP (1)
        if calibrated.shape[0] == 1:
            out: dict[str, Any] = {
                "DOWN": float(calibrated[0, 0]),
                "NEUTRAL": float(calibrated[0, 1]),
                "UP": float(calibrated[0, 2]),
            }
        else:
            out = {
                "DOWN": calibrated[:, 0],
                "NEUTRAL": calibrated[:, 1],
                "UP": calibrated[:, 2],
            }

        if explain:
            try:
                first_row = X_aligned.iloc[0]
                out["_explanation"] = self.explain_prediction(first_row)
            except Exception as exc:
                logger.warning("explain_prediction failed: %s", exc)
                out["_explanation"] = None

        return out

    # ── SHAP explainability ──────────────────────────────────────────────────

    def explain_prediction(self, X_row: pd.Series | pd.DataFrame) -> dict[str, Any]:
        """
        SHAP attribution for a single prediction.

        Returns
        ───────
        dict with keys:
            shap_values            : {feature_name: shap_value}
            base_value             : float (model expected value for predicted class)
            top_positive_features  : [(feature, shap_value), ...] up to 3
            top_negative_features  : [(feature, shap_value), ...] up to 3
            motor_attribution      : {motor_name: normalised_contribution}

        If shap is not available or the explainer was not initialised, returns
        a graceful stub with empty fields and an "error_msg" key.
        """
        if not self.is_fitted:
            raise RuntimeError("Model is not fitted.")

        if isinstance(X_row, pd.Series):
            row_df = X_row.to_frame().T
        elif isinstance(X_row, pd.DataFrame):
            row_df = X_row.iloc[[0]] if len(X_row) > 1 else X_row
        else:
            raise TypeError("X_row must be a Series or single-row DataFrame.")

        row_df = row_df.reindex(columns=self.feature_names).fillna(0.0)

        if not _SHAP_AVAILABLE or self.explainer is None:
            return {
                "shap_values": {},
                "base_value": 0.0,
                "top_positive_features": [],
                "top_negative_features": [],
                "motor_attribution": {},
                "error_msg": "shap not available or explainer not initialised",
            }

        # Pick the predicted class to explain
        raw = self._predict_proba_raw(self.model_, row_df)
        calibrated = self.calibrator_.transform(raw)
        pred_cls = int(np.argmax(calibrated[0]))

        try:
            shap_values = self.explainer.shap_values(row_df)
        except Exception as exc:
            logger.warning("shap_values evaluation failed: %s", exc)
            return {
                "shap_values": {},
                "base_value": 0.0,
                "top_positive_features": [],
                "top_negative_features": [],
                "motor_attribution": {},
                "error_msg": f"shap evaluation failed: {exc}",
            }

        # Multiclass output shapes vary across shap versions:
        #   - list of (n, p) arrays, one per class                       (legacy)
        #   - single (n, p, num_class) array                             (newer)
        #   - single (n, p) array                                        (binary)
        if isinstance(shap_values, list):
            cls_vals = np.asarray(shap_values[pred_cls]).ravel()
        else:
            sv = np.asarray(shap_values)
            cls_vals = sv[0, :, pred_cls].ravel() if sv.ndim == 3 else sv[0].ravel()

        # Base value selection
        if self.base_value_ and len(self.base_value_) > pred_cls:
            base_value = float(self.base_value_[pred_cls])
        elif self.base_value_:
            base_value = float(self.base_value_[0])
        else:
            base_value = 0.0

        per_feat = dict(zip(self.feature_names, cls_vals.tolist(), strict=False))

        sorted_pos = sorted(per_feat.items(), key=lambda kv: kv[1], reverse=True)
        sorted_neg = sorted(per_feat.items(), key=lambda kv: kv[1])
        top_pos = [(f, float(v)) for f, v in sorted_pos[:3] if v > 0]
        top_neg = [(f, float(v)) for f, v in sorted_neg[:3] if v < 0]

        # Motor attribution: sum |shap| per originating motor, then normalise
        motor_raw: dict[str, float] = {}
        for feat, val in per_feat.items():
            motor = FEATURE_TO_MOTOR.get(feat, "unknown")
            motor_raw[motor] = motor_raw.get(motor, 0.0) + abs(float(val))
        total = sum(motor_raw.values())
        if total > 0:
            motor_attribution = {m: v / total for m, v in motor_raw.items()}
        else:
            motor_attribution = dict.fromkeys(motor_raw, 0.0)

        return {
            "shap_values": {f: float(v) for f, v in per_feat.items()},
            "base_value": base_value,
            "top_positive_features": top_pos,
            "top_negative_features": top_neg,
            "motor_attribution": motor_attribution,
        }

    # ── Feature importance ────────────────────────────────────────────────────

    def get_feature_importance(self, X_sample: pd.DataFrame | None = None) -> pd.DataFrame:
        """
        Tabulate feature importance from the fitted model.

        Returns
        ───────
        pd.DataFrame with columns:
            feature        — feature name
            importance     — model native importance (gain / split count)
            shap_mean_abs  — mean |SHAP value| across X_sample (NaN if shap absent)

        Sorted by `importance` descending.
        """
        if not self.is_fitted:
            raise RuntimeError("Model is not fitted.")

        # Native importance
        if hasattr(self.model_, "feature_importances_"):
            importance = np.asarray(self.model_.feature_importances_, dtype=float)
        else:
            importance = np.zeros(len(self.feature_names))

        # SHAP (best-effort)
        shap_means = np.full(len(self.feature_names), np.nan)
        if _SHAP_AVAILABLE and X_sample is not None and not X_sample.empty:
            try:
                X_aligned = X_sample.reindex(columns=self.feature_names).fillna(0.0)
                explainer = shap.TreeExplainer(self.model_)
                shap_values = explainer.shap_values(X_aligned)
                # Multiclass: list of arrays, one per class
                if isinstance(shap_values, list):
                    stacked = np.stack([np.abs(arr) for arr in shap_values], axis=0)
                    shap_means = stacked.mean(axis=(0, 1))
                else:
                    shap_means = np.abs(shap_values).mean(axis=0)
                    if shap_means.ndim > 1:
                        shap_means = shap_means.mean(axis=tuple(range(shap_means.ndim - 1)))
            except Exception as exc:
                logger.warning("SHAP computation failed: %s", exc)

        out = (
            pd.DataFrame(
                {
                    "feature": self.feature_names,
                    "importance": importance,
                    "shap_mean_abs": shap_means,
                }
            )
            .sort_values("importance", ascending=False)
            .reset_index(drop=True)
        )
        return out

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Persist the entire learner (including calibrator) via joblib."""
        if not self.is_fitted:
            raise RuntimeError("Refusing to save an unfitted model.")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: str | Path) -> EnsembleMetaLearner:
        """Load a previously saved learner."""
        obj = joblib.load(Path(path))
        if not isinstance(obj, cls):
            raise TypeError(f"Loaded object is not an EnsembleMetaLearner: {type(obj)}")
        return obj


# ──────────────────────────────────────────────────────────────────────────────
