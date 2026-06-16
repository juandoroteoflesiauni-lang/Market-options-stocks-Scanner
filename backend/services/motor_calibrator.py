from __future__ import annotations
from typing import Any
"""motor_calibrator.py
=====================
Isotonic / Platt calibration for probabilistic scanner engines.

Converts raw engine scores (arbitrary range) → calibrated P(direction_correct) ∈ [0, 1],
making outputs comparable across the 38+ heterogeneous engines.

Public API
----------
- MotorCalibrator            — main class
- calibrate_to_direction_prob(motor_name, raw_score) -> float   module-level shortcut
- evaluate_calibration(motor_name, y_scores, y_true) -> dict
"""


import math
from pathlib import Path

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# ECE helper
# ---------------------------------------------------------------------------


def _expected_calibration_error(
    y_prob: np.ndarray[Any, Any],
    y_true: np.ndarray[Any, Any],
    n_bins: int = 10,
) -> tuple[float, list[dict]]:
    """Compute ECE and per-bin reliability diagram data.

    Returns
    -------
    ece : float
    bins : list[dict] with keys fraction_of_positives, mean_predicted_value, count
    """
    bins_data: list[dict] = []
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    n = len(y_true)
    ece = 0.0

    for lo, hi in zip(bin_edges[:-1], bin_edges[1:], strict=False):
        mask = (y_prob >= lo) & (y_prob < hi)
        if not mask.any():
            bins_data.append(
                {"fraction_of_positives": None, "mean_predicted_value": None, "count": 0}
            )
            continue
        fop = float(y_true[mask].mean())
        mpv = float(y_prob[mask].mean())
        cnt = int(mask.sum())
        ece += (cnt / n) * abs(fop - mpv)
        bins_data.append({"fraction_of_positives": fop, "mean_predicted_value": mpv, "count": cnt})

    return round(ece, 6), bins_data


# ---------------------------------------------------------------------------
# MotorCalibrator
# ---------------------------------------------------------------------------


class MotorCalibrator:
    """Per-engine calibration registry.

    Supports isotonic regression (default) and Platt scaling (logistic).
    Engines without a fitted calibrator pass through raw (graceful degradation).
    """

    def __init__(self) -> None:
        self._registry: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(
        self,
        motor_name: str,
        y_scores: list[float] | np.ndarray[Any, Any],
        y_true: list[float] | np.ndarray[Any, Any],
    ) -> None:
        """Fit isotonic regression calibrator for *motor_name*.

        Parameters
        ----------
        y_scores : raw engine scores (any range)
        y_true   : binary labels 0/1  (1 = direction was correct)
        """
        xs = np.asarray(y_scores, dtype=float)
        yt = np.asarray(y_true, dtype=float)
        if len(xs) < 3:
            logger.warning("calibrator.fit skipped motor=%s n=%d (need ≥3)", motor_name, len(xs))
            return

        cal = IsotonicRegression(out_of_bounds="clip")
        cal.fit(xs, yt)
        self._registry[motor_name] = ("isotonic", cal)
        logger.info("calibrator.fit motor=%s n=%d method=isotonic", motor_name, len(xs))

    def fit_platt(
        self,
        motor_name: str,
        y_scores: list[float] | np.ndarray[Any, Any],
        y_true: list[float] | np.ndarray[Any, Any],
    ) -> None:
        """Fit Platt scaling (logistic regression) calibrator for *motor_name*."""
        xs = np.asarray(y_scores, dtype=float).reshape(-1, 1)
        yt = np.asarray(y_true, dtype=float)
        if len(xs) < 3:
            logger.warning(
                "calibrator.fit_platt skipped motor=%s n=%d (need ≥3)", motor_name, len(xs)
            )
            return

        cal = LogisticRegression(solver="lbfgs")
        cal.fit(xs, yt)
        self._registry[motor_name] = ("platt", cal)
        logger.info("calibrator.fit_platt motor=%s n=%d", motor_name, len(xs))

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def transform(self, motor_name: str, raw_score: float) -> float:
        """Return calibrated P(direction_correct) ∈ [0, 1].

        Falls back to clipped raw value if motor has no fitted calibrator.
        """
        if not math.isfinite(raw_score):
            return 0.5

        entry = self._registry.get(motor_name)
        if entry is None:
            logger.debug(
                "calibrator.transform motor=%s no calibrator → raw passthrough", motor_name
            )
            return float(np.clip(raw_score, 0.0, 1.0))

        method, cal = entry
        try:
            if method == "isotonic":
                return float(np.clip(cal.predict([raw_score])[0], 0.0, 1.0))
            if method == "platt":
                prob = cal.predict_proba(np.array([[raw_score]]))[0, 1]
                return float(np.clip(prob, 0.0, 1.0))
        except Exception as exc:
            logger.warning("calibrator.transform error motor=%s error=%s", motor_name, exc)

        return float(np.clip(raw_score, 0.0, 1.0))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Serialize registry to *path* using joblib (escritura atómica)."""
        from backend.infrastructure.sqlite_health import atomic_joblib_dump

        atomic_joblib_dump(self._registry, Path(path))
        logger.info("calibrator.save path=%s motors=%d", path, len(self._registry))

    def load(self, path: str) -> None:
        """Load registry from *path*.  Merges into existing registry."""
        loaded: dict[str, Any] = joblib.load(path)
        self._registry.update(loaded)
        logger.info("calibrator.load path=%s motors=%d", path, len(loaded))

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate_calibration(
        self,
        motor_name: str,
        y_scores: list[float] | np.ndarray[Any, Any],
        y_true: list[float] | np.ndarray[Any, Any],
    ) -> dict[str, Any]:
        """Return Brier score, ECE, and reliability diagram data.

        Compares raw scores vs calibrated probabilities so callers can
        verify calibration improves (or at least doesn't hurt) accuracy.
        """
        xs = np.asarray(y_scores, dtype=float)
        yt = np.asarray(y_true, dtype=float)

        calibrated = np.array([self.transform(motor_name, s) for s in xs])

        brier_raw = float(brier_score_loss(yt, np.clip(xs, 0.0, 1.0)))
        brier_cal = float(brier_score_loss(yt, calibrated))

        ece_raw, bins_raw = _expected_calibration_error(np.clip(xs, 0.0, 1.0), yt)
        ece_cal, bins_cal = _expected_calibration_error(calibrated, yt)

        return {
            "motor_name": motor_name,
            "n_samples": len(yt),
            "brier_raw": round(brier_raw, 6),
            "brier_cal": round(brier_cal, 6),
            "ece_raw": ece_raw,
            "ece_cal": ece_cal,
            "reliability_raw": bins_raw,
            "reliability_cal": bins_cal,
        }

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def registered_motors(self) -> list[str]:
        return list(self._registry.keys())

    def is_fitted(self, motor_name: str) -> bool:
        return motor_name in self._registry


# ---------------------------------------------------------------------------
# Module-level singleton + convenience function
# ---------------------------------------------------------------------------

_default_calibrator = MotorCalibrator()


def calibrate_to_direction_prob(motor_name: str, raw_score: float) -> float:
    """Module-level shortcut using the default calibrator singleton.

    Returns P(direction_correct) ∈ [0, 1].
    Gracefully passes raw score through if motor not yet calibrated.
    """
    return _default_calibrator.transform(motor_name, raw_score)


def evaluate_calibration(
    motor_name: str,
    y_scores: list[float] | np.ndarray[Any, Any],
    y_true: list[float] | np.ndarray[Any, Any],
) -> dict[str, Any]:
    """Module-level shortcut for evaluate_calibration on default singleton."""
    return _default_calibrator.evaluate_calibration(motor_name, y_scores, y_true)
