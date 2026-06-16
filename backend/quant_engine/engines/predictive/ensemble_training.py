"""Offline, auditable training pipeline for the EnsembleMetaLearner."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast

import joblib  # type: ignore[import-untyped]
import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from sklearn.ensemble import (
    GradientBoostingClassifier,  # type: ignore[import-not-found, import-untyped]
)
from sklearn.metrics import (  # type: ignore[import-not-found, import-untyped]
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
)
from sklearn.preprocessing import StandardScaler  # type: ignore[import-not-found, import-untyped]

from backend.config.logger_setup import get_logger
from backend.quant_engine.engines.predictive.ensemble_training_models import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    TrainingConfig,
    TrainingResult,
)

logger = get_logger(__name__)

try:
    import lightgbm as _lightgbm

    lgb: Any = _lightgbm
    _LGB_AVAILABLE = True
except Exception as exc:
    logger.warning(
        "lightgbm unavailable for ensemble training; using sklearn fallback. reason=%s",
        exc,
    )
    lgb = None
    _LGB_AVAILABLE = False


_RANDOM_STATE = 42
_LABEL_TO_IDX = {-1: 0, 0: 1, 1: 2}
_CLASS_LABELS = [0, 1, 2]
_CLASS_NAMES = ["DOWN", "NEUTRAL", "UP"]
_EARLY_STOPPING_ROUNDS = 50

_LGB_HPARAMS: dict[str, Any] = {
    "objective": "multiclass",
    "num_class": 3,
    "n_estimators": 200,
    "learning_rate": 0.05,
    "max_depth": 4,
    "num_leaves": 7,
    "min_child_samples": 100,
    "is_unbalance": True,
    "class_weight": "balanced",
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.7,
    "reg_alpha": 0.1,
    "reg_lambda": 0.1,
    "random_state": _RANDOM_STATE,
    "verbose": -1,
    "n_jobs": -1,
}


class _EstimatorLike(Protocol):
    def fit(self: _EstimatorLike, *args: object, **kwargs: object) -> Any: ...

    def predict_proba(
        self: _EstimatorLike,
        features: pd.DataFrame | np.ndarray[Any, np.dtype[Any]],
    ) -> Any: ...


def validate_no_leakage(
    train_idx: np.ndarray[Any, np.dtype[Any]],
    test_idx: np.ndarray[Any, np.dtype[Any]],
    timestamps: pd.Series | pd.DatetimeIndex | np.ndarray[Any, np.dtype[Any]],
) -> None:
    """Raise ValueError if train timestamps overlap or exceed the test window."""
    ts = pd.to_datetime(pd.Series(timestamps).reset_index(drop=True))
    if len(train_idx) == 0 or len(test_idx) == 0:
        raise ValueError("Empty train_idx or test_idx in leakage check.")

    train_max = ts.iloc[train_idx].max()
    test_min = ts.iloc[test_idx].min()
    if train_max >= test_min:
        raise ValueError(f"Temporal leakage: train.max={train_max} >= test.min={test_min}")


def _compute_sample_weights(n: int, decay_rate: float) -> np.ndarray[Any, np.dtype[Any]]:
    """Return exponential time-decay weights with recent rows weighted higher."""
    if n <= 0:
        return np.array([], dtype=float)
    positions = np.arange(n, dtype=float)
    return np.exp(-decay_rate * (n - 1 - positions))


def _simulate_sharpe(
    proba: np.ndarray[Any, np.dtype[Any]],
    forward_returns: np.ndarray[Any, np.dtype[Any]],
    confidence_threshold: float,
) -> float:
    """Simulate a simple directional strategy from class probabilities."""
    if len(forward_returns) == 0 or proba.shape[0] != len(forward_returns):
        return float("nan")

    predicted = proba.argmax(axis=1)
    confidence = proba.max(axis=1)
    position = np.zeros(len(forward_returns))
    position[(predicted == 2) & (confidence > confidence_threshold)] = 1.0
    position[(predicted == 0) & (confidence > confidence_threshold)] = -1.0

    pnl = position * forward_returns
    pnl_std = pnl.std(ddof=1)
    if pnl_std == 0 or np.isnan(pnl_std):
        return 0.0
    return float(pnl.mean() / pnl_std * np.sqrt(252))


def evaluate_fold(
    y_true: np.ndarray[Any, np.dtype[Any]],
    proba: np.ndarray[Any, np.dtype[Any]],
    forward_returns: np.ndarray[Any, np.dtype[Any]] | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> dict[str, object]:
    """Compute classification, calibration, and simulated Sharpe metrics."""
    predicted = proba.argmax(axis=1)
    metrics: dict[str, object] = {
        "accuracy": float(accuracy_score(y_true, predicted)),
        "confusion_matrix": confusion_matrix(
            y_true,
            predicted,
            labels=_CLASS_LABELS,
        )
        .astype(int)
        .tolist(),
    }

    try:
        metrics["log_loss"] = float(log_loss(y_true, proba, labels=_CLASS_LABELS))
    except ValueError:
        metrics["log_loss"] = float("nan")

    precision = precision_score(
        y_true,
        predicted,
        labels=_CLASS_LABELS,
        average=None,
        zero_division=0,
    )
    recall = recall_score(
        y_true,
        predicted,
        labels=_CLASS_LABELS,
        average=None,
        zero_division=0,
    )
    f1 = f1_score(
        y_true,
        predicted,
        labels=_CLASS_LABELS,
        average=None,
        zero_division=0,
    )
    for class_idx, class_name in enumerate(_CLASS_NAMES):
        metrics[f"precision_{class_name}"] = float(precision[class_idx])
        metrics[f"recall_{class_name}"] = float(recall[class_idx])
        metrics[f"f1_{class_name}"] = float(f1[class_idx])

    for class_idx, class_name in enumerate(_CLASS_NAMES):
        class_target = (y_true == class_idx).astype(int)
        try:
            metrics[f"brier_{class_name}"] = float(
                brier_score_loss(class_target, proba[:, class_idx])
            )
        except ValueError:
            metrics[f"brier_{class_name}"] = float("nan")

    if forward_returns is None:
        metrics["sharpe"] = float("nan")
    else:
        metrics["sharpe"] = _simulate_sharpe(
            proba,
            forward_returns,
            confidence_threshold,
        )

    return metrics


def evaluate_model_gate(
    *,
    mean_accuracy: float,
    mean_log_loss: float,
    mean_brier: float,
    mean_sharpe: float,
    naive_accuracy: float,
    rule_based_accuracy: float,
    n_samples_test: int,
    min_test_samples: int = 30,
) -> dict[str, object]:
    """Institutional promotion gate for a trained meta-learner.

    A model is promotable only when walk-forward OOS accuracy beats both
    baselines and enough test samples exist. Sharpe/log-loss/Brier are reported
    for governance; they do not override insufficient samples or weak accuracy.
    """
    baseline = max(float(naive_accuracy), float(rule_based_accuracy))
    reasons: list[str] = []
    if n_samples_test < min_test_samples:
        reasons.append("minimum_samples")
    if not np.isfinite(mean_accuracy) or mean_accuracy <= baseline:
        reasons.append("below_baseline")
    if not np.isfinite(mean_log_loss):
        reasons.append("invalid_log_loss")
    if not np.isfinite(mean_brier):
        reasons.append("invalid_brier")
    status = "approved" if not reasons else "blocked"
    return {
        "status": status,
        "primary_horizon_days": 5,
        "baseline_to_beat": baseline,
        "naive_accuracy": float(naive_accuracy),
        "rule_based_accuracy": float(rule_based_accuracy),
        "mean_accuracy": float(mean_accuracy),
        "mean_log_loss": float(mean_log_loss),
        "mean_brier": float(mean_brier),
        "mean_sharpe": float(mean_sharpe),
        "n_samples_test": int(n_samples_test),
        "reasons": reasons,
    }


def _drop_high_nan_rows(
    features: pd.DataFrame,
    threshold: float,
) -> tuple[pd.DataFrame, np.ndarray[Any, np.dtype[Any]]]:
    """Drop rows whose NaN fraction exceeds threshold."""
    if features.empty:
        return features, np.array([], dtype=bool)
    nan_fraction = features.isna().mean(axis=1)
    keep = (nan_fraction <= threshold).to_numpy()
    return features.loc[keep].copy(), keep


def _empty_result(symbol: str, training_date: str, error_msg: str) -> TrainingResult:
    return TrainingResult(
        symbol=symbol,
        metrics_by_fold=[],
        mean_accuracy=float("nan"),
        mean_log_loss=float("nan"),
        mean_sharpe=float("nan"),
        best_fold=-1,
        feature_importance=pd.DataFrame(),
        n_samples_train=0,
        n_samples_test=0,
        training_date=training_date,
        error_msg=error_msg,
    )


def _encode_direction_labels(labels: pd.Series) -> np.ndarray[Any, np.dtype[Any]]:
    return np.array([_LABEL_TO_IDX[int(value)] for value in labels], dtype=int)


def _build_estimator(use_lightgbm: bool) -> _EstimatorLike:
    if use_lightgbm and lgb is not None:
        return cast(_EstimatorLike, lgb.LGBMClassifier(**_LGB_HPARAMS))
    return cast(
        _EstimatorLike,
        GradientBoostingClassifier(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=3,
            random_state=_RANDOM_STATE,
        ),
    )


_INTERNAL_VAL_FRACTION = 0.15
_MIN_INTERNAL_VAL_ROWS = 30


def _fit_estimator(
    estimator: _EstimatorLike,
    features: pd.DataFrame | np.ndarray[Any, np.dtype[Any]],
    labels: np.ndarray[Any, np.dtype[Any]],
    sample_weight: np.ndarray[Any, np.dtype[Any]],
    validation: (
        tuple[pd.DataFrame | np.ndarray[Any, np.dtype[Any]], np.ndarray[Any, np.dtype[Any]]] | None
    ) = None,
) -> None:
    """
    Fit estimator. Early stopping uses an INTERNAL split of the train fold —
    never the test fold. The `validation` arg is accepted for API compat but
    intentionally ignored to prevent target leakage on walk-forward CV.
    """
    del validation  # ignored on purpose: early stopping must not see test fold

    use_early_stop = (
        lgb is not None
        and isinstance(estimator, lgb.LGBMClassifier)
        and len(features) >= _MIN_INTERNAL_VAL_ROWS * 2
    )
    if lgb is not None and isinstance(estimator, lgb.LGBMClassifier) and len(features) < 500:
        estimator.set_params(min_child_samples=max(5, len(features) // 20))

    if use_early_stop:
        split_idx = int(len(features) * (1.0 - _INTERNAL_VAL_FRACTION))
        if isinstance(features, pd.DataFrame):
            x_tr, x_val = features.iloc[:split_idx], features.iloc[split_idx:]
        else:
            x_tr, x_val = features[:split_idx], features[split_idx:]
        y_tr, y_val = labels[:split_idx], labels[split_idx:]
        w_tr = sample_weight[:split_idx] if sample_weight is not None else None

        if len(x_val) >= _MIN_INTERNAL_VAL_ROWS and len(np.unique(y_tr)) >= 2:
            try:
                estimator.fit(
                    x_tr,
                    y_tr,
                    sample_weight=w_tr,
                    eval_set=[(x_val, y_val)],
                    callbacks=[lgb.early_stopping(_EARLY_STOPPING_ROUNDS, verbose=False)],
                )
                return
            except TypeError:
                logger.debug("LightGBM callbacks unavailable; fitting without early stop.")

    estimator.fit(features, labels, sample_weight=sample_weight)


def _predict_proba(
    estimator: _EstimatorLike,
    features: pd.DataFrame | np.ndarray[Any, np.dtype[Any]],
) -> np.ndarray[Any, np.dtype[Any]]:
    raw_proba = np.asarray(estimator.predict_proba(features), dtype=float)
    classes = getattr(estimator, "classes_", np.array(_CLASS_LABELS))
    if list(classes) == _CLASS_LABELS:
        return raw_proba

    reordered = np.zeros((raw_proba.shape[0], len(_CLASS_LABELS)))
    for col_idx, class_idx in enumerate(classes):
        reordered[:, int(class_idx)] = raw_proba[:, col_idx]
    return reordered


def _mean_metric(metrics_by_fold: list[dict[str, Any]], key: str) -> float:
    values: list[float] = []
    for metrics in metrics_by_fold:
        raw = metrics.get(key)
        if not isinstance(raw, int | float | np.integer | np.floating):
            continue
        value = float(raw)
        if not np.isnan(value):
            values.append(value)
    return float(np.mean(values)) if values else float("nan")


def _mean_multiclass_brier(metrics_by_fold: list[dict[str, Any]]) -> float:
    values: list[float] = []
    for cls in _CLASS_NAMES:
        val = _mean_metric(metrics_by_fold, f"brier_{cls}")
        if np.isfinite(val):
            values.append(val)
    return float(np.mean(values)) if values else float("nan")


def _naive_majority_accuracy(labels: np.ndarray[Any, np.dtype[Any]]) -> float:
    if len(labels) == 0:
        return float("nan")
    counts = np.bincount(labels.astype(int), minlength=len(_CLASS_LABELS))
    return float(counts.max() / len(labels))


def _rule_based_accuracy(features: pd.DataFrame, target: pd.Series) -> float:
    """Simple no-ML baseline: directional engine signal, else 5D momentum."""
    if len(features) == 0 or len(target) == 0:
        return float("nan")
    signal_col = "signal" if "signal" in features.columns else None
    if signal_col is None and "return_5d" in features.columns:
        signal_col = "return_5d"
    if signal_col is None and "price__return_5d" in features.columns:
        signal_col = "price__return_5d"
    if signal_col is None:
        pred = np.zeros(len(target), dtype=int)
    else:
        signal = pd.to_numeric(features[signal_col], errors="coerce").fillna(0.0).to_numpy()
        pred = np.zeros(len(signal), dtype=int)
        pred[signal > 0.005] = 1
        pred[signal < -0.005] = -1
    truth = target.astype(int).to_numpy()
    return float((pred == truth).mean()) if len(truth) else float("nan")


def _sum_confusion_matrices(metrics_by_fold: list[dict[str, Any]]) -> list[list[int]]:
    total = np.zeros((3, 3), dtype=int)
    for metrics in metrics_by_fold:
        raw = metrics.get("confusion_matrix")
        try:
            arr = np.asarray(raw, dtype=int)
        except (TypeError, ValueError):
            continue
        if arr.shape == (3, 3):
            total += arr
    return total.tolist()


def _feature_importance(estimator: object, columns: pd.Index) -> pd.DataFrame:
    importance = np.asarray(getattr(estimator, "feature_importances_", []), dtype=float)
    if len(importance) != len(columns):
        importance = np.zeros(len(columns))
    return (
        pd.DataFrame(
            {
                "feature": list(columns),
                "importance": importance,
                "shap_mean_abs": np.full(len(columns), np.nan),
            }
        )
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )


def train_meta_learner(
    symbol: str,
    engine_outputs_history: list[dict[str, Any]],
    price_history: pd.Series,
    config: TrainingConfig | None = None,
) -> TrainingResult:
    """Train EnsembleMetaLearner offline with walk-forward validation."""
    from backend.quant_engine.engines.predictive.ensemble_meta_learner import (
        EnsembleMetaLearner,
        build_feature_matrix,
        create_targets,
        temporal_split,
    )

    cfg = config or TrainingConfig()
    training_date = pd.Timestamp.now(tz="UTC").isoformat()

    features = build_feature_matrix(engine_outputs_history)
    if features.empty:
        return _empty_result(symbol, training_date, "Empty feature matrix.")

    targets = create_targets(price_history, forward_periods=cfg.forward_periods)
    primary_target_col = f"target_direction_{cfg.primary_period}"
    primary_return_col = f"target_return_{cfg.primary_period}"
    if primary_target_col not in targets.columns:
        return _empty_result(symbol, training_date, f"Missing {primary_target_col}.")

    if hasattr(features.index, "tz") and features.index.tz is not None:
        features = features.copy()
        features.index = features.index.tz_localize(None)
    if hasattr(targets.index, "tz") and targets.index.tz is not None:
        targets = targets.copy()
        targets.index = targets.index.tz_localize(None)

    common_index = features.index.intersection(targets.index)
    if len(common_index) == 0:
        return _empty_result(
            symbol,
            training_date,
            "No overlap between feature matrix index and price history "
            f"(features: {features.index.min()} -> {features.index.max()}; "
            f"targets: {targets.index.min()} -> {targets.index.max()}).",
        )
    logger.info("Alineacion feature/target OK: %s filas", len(common_index))

    features = features.loc[common_index]
    target = targets.loc[common_index, primary_target_col]
    forward_return = targets.loc[common_index, primary_return_col]

    features, _ = _drop_high_nan_rows(features, cfg.nan_row_threshold)
    target = target.loc[features.index]
    forward_return = forward_return.loc[features.index]

    valid_target = target.notna()
    features = features.loc[valid_target]
    target = target.loc[valid_target]
    forward_return = forward_return.loc[valid_target]

    if len(features) < cfg.n_splits + 10:
        return _empty_result(
            symbol,
            training_date,
            f"Too few usable rows after cleaning: {len(features)}.",
        )

    features = features.fillna(features.median(numeric_only=True)).fillna(0.0)
    splits = temporal_split(features, n_splits=cfg.n_splits, test_size=cfg.test_size)
    timestamps = pd.Series(features.index, index=range(len(features)))

    metrics_by_fold: list[dict[str, Any]] = []
    oof_proba: list[np.ndarray[Any, np.dtype[Any]]] = []
    oof_y: list[np.ndarray[Any, np.dtype[Any]]] = []
    n_train_total = 0
    n_test_total = 0
    use_lightgbm = _LGB_AVAILABLE and lgb is not None

    for fold_idx, (train_idx, test_idx) in enumerate(splits):
        validate_no_leakage(train_idx, test_idx, timestamps)

        train_features = features.iloc[train_idx]
        test_features = features.iloc[test_idx]
        train_labels = _encode_direction_labels(target.iloc[train_idx].astype(int))
        test_labels = _encode_direction_labels(target.iloc[test_idx].astype(int))
        test_returns = forward_return.iloc[test_idx].to_numpy()

        scaler = StandardScaler()
        train_scaled = scaler.fit_transform(train_features)
        test_scaled = scaler.transform(test_features)
        sample_weight = _compute_sample_weights(len(train_features), cfg.decay_rate)

        estimator = _build_estimator(use_lightgbm)
        _fit_estimator(
            estimator,
            train_scaled,
            train_labels,
            sample_weight,
            validation=(test_scaled, test_labels),
        )
        proba = _predict_proba(estimator, test_scaled)

        fold_metrics = evaluate_fold(
            test_labels,
            proba,
            forward_returns=test_returns,
            confidence_threshold=cfg.confidence_threshold,
        )
        fold_metrics["fold"] = fold_idx
        fold_metrics["n_train"] = len(train_features)
        fold_metrics["n_test"] = len(test_features)
        metrics_by_fold.append(fold_metrics)

        oof_proba.append(proba)
        oof_y.append(test_labels)
        n_train_total += len(train_features)
        n_test_total += len(test_features)

    full_scaler = StandardScaler()
    full_scaled = full_scaler.fit_transform(features)
    full_labels = _encode_direction_labels(target.astype(int))
    full_weight = _compute_sample_weights(len(features), cfg.decay_rate)

    learner = EnsembleMetaLearner(
        model_type="lightgbm" if use_lightgbm else "sklearn",
        n_splits=cfg.n_splits,
        test_size=cfg.test_size,
    )
    learner.feature_names = list(features.columns)

    final_estimator = _build_estimator(use_lightgbm)
    scaled_frame = pd.DataFrame(full_scaled, columns=features.columns, index=features.index)
    _fit_estimator(final_estimator, scaled_frame, full_labels, full_weight)
    learner.model_ = final_estimator

    if oof_proba and oof_y:
        calib_proba = np.vstack(oof_proba)
        calib_y = np.concatenate(oof_y)
        if len(calib_y) >= 10:
            learner.calibrator_.fit(calib_proba, calib_y)
    learner.is_fitted = True

    if cfg.save_path:
        save_dir = Path(cfg.save_path)
        save_dir.mkdir(parents=True, exist_ok=True)
        learner.save(save_dir / f"{symbol}_learner.joblib")
        joblib.dump(full_scaler, save_dir / f"{symbol}_scaler.joblib")

    mean_accuracy = _mean_metric(metrics_by_fold, "accuracy")
    mean_log_loss = _mean_metric(metrics_by_fold, "log_loss")
    mean_brier = _mean_multiclass_brier(metrics_by_fold)
    mean_sharpe = _mean_metric(metrics_by_fold, "sharpe")
    accuracies = [float(metrics["accuracy"]) for metrics in metrics_by_fold]
    naive_accuracy = _naive_majority_accuracy(full_labels)
    rule_based_accuracy = _rule_based_accuracy(features, target)
    gate = evaluate_model_gate(
        mean_accuracy=mean_accuracy,
        mean_log_loss=mean_log_loss,
        mean_brier=mean_brier,
        mean_sharpe=mean_sharpe,
        naive_accuracy=naive_accuracy,
        rule_based_accuracy=rule_based_accuracy,
        n_samples_test=n_test_total,
    )

    return TrainingResult(
        symbol=symbol,
        metrics_by_fold=metrics_by_fold,
        mean_accuracy=mean_accuracy,
        mean_log_loss=mean_log_loss,
        mean_sharpe=mean_sharpe,
        best_fold=int(np.argmax(accuracies)) if accuracies else -1,
        feature_importance=_feature_importance(final_estimator, features.columns),
        n_samples_train=n_train_total,
        n_samples_test=n_test_total,
        training_date=training_date,
        mean_brier=mean_brier,
        naive_accuracy=naive_accuracy,
        rule_based_accuracy=rule_based_accuracy,
        model_gate=gate,
        confusion_matrix=_sum_confusion_matrices(metrics_by_fold),
        learner=learner,
        scaler=full_scaler,
        error_msg=None,
    )
