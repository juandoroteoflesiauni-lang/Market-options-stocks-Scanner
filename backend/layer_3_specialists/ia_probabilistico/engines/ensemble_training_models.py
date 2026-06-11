"""Contracts for auditable EnsembleMetaLearner training runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

DEFAULT_NAN_ROW_THRESHOLD = 0.30
DEFAULT_FORWARD_PERIODS = (1, 5, 10)
DEFAULT_PRIMARY_PERIOD = 5
DEFAULT_N_SPLITS = 5
DEFAULT_TEST_SIZE = 0.20
DEFAULT_DECAY_RATE = 0.01
DEFAULT_CONFIDENCE_THRESHOLD = 0.50


@dataclass(slots=True)
class TrainingConfig:
    """Parameters governing an offline meta-learner training run."""

    nan_row_threshold: float = DEFAULT_NAN_ROW_THRESHOLD
    forward_periods: tuple[int, ...] = DEFAULT_FORWARD_PERIODS
    primary_period: int = DEFAULT_PRIMARY_PERIOD
    n_splits: int = DEFAULT_N_SPLITS
    test_size: float = DEFAULT_TEST_SIZE
    decay_rate: float = DEFAULT_DECAY_RATE
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    save_path: str | Path | None = None


@dataclass(slots=True)
class TrainingResult:
    """Auditable output of a complete meta-learner training run."""

    symbol: str
    metrics_by_fold: list[dict[str, object]]
    mean_accuracy: float
    mean_log_loss: float
    mean_sharpe: float
    best_fold: int
    feature_importance: pd.DataFrame
    n_samples_train: int
    n_samples_test: int
    training_date: str
    mean_brier: float = float("nan")
    naive_accuracy: float = float("nan")
    rule_based_accuracy: float = float("nan")
    model_gate: dict[str, object] | None = None
    confusion_matrix: list[list[int]] | None = None
    learner: object | None = None
    scaler: object | None = None
    error_msg: str | None = None
