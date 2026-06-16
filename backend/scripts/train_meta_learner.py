from __future__ import annotations
from typing import Any
"""Train and persist the production EnsembleMetaLearner."""


import argparse
import json
import sqlite3
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.quant_engine.engines.predictive.ensemble_meta_learner import (
    EnsembleMetaLearner,
    build_feature_matrix,
)
from backend.services.prediction_logger import PredictionLogger

META_LEARNER_PATH = Path("backend/models/meta_learner.joblib")
META_LEARNER_LONG_PATH = Path("backend/models/meta_learner_long.joblib")
META_LEARNER_SHORT_PATH = Path("backend/models/meta_learner_short.joblib")
INSTITUTIONAL_DB_PATH = Path("backend/data/predictions.db")
MIN_HISTORY_SAMPLES = 50
MIN_REAL_SAMPLES = 300
MIN_SIDE_EDGE_SAMPLES = 20
MAX_CLASS_DOMINANCE = 0.70
ACCURACY_TOLERANCE = 0.05
RETURN_THRESHOLD = 0.0015
RANDOM_STATE = 42
TARGET_HORIZONS = ("1h", "4h", "eod")
TRAINING_SIDES = ("generic", "long", "short")
CLI_SIDES = ("long", "short", "both")
logger = get_logger(__name__)


def _nanmean_or_zero(values: list[float]) -> float:
    clean = [value for value in values if not np.isnan(value)]
    if not clean:
        return 0.0
    return float(np.nanmean(clean))


def _target_from_return(
    value: float,
    return_threshold: float = RETURN_THRESHOLD,
    side: str = "generic",
) -> int:
    if side == "long":
        return 1 if value > return_threshold else 0
    if side == "short":
        return -1 if value < -return_threshold else 0
    if value > return_threshold:
        return 1
    if value < -return_threshold:
        return -1
    return 0


def _signal_series(history: pd.DataFrame, key: str) -> pd.Series:
    values: list[float] = []
    for signals in history.get("motor_signals", []):
        value = signals.get(key) if isinstance(signals, dict) else None
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            values.append(float("nan"))
    return pd.Series(values, index=history.index, dtype="float64")


def apply_side_training_filter(history: pd.DataFrame, side: str) -> pd.DataFrame:
    """Keep only high-conviction rows for directional Funding Lab meta-learners."""
    if not isinstance(history, pd.DataFrame):
        return history
    if side == "generic" or history.empty:
        return history.copy()
    feature_versions = history.get("snapshot_feature_set_version")
    if (
        feature_versions is not None
        and feature_versions.astype(str).str.contains("funding_lab_intraday").any()
    ):
        return history.copy()
    if side not in {"long", "short"}:
        raise ValueError(f"side invalido: {side}")

    trend = _signal_series(history, "vsa_forecast__trend_score")
    volume = _signal_series(history, "vsa_forecast__volume_score")
    mean_rev = _signal_series(history, "price__mean_rev_signal")
    return_5d = _signal_series(history, "price__return_5d")
    if return_5d.isna().all():
        return_5d = _signal_series(history, "price__return_1h")
    structure = _signal_series(history, "technical__market_structure_trend")
    vwap_distance = _signal_series(history, "technical__vwap_distance")
    rsi_14 = _signal_series(history, "price__rsi_14")
    vol_ratio = _signal_series(history, "price__vol_ratio_5_20")

    if side == "long":
        mask = (
            (trend >= 0.75)
            & (volume >= volume.median())
            & mean_rev.between(mean_rev.quantile(0.10), mean_rev.quantile(0.90))
            & (return_5d <= return_5d.quantile(0.80))
        )
    else:
        mask = (
            (trend <= -0.70)
            & (structure == -1.0)
            & (vwap_distance < 0.0)
            & ((rsi_14 >= 55.0) | (mean_rev < 0.0))
            & vol_ratio.between(0.60, 1.30)
        )
    return history.loc[mask.fillna(False)].copy()


def _feature_value_map(motor_signals: object) -> dict[str, float]:
    if not isinstance(motor_signals, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in motor_signals.items():
        try:
            out[f"{key}__signal"] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _price_derived_features(price_series: pd.Series) -> pd.DataFrame:
    """
    Build price-derived signal features from real close history.

    These features carry genuine (low) correlation with forward returns —
    momentum, mean-reversion, volatility-regime, RSI. Motor-level features
    (gamma, RND, dealer-flow) stay NaN: LightGBM treats NaN as a learnable
    "missing" branch, which is strictly better than injecting Gaussian noise.
    """
    df = pd.DataFrame(index=price_series.index)

    df["return_1d"] = price_series.pct_change(1)
    df["return_5d"] = price_series.pct_change(5)
    df["return_10d"] = price_series.pct_change(10)
    df["return_20d"] = price_series.pct_change(20)

    df["realized_vol_5d"] = df["return_1d"].rolling(5).std()
    df["realized_vol_20d"] = df["return_1d"].rolling(20).std()

    df["price_vs_ma20"] = price_series / price_series.rolling(20).mean() - 1
    df["price_vs_ma50"] = price_series / price_series.rolling(50).mean() - 1
    df["price_vs_ma200"] = price_series / price_series.rolling(200).mean() - 1

    delta = price_series.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    df["rsi_14"] = 100 - (100 / (1 + rs))
    df["rsi_14_normalized"] = (df["rsi_14"] - 50) / 50

    df["vol_ratio_5_20"] = df["realized_vol_5d"] / (df["realized_vol_20d"] + 1e-10)
    df["mean_rev_signal"] = -df["return_5d"].clip(-0.10, 0.10) / 0.10

    return df.dropna(subset=["return_1d"])


def _engine_entry_from_price(
    ts: pd.Timestamp,
    price_row: pd.Series,
) -> dict[str, Any]:
    """
    Pack price-derived features into the engine-namespaced shape that
    `build_feature_matrix` expects. Motor blocks emitted as None so missing
    fields surface as NaN rather than spurious zeros.
    """
    return {
        "timestamp": ts,
        "dte_category": "monthly",
        "return_1d": float(price_row.get("return_1d", np.nan)),
        "return_5d": float(price_row.get("return_5d", np.nan)),
        "return_10d": float(price_row.get("return_10d", np.nan)),
        "return_20d": float(price_row.get("return_20d", np.nan)),
        "realized_vol_5d": float(price_row.get("realized_vol_5d", np.nan)),
        "realized_vol_20d": float(price_row.get("realized_vol_20d", np.nan)),
        "price_vs_ma20": float(price_row.get("price_vs_ma20", np.nan)),
        "price_vs_ma50": float(price_row.get("price_vs_ma50", np.nan)),
        "price_vs_ma200": float(price_row.get("price_vs_ma200", np.nan)),
        "rsi_14_normalized": float(price_row.get("rsi_14_normalized", np.nan)),
        "rsi_14": float(price_row.get("rsi_14", np.nan)),
        "vol_ratio_5_20": float(price_row.get("vol_ratio_5_20", np.nan)),
        "mean_rev_signal": float(price_row.get("mean_rev_signal", np.nan)),
        "tail_risk": None,
        "gamma_flip": None,
        "vsa_forecast": None,
        "sentiment": None,
        "fear_greed": None,
        "cross_asset": None,
        "squeeze": None,
        "shadow_delta": None,
        "zomma": None,
        "speed_instability": None,
        "volatility_skew": None,
        "rnd": None,
        "dealer_flow": None,
        "hmm": None,
        "macro_regime": None,
        "orchestrator": None,
    }


def dataset_from_prediction_history(
    history: pd.DataFrame,
    return_threshold: float = RETURN_THRESHOLD,
    side: str = "generic",
) -> tuple[pd.DataFrame, pd.Series]:
    rows: list[dict[str, Any]] = []
    labels: list[int] = []
    indexes: list[pd.Timestamp] = []

    if history.empty:
        return pd.DataFrame(), pd.Series(dtype=int)

    for _, record in history.iterrows():
        ret_5d = record.get("outcome_return_5d")
        if pd.isna(ret_5d):
            continue
        ts = pd.Timestamp(record.get("timestamp"))
        signals = _feature_value_map(record.get("motor_signals"))
        rows.append(
            {
                "timestamp": ts,
                "dte_category": record.get("dte_category"),
                "gamma_flip": {"flip_signal": signals.get("gamma_flip__signal", np.nan)},
                "rnd": {"q_skewness": signals.get("risk_neutral_density__signal", np.nan)},
                "dealer_flow": {"ndde_normalized": signals.get("dealer_flow__signal", np.nan)},
                "macro_regime": {
                    "macro_bull_prior": signals.get("macro_regime_prior__signal", np.nan),
                },
                "orchestrator": {
                    "conflict_score": float(record.get("conflict_score") or 0.0),
                    "confidence": record.get("confidence", np.nan),
                    "signal": record.get("signal", np.nan),
                },
            }
        )
        labels.append(
            _target_from_return(
                float(ret_5d),
                return_threshold=return_threshold,
                side=side,
            )
        )
        indexes.append(ts)

    features = build_feature_matrix(rows)
    features.index = pd.DatetimeIndex(indexes)
    target = pd.Series(labels, index=features.index, dtype=int)
    return features, target


def dataset_from_real_outcomes(
    history: pd.DataFrame,
    return_threshold: float = RETURN_THRESHOLD,
    side: str = "generic",
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Real-mode dataset: expand each prediction's `motor_signals` dict into
    feature columns and derive a trinary target from `outcome_return_5d`.
    """
    if history.empty:
        return pd.DataFrame(), pd.Series(dtype=int)

    feature_rows: list[dict[str, float]] = []
    labels: list[int] = []
    indexes: list[pd.Timestamp] = []

    for _, record in history.iterrows():
        ret_5d = record.get("outcome_return_5d")
        if pd.isna(ret_5d):
            continue
        signals = record.get("motor_signals")
        if not isinstance(signals, dict) or not signals:
            continue

        feats: dict[str, float] = {}
        for key, value in signals.items():
            try:
                feats[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        # Orchestrator-level features that aren't in motor_signals
        try:
            feats["orchestrator__conflict_score"] = float(record.get("conflict_score") or 0.0)
        except (TypeError, ValueError):
            feats["orchestrator__conflict_score"] = 0.0
        try:
            feats["orchestrator__confidence"] = float(record.get("confidence") or 0.0)
        except (TypeError, ValueError):
            feats["orchestrator__confidence"] = 0.0

        if not feats:
            continue

        feature_rows.append(feats)
        labels.append(
            _target_from_return(
                float(ret_5d),
                return_threshold=return_threshold,
                side=side,
            )
        )
        indexes.append(pd.Timestamp(record.get("timestamp")))

    if not feature_rows:
        return pd.DataFrame(), pd.Series(dtype=int)

    features = pd.DataFrame(feature_rows, index=pd.to_datetime(indexes, utc=True))
    target = pd.Series(labels, index=features.index, dtype=int)
    return features, target


def _validate_real_dataset(
    features: pd.DataFrame,
    target: pd.Series,
    min_real_samples: int = MIN_REAL_SAMPLES,
    side: str = "generic",
) -> None:
    """Hard-fail before training if the real dataset is too small or skewed."""
    n = len(features)
    if n < min_real_samples:
        raise ValueError(
            f"Modo real requiere >= {min_real_samples} muestras con outcome; "
            f"hay {n}. Ejecutar audit_prediction_quality.py para confirmar."
        )

    if side in {"long", "short"}:
        edge_label = 1 if side == "long" else -1
        observed = set(target.unique().tolist())
        expected = {0, edge_label}
        if not expected.issubset(observed):
            raise ValueError(
                f"Modo real side={side} requiere clases {sorted(expected)}; "
                f"presentes: {sorted(observed)}"
            )
        edge_count = int((target == edge_label).sum())
        if edge_count < MIN_SIDE_EDGE_SAMPLES:
            raise ValueError(
                f"Modo real side={side} requiere >= {MIN_SIDE_EDGE_SAMPLES} "
                f"muestras edge; hay {edge_count}."
            )
        return

    n_classes = int(target.nunique())
    if n_classes < 3:
        raise ValueError(
            f"Modo real requiere las 3 clases representadas; "
            f"presentes: {sorted(target.unique().tolist())}"
        )

    counts = target.value_counts(normalize=True)
    top = float(counts.max())
    if top > MAX_CLASS_DOMINANCE:
        raise ValueError(
            f"Clase dominante = {top:.1%} (limite {MAX_CLASS_DOMINANCE:.0%}). "
            f"Distribucion:\n{counts.round(3).to_string()}"
        )


def _load_existing_mean_accuracy(output_path: Path) -> float:
    if not output_path.exists():
        return 0.0
    try:
        prev = joblib.load(output_path)
    except Exception as exc:
        logger.warning("No se pudo cargar modelo previo (%s); se asume baseline 0.", exc)
        return 0.0
    explicit = getattr(prev, "mean_accuracy", None)
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    cv = getattr(prev, "cv_scores_", None) or []
    accs = [s.get("accuracy") for s in cv if isinstance(s, dict) and s.get("accuracy") is not None]
    if not accs:
        return 0.0
    return float(np.nanmean(accs))


def download_close_prices(symbol: str, days: int) -> pd.Series:
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance is required for synthetic bootstrap training") from exc

    period_days = max(days + 15, 30)
    data = yf.download(
        symbol,
        period=f"{period_days}d",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    if data is None or data.empty or "Close" not in data:
        raise RuntimeError(f"No yfinance close history returned for {symbol}")

    close = data["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return pd.Series(close, dtype=float).dropna().tail(days + 5)


def _align_indexes(
    features: pd.DataFrame,
    targets: pd.Series | pd.DataFrame,
    min_overlap: int = 50,
) -> tuple[pd.DataFrame, Any]:
    """Strip tz, intersect indices, fail loudly if overlap is too small."""
    if hasattr(features.index, "tz") and features.index.tz is not None:
        features = features.copy()
        features.index = features.index.tz_localize(None)
    if hasattr(targets.index, "tz") and targets.index.tz is not None:
        targets = targets.copy()
        targets.index = targets.index.tz_localize(None)

    common = features.index.intersection(targets.index)
    if len(common) < min_overlap:
        raise ValueError(
            f"Solo {len(common)} filas en comun entre features y targets. "
            f"Features: {features.index.min()} -> {features.index.max()}. "
            f"Targets: {targets.index.min()} -> {targets.index.max()}."
        )
    logger.info("Alineacion OK: %s filas en comun", len(common))
    return features.loc[common], targets.loc[common]


def build_synthetic_dataset(
    symbol: str,
    close_prices: pd.Series,
    return_threshold: float = RETURN_THRESHOLD,
    side: str = "generic",
) -> tuple[pd.DataFrame, pd.Series]:
    """Build dataset with price-derived signal features and forward-return targets."""
    del symbol  # unused — kept for API compatibility
    prices = pd.Series(close_prices, dtype=float).dropna()
    if len(prices) < 30:
        raise ValueError(f"Need at least 30 close prices for training, got {len(prices)}.")

    price_feats = _price_derived_features(prices)
    forward_return = (prices.shift(-5) / prices - 1.0).rename("forward_return_5")
    target_full = forward_return.apply(
        lambda value: _target_from_return(
            value,
            return_threshold=return_threshold,
            side=side,
        )
    )
    target_full[forward_return.isna()] = np.nan

    aligned_feats, aligned_target = _align_indexes(price_feats, target_full, min_overlap=30)
    valid_mask = aligned_target.notna()
    aligned_feats = aligned_feats.loc[valid_mask]
    aligned_target = aligned_target.loc[valid_mask].astype(int)

    rows = [
        _engine_entry_from_price(pd.Timestamp(ts), aligned_feats.loc[ts])
        for ts in aligned_feats.index
    ]
    features = build_feature_matrix(rows)
    target = pd.Series(aligned_target.values, index=features.index, dtype=int)
    return features, target


def _assert_class_distribution(target: pd.Series) -> None:
    """Fail loudly if dataset cannot support multi-class training."""
    class_counts = target.value_counts().sort_index()
    pct = class_counts / len(target) * 100
    logger.info("Distribucion de clases:\n%s", class_counts.to_string())
    logger.info("Porcentaje por clase:\n%s", pct.round(2).to_string())

    n_classes = int(target.nunique())
    if n_classes < 2:
        raise ValueError(
            f"Dataset tiene solo {n_classes} clase(s). "
            f"Necesitas mas datos o reducir threshold de target. "
            f"Clases presentes: {sorted(target.unique().tolist())}"
        )


def _class_balanced_sample_weights(target: pd.Series) -> np.ndarray[Any, Any]:
    counts = target.value_counts()
    if counts.empty:
        return np.array([], dtype=float)
    total = float(len(target))
    n_classes = float(len(counts))
    return np.array(
        [total / (n_classes * float(counts.loc[label])) for label in target],
        dtype=float,
    )


def _load_institutional_history(
    symbol: str,
    db_path: Path,
    feature_set: str | None = None,
    target_horizon: str = "eod",
) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    if target_horizon not in TARGET_HORIZONS:
        raise ValueError(
            f"target_horizon invalido: {target_horizon}. "
            f"Usar uno de: {', '.join(TARGET_HORIZONS)}"
        )
    target_column = f"outcome_return_{target_horizon}"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        has_snapshots = bool("feature_snapshots" in tables)
        snapshot_join = ""
        snapshot_select = (
            "NULL AS snapshot_features_json, "
            "NULL AS snapshot_source_quality, "
            "NULL AS snapshot_feature_set_version"
        )
        feature_filter = ""
        symbol_filter = ""
        params: list[Any] = []
        if symbol.upper() not in {"ALL", "*"}:
            symbol_filter = "AND p.symbol = ?"
            params.append(symbol.upper())
        if has_snapshots:
            snapshot_join = "LEFT JOIN feature_snapshots fs ON fs.prediction_id = p.prediction_id"
            snapshot_select = (
                "fs.features_json AS snapshot_features_json, "
                "fs.source_quality AS snapshot_source_quality, "
                "fs.feature_set_version AS snapshot_feature_set_version"
            )
            if feature_set:
                feature_filter = "AND fs.feature_set_version = ?"
                params.append(feature_set)
        if {"intraday_predictions", "intraday_outcomes_v3"}.issubset(tables):
            rows = conn.execute(
                f"""
                SELECT p.prediction_id,
                       p.symbol,
                       p.timestamp,
                       p.direction,
                       0.0 AS signal,
                       0.0 AS confidence,
                       NULL AS p_up,
                       NULL AS p_down,
                       NULL AS p_neutral,
                       NULL AS conviction_level,
                       1 AS should_trade,
                       NULL AS position_size_pct,
                       p.session_type AS regime,
                       0.0 AS conflict_score,
                       p.motor_signals,
                       NULL AS shap_attribution,
                       NULL AS filter_reason,
                       0 AS meta_learner_used,
                       p.entry_price AS price_t0,
                       p.features_json AS snapshot_features_json,
                       p.source_quality_score AS snapshot_source_quality,
                       'funding_lab_intraday_v1' AS snapshot_feature_set_version,
                       o.{target_column} AS outcome_return_5d,
                       o.outcome_return_1h AS outcome_return_1h,
                       o.outcome_return_4h AS outcome_return_4h,
                       NULL AS outcome_logged_at
                FROM intraday_predictions p
                INNER JOIN intraday_outcomes_v3 o ON p.prediction_id = o.prediction_id
                WHERE 1 = 1
                  {symbol_filter}
                  AND o.{target_column} IS NOT NULL
                  {feature_filter}
                ORDER BY p.timestamp ASC, p.symbol ASC
                """,
                params,
            ).fetchall()
        elif "outcomes_v3" in tables:
            rows = conn.execute(
                f"""
                SELECT p.*,
                       {snapshot_select},
                       o.{target_column} AS outcome_return_5d,
                       o.outcome_return_1h AS outcome_return_1h,
                       o.outcome_return_4h AS outcome_return_4h,
                       o.updated_at AS outcome_logged_at
                FROM predictions p
                INNER JOIN outcomes_v3 o ON p.prediction_id = o.prediction_id
                {snapshot_join}
                WHERE 1 = 1
                  {symbol_filter}
                  AND o.{target_column} IS NOT NULL
                  {feature_filter}
                ORDER BY p.timestamp ASC, p.symbol ASC
                """,
                params,
            ).fetchall()
        elif "outcomes" in tables:
            logger.warning(
                "Usando tabla outcomes legacy n_days=5 como target; "
                "target_horizon=%s no esta disponible en %s",
                target_horizon,
                db_path,
            )
            rows = conn.execute(
                f"""
                SELECT p.*,
                       {snapshot_select},
                       o.outcome_return AS outcome_return_5d,
                       NULL AS outcome_return_1h,
                       NULL AS outcome_return_4h,
                       o.logged_at AS outcome_logged_at
                FROM predictions p
                INNER JOIN outcomes o ON p.prediction_id = o.prediction_id
                {snapshot_join}
                WHERE 1 = 1
                  {symbol_filter}
                  AND o.n_days = 5
                  AND o.outcome_return IS NOT NULL
                  {feature_filter}
                ORDER BY p.timestamp ASC, p.symbol ASC
                """,
                params,
            ).fetchall()
        else:
            rows = []
    records = [dict(row) for row in rows]
    for rec in records:
        motor_signals: dict[str, Any]
        try:
            motor_signals = json.loads(rec["motor_signals"]) if rec.get("motor_signals") else {}
        except (TypeError, ValueError):
            motor_signals = {}
        if not isinstance(motor_signals, dict):
            motor_signals = {}

        try:
            snapshot_features = (
                json.loads(rec["snapshot_features_json"])
                if rec.get("snapshot_features_json")
                else {}
            )
        except (TypeError, ValueError):
            snapshot_features = {}
        if isinstance(snapshot_features, dict) and snapshot_features:
            motor_signals = {**motor_signals, **snapshot_features}

        rec["motor_signals"] = motor_signals
    return pd.DataFrame(records)


def load_retraining_history(
    symbol: str,
    db_path: Path | None = None,
    feature_set: str | None = None,
    target_horizon: str = "eod",
) -> pd.DataFrame:
    if db_path is not None:
        try:
            return _load_institutional_history(symbol, db_path, feature_set, target_horizon)
        except Exception as exc:
            logger.warning("No se pudo cargar dataset institucional: %s", exc)
            return pd.DataFrame()
    try:
        return PredictionLogger().get_predictions_for_retraining(symbol)
    except Exception as exc:
        logger.warning("No se pudo cargar prediction_logger: %s", exc)
        return pd.DataFrame()


def _train_learner(
    features: pd.DataFrame,
    target: pd.Series,
    n_splits: int = 5,
    n_estimators: int | None = None,
    num_leaves: int | None = None,
    min_child_samples: int | None = None,
) -> tuple[EnsembleMetaLearner, dict[str, Any]]:
    learner = EnsembleMetaLearner(
        model_type="lightgbm",
        n_splits=n_splits,
        test_size=0.20,
        n_estimators=n_estimators,
        num_leaves=num_leaves,
        min_child_samples=min_child_samples,
    )
    if learner.model_type == "sklearn":
        features = features.fillna(0.0)
    target = target.astype(int)
    _assert_class_distribution(target)
    if len(features) < n_splits + 10:
        raise ValueError(f"Dataset insuficiente para entrenar: {len(features)} filas.")

    logger.info(
        "Entrenando meta-learner: rows=%s features=%s folds=%s backend=%s estimators=%s leaves=%s min_child=%s",
        len(features),
        len(features.columns),
        n_splits,
        learner.model_type,
        n_estimators or "default",
        num_leaves or "default",
        min_child_samples or "default",
    )
    sample_weights = _class_balanced_sample_weights(target)
    learner.fit(features, target, sample_weights=sample_weights)

    metrics = {
        "n_samples": int(len(features)),
        "features": list(features.columns),
        "metrics_by_fold": learner.cv_scores_,
        "mean_accuracy": float(np.nanmean([m["accuracy"] for m in learner.cv_scores_])),
        "mean_log_loss": float(np.nanmean([m["log_loss"] for m in learner.cv_scores_])),
        "mean_precision_down": _nanmean_or_zero(
            [m.get("precision_down", np.nan) for m in learner.cv_scores_]
        ),
        "mean_precision_neutral": _nanmean_or_zero(
            [m.get("precision_neutral", np.nan) for m in learner.cv_scores_]
        ),
        "mean_precision_up": _nanmean_or_zero(
            [m.get("precision_up", np.nan) for m in learner.cv_scores_]
        ),
    }
    learner.mean_accuracy = metrics["mean_accuracy"]
    return learner, metrics


def train_and_save(
    features: pd.DataFrame,
    target: pd.Series,
    output_path: Path = META_LEARNER_PATH,
    n_splits: int = 5,
) -> dict[str, Any]:
    learner, metrics = _train_learner(features, target, n_splits=n_splits)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(learner, output_path)
    metrics["output_path"] = str(output_path)
    return metrics


def train_and_save_real(
    features: pd.DataFrame,
    target: pd.Series,
    output_path: Path = META_LEARNER_PATH,
    n_splits: int = 5,
    min_real_samples: int = MIN_REAL_SAMPLES,
    n_estimators: int | None = None,
    num_leaves: int | None = None,
    min_child_samples: int | None = None,
    side: str = "generic",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Real-mode training: relax overfitting guards, then gate-save vs prior."""
    _validate_real_dataset(features, target, min_real_samples=min_real_samples, side=side)

    learner, metrics = _train_learner(
        features,
        target,
        n_splits=n_splits,
        n_estimators=n_estimators,
        num_leaves=num_leaves,
        min_child_samples=min_child_samples,
    )
    new_acc = metrics["mean_accuracy"]
    old_acc = _load_existing_mean_accuracy(output_path)

    metrics["prev_mean_accuracy"] = old_acc
    if new_acc > old_acc - ACCURACY_TOLERANCE:
        if metadata:
            for key, value in metadata.items():
                setattr(learner, key, value)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(learner, output_path)
        logger.info("Modelo actualizado: %.3f -> %.3f", old_acc, new_acc)
        metrics["saved"] = True
        metrics["output_path"] = str(output_path)
        _hot_reload_router_meta_learner()
    else:
        logger.warning(
            "ABORTANDO: nuevo modelo (%.3f) peor que actual (%.3f) fuera de tolerancia %.2f",
            new_acc,
            old_acc,
            ACCURACY_TOLERANCE,
        )
        metrics["saved"] = False
        metrics["output_path"] = None
    return metrics


def _hot_reload_router_meta_learner() -> None:
    """Best-effort: reload the router-cached learner so changes apply sin restart."""
    try:
        from backend.api.routes.probabilistic_router import get_or_load_meta_learner

        get_or_load_meta_learner(force_reload=True)
        logger.info("Router meta-learner recargado en memoria.")
    except Exception as exc:
        logger.warning("Hot-reload fallo (no critico): %s", exc)


def train_for_symbol(
    symbol: str,
    days: int,
    output_path: Path = META_LEARNER_PATH,
    db_path: Path | None = None,
    feature_set: str | None = None,
    target_horizon: str = "eod",
    return_threshold: float = RETURN_THRESHOLD,
    side: str = "generic",
) -> dict[str, Any]:
    history = load_retraining_history(symbol, db_path, feature_set, target_horizon)
    if len(history) >= MIN_HISTORY_SAMPLES:
        history = apply_side_training_filter(history, side)
        features, target = dataset_from_prediction_history(
            history,
            return_threshold=return_threshold,
            side=side,
        )
        source = "prediction_logger"
    else:
        prices = download_close_prices(symbol, days)
        features, target = build_synthetic_dataset(
            symbol,
            prices,
            return_threshold=return_threshold,
            side=side,
        )
        source = "synthetic_yfinance"

    if len(features) < MIN_HISTORY_SAMPLES:
        logger.warning("Dataset bootstrap con %s muestras; modelo inicial debil.", len(features))

    result = train_and_save(features, target, output_path=output_path)
    result["source"] = source
    return result


def train_for_symbol_real(
    symbol: str,
    output_path: Path = META_LEARNER_PATH,
    db_path: Path | None = None,
    feature_set: str | None = None,
    min_real_samples: int = MIN_REAL_SAMPLES,
    n_splits: int = 5,
    n_estimators: int | None = None,
    num_leaves: int | None = None,
    min_child_samples: int | None = None,
    target_horizon: str = "eod",
    return_threshold: float = RETURN_THRESHOLD,
    side: str = "generic",
) -> dict[str, Any]:
    history = load_retraining_history(symbol, db_path, feature_set, target_horizon)
    history = apply_side_training_filter(history, side)
    features, target = dataset_from_real_outcomes(
        history,
        return_threshold=return_threshold,
        side=side,
    )
    if features.empty:
        raise ValueError(
            f"No hay predicciones con outcome para {symbol}. "
            f"Ejecutar audit_prediction_quality.py primero."
        )
    result = train_and_save_real(
        features,
        target,
        output_path=output_path,
        n_splits=n_splits,
        min_real_samples=min_real_samples,
        n_estimators=n_estimators,
        num_leaves=num_leaves,
        min_child_samples=min_child_samples,
        side=side,
        metadata={
            "training_side": side,
            "target_horizon": target_horizon,
            "return_threshold": return_threshold,
            "side_filter": _side_filter_metadata(side),
            "version": f"real_{side}_{target_horizon}_v1",
        },
    )
    result["source"] = "prediction_logger_real"
    result["training_side"] = side
    return result


def _side_filter_metadata(side: str) -> dict[str, Any]:
    if side == "long":
        return {
            "trend_score_min": 0.75,
            "volume_score": ">= median",
            "mean_rev_signal": "p10..p90",
            "price_return_5d": "<= p80",
        }
    if side == "short":
        return {
            "trend_score_max": -0.70,
            "market_structure_trend": -1,
            "vwap_distance": "< 0",
            "rsi_or_mean_reversion": "rsi_14 >= 55 OR mean_rev_signal < 0",
            "vol_ratio_5_20": "0.60..1.30",
        }
    return {}


def _default_output_for_side(side: str, output_dir: Path | None = None) -> Path:
    root = output_dir or META_LEARNER_PATH.parent
    if side == "long":
        return root / META_LEARNER_LONG_PATH.name
    if side == "short":
        return root / META_LEARNER_SHORT_PATH.name
    return META_LEARNER_PATH


def train_side_models(
    symbol: str,
    *,
    db_path: Path | None,
    side: str,
    output_path: Path | None,
    output_dir: Path | None = None,
    feature_set: str | None = None,
    min_real_samples: int = MIN_REAL_SAMPLES,
    n_splits: int = 5,
    n_estimators: int | None = None,
    num_leaves: int | None = None,
    min_child_samples: int | None = None,
    target_horizon: str = "1h",
    return_threshold: float = RETURN_THRESHOLD,
) -> dict[str, Any]:
    sides = ("long", "short") if side == "both" else (side,)
    if any(item not in {"long", "short"} for item in sides):
        raise ValueError(f"side invalido: {side}")

    side_results: dict[str, dict[str, Any]] = {}
    for item in sides:
        resolved_output = output_path if output_path is not None and len(sides) == 1 else None
        if resolved_output is None:
            resolved_output = _default_output_for_side(item, output_dir)
        side_results[item] = train_for_symbol_real(
            symbol,
            output_path=resolved_output,
            db_path=db_path,
            feature_set=feature_set,
            min_real_samples=min_real_samples,
            n_splits=n_splits,
            n_estimators=n_estimators,
            num_leaves=num_leaves,
            min_child_samples=min_child_samples,
            target_horizon=target_horizon,
            return_threshold=return_threshold,
            side=item,
        )

    return {
        "source": "prediction_logger_real",
        "training_side": side,
        "side_results": side_results,
        "n_samples": int(sum(result.get("n_samples", 0) for result in side_results.values())),
        "metrics_by_fold": [],
        "mean_accuracy": float(
            np.nanmean([result.get("mean_accuracy", np.nan) for result in side_results.values()])
        ),
        "mean_log_loss": float(
            np.nanmean([result.get("mean_log_loss", np.nan) for result in side_results.values()])
        ),
        "mean_precision_down": _nanmean_or_zero(
            [result.get("mean_precision_down", np.nan) for result in side_results.values()]
        ),
        "mean_precision_neutral": _nanmean_or_zero(
            [result.get("mean_precision_neutral", np.nan) for result in side_results.values()]
        ),
        "mean_precision_up": _nanmean_or_zero(
            [result.get("mean_precision_up", np.nan) for result in side_results.values()]
        ),
        "output_path": {key: value.get("output_path") for key, value in side_results.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train QuantumAnalyzer EnsembleMetaLearner.")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--output", default=str(META_LEARNER_PATH))
    parser.add_argument("--db-path", type=Path, default=INSTITUTIONAL_DB_PATH)
    parser.add_argument("--feature-set", default=None)
    parser.add_argument("--source", choices=("auto", "synthetic", "real"), default=None)
    parser.add_argument("--min-real-samples", type=int, default=MIN_REAL_SAMPLES)
    parser.add_argument("--n-splits", type=int, default=5)
    parser.add_argument("--n-estimators", type=int, default=None)
    parser.add_argument("--num-leaves", type=int, default=None)
    parser.add_argument("--min-child-samples", type=int, default=None)
    parser.add_argument("--target-horizon", choices=TARGET_HORIZONS, default="eod")
    parser.add_argument("--return-threshold", type=float, default=RETURN_THRESHOLD)
    parser.add_argument("--side", choices=CLI_SIDES, default="both")
    parser.add_argument(
        "--mode",
        choices=("auto", "synthetic", "real"),
        default="auto",
        help="auto: prediction_logger si hay >=50 muestras, sino sintetico. "
        "real: solo prediction_logger con gating por accuracy. "
        "synthetic: forzar sintetico yfinance.",
    )
    args = parser.parse_args()
    mode = args.source or args.mode

    if mode == "real":
        output_path = Path(args.output)
        explicit_output = output_path if output_path != META_LEARNER_PATH else None
        result = train_side_models(
            args.symbol,
            db_path=args.db_path,
            side=args.side,
            output_path=explicit_output,
            output_dir=META_LEARNER_PATH.parent,
            feature_set=args.feature_set,
            min_real_samples=args.min_real_samples,
            n_splits=args.n_splits,
            n_estimators=args.n_estimators,
            num_leaves=args.num_leaves,
            min_child_samples=args.min_child_samples,
            target_horizon=args.target_horizon,
            return_threshold=args.return_threshold,
        )
    elif mode == "synthetic":
        prices = download_close_prices(args.symbol, args.days)
        features, target = build_synthetic_dataset(
            args.symbol,
            prices,
            return_threshold=args.return_threshold,
        )
        result = train_and_save(
            features, target, output_path=Path(args.output), n_splits=args.n_splits  # nosec # NOSONAR
        )
        result["source"] = "synthetic_yfinance"
    else:
        result = train_for_symbol(
            args.symbol,
            args.days,
            Path(args.output),  # nosec # NOSONAR
            args.db_path,
            args.feature_set,
            target_horizon=args.target_horizon,
            return_threshold=args.return_threshold,
        )

    logger.info("Meta-learner guardado en: %s", result.get("output_path"))
    logger.info("Fuente dataset: %s", result["source"])
    logger.info("Muestras: %s", result["n_samples"])
    for metric in result["metrics_by_fold"]:
        logger.info(
            "fold={fold} accuracy={accuracy:.4f} log_loss={log_loss:.4f} "
            "precision_down={precision_down:.4f} precision_neutral={precision_neutral:.4f} "
            "precision_up={precision_up:.4f}".format(
                precision_down=float(metric.get("precision_down", 0.0)),
                precision_neutral=float(metric.get("precision_neutral", 0.0)),
                precision_up=float(metric.get("precision_up", 0.0)),
                **metric,
            )
        )
    logger.info("mean_accuracy=%.4f", result["mean_accuracy"])
    logger.info("mean_log_loss=%.4f", result["mean_log_loss"])
    logger.info(
        "mean_precision_down=%.4f mean_precision_neutral=%.4f mean_precision_up=%.4f",
        result.get("mean_precision_down", 0.0),
        result.get("mean_precision_neutral", 0.0),
        result.get("mean_precision_up", 0.0),
    )
    if "prev_mean_accuracy" in result:
        logger.info(
            "prev_mean_accuracy=%.4f saved=%s",
            result["prev_mean_accuracy"],
            result.get("saved"),
        )


if __name__ == "__main__":
    main()
