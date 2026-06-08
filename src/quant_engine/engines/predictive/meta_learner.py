"""
backend/engine/metrics/meta_learner.py
Sector: Options / Meta Learner (Inference Only)
[ARCH-1, PD-4]

Distilled Ensemble Meta-Learner Predictor.
Consumes engine outputs to predict forward direction.
Optimised for asynchronous inference without pandas/shap overhead.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import numpy as np
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger(__name__)

class CalibratorBundle(BaseModel):
    """Container for the fitted calibrator model."""
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    
    calibrator: Any = None  # Usually a fitted LogisticRegression
    is_fitted: bool = False

    def transform(self, raw_probs: np.ndarray[Any, np.dtype[np.float64]]) -> np.ndarray[Any, np.dtype[np.float64]]:
        if not self.is_fitted or self.calibrator is None:
            return raw_probs
        
        proba = self.calibrator.predict_proba(np.asarray(raw_probs, dtype=float))
        classes = getattr(self.calibrator, "classes_", np.array([0, 1, 2]))
        out = np.zeros_like(raw_probs, dtype=float)
        
        for col_idx, cls_idx in enumerate(classes):
            cls_int = int(cls_idx)
            if 0 <= cls_int < out.shape[1]:
                out[:, cls_int] = proba[:, col_idx]
                
        row_sums = out.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        return cast(np.ndarray[Any, np.dtype[np.float64]], out / row_sums)


class EnsembleMetaLearnerPredictor:
    """
    Distilled Meta-Learner for inference only.
    """
    def __init__(
        self, 
        model: Any, 
        feature_names: list[str], 
        calibrator: CalibratorBundle | None = None
    ) -> None:
        self.model_ = model
        self.feature_names = feature_names
        self.calibrator_ = calibrator or CalibratorBundle()
        self.is_fitted = model is not None

    def _predict_proba_raw(self, x_arr: np.ndarray[Any, np.dtype[np.float64]]) -> np.ndarray[Any, np.dtype[np.float64]]:
        proba = self.model_.predict_proba(x_arr)
        classes = getattr(self.model_, "classes_", np.array([0, 1, 2]))
        if list(classes) == [0, 1, 2]:
            return cast(np.ndarray[Any, np.dtype[np.float64]], proba)
        
        out = np.zeros((proba.shape[0], 3))
        for col, cls in enumerate(classes):
            if 0 <= int(cls) < 3:
                out[:, int(cls)] = proba[:, col]
        return out

    def _apply_price_directional_prior(
        self, 
        x_arr: np.ndarray[Any, np.dtype[np.float64]], 
        probabilities: np.ndarray[Any, np.dtype[np.float64]]
    ) -> np.ndarray[Any, np.dtype[np.float64]]:
        """Use explicit price momentum features as a small anti-inversion prior."""
        feature_scales = {
            "price__return_5d": 0.05,
            "price__return_20d": 0.10,
            "price__rsi_14_normalized": 1.00,
            "price__price_vs_ma20": 0.05,
        }
        
        components = []
        for col, scale in feature_scales.items():
            try:
                idx = self.feature_names.index(col)
                values = x_arr[:, idx].astype(float)
                components.append(np.nan_to_num(values / scale, nan=0.0, posinf=0.0, neginf=0.0))
            except ValueError:
                continue

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
        return cast(np.ndarray[Any, np.dtype[np.float64]], out / row_sums)

    def predict_proba(self, x: dict[str, float] | np.ndarray[Any, Any]) -> Result[dict[str, float]]:
        """
        Returns calibrated class probabilities: {DOWN: float, NEUTRAL: float, UP: float}
        """
        if not self.is_fitted:
            return Result.failure(reason="MetaLearner Predictor is not fitted or model is missing.")
            
        try:
            # Drop Pandas dependency for input, parse native dict or raw numpy array
            if isinstance(x, dict):
                row = [float(x.get(f, 0.0) if x.get(f) is not None else 0.0) for f in self.feature_names]
                x_arr = np.array([row], dtype=np.float64)
            else:
                x_arr = np.asarray(x, dtype=np.float64)
                if x_arr.ndim == 1:
                    x_arr = x_arr.reshape(1, -1)
            
            if x_arr.shape[1] != len(self.feature_names):
                return Result.failure(reason=f"Feature mismatch: expected {len(self.feature_names)}, got {x_arr.shape[1]}")

            raw = self._predict_proba_raw(x_arr)
            calibrated = self.calibrator_.transform(raw)
            final_probs = self._apply_price_directional_prior(x_arr, calibrated)

            return Result.success({
                "DOWN": float(final_probs[0, 0]),
                "NEUTRAL": float(final_probs[0, 1]),
                "UP": float(final_probs[0, 2]),
            })

        except Exception as e:
            logger.error("MetaLearner prediction failed: %s", e)
            return Result.failure(reason=f"Prediction failed: {e}")
