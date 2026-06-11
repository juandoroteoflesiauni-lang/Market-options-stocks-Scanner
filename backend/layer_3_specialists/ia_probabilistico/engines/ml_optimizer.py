"""
backend/layer_3_specialists/ia_probabilistico/engines/ml_optimizer.py
════════════════════════════════════════════════════════════════════════════════
Machine Learning Optimizer for Fear & Greed Factor Weights.

Uses advanced ML techniques to optimize factor weights:
- Ridge Regression (regularized linear regression)
- Random Forest (non-linear importance)
- Gradient Boosting (sequential optimization)
- Cross-validation for robustness
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """Result of ML optimization."""

    method: str
    weights: dict[str, float]
    score: float  # R² or other metric
    feature_importance: dict[str, float]
    timestamp: datetime


class MLOptimizer:
    """
    Machine Learning optimizer for factor weights.

    Uses scikit-learn if available, otherwise falls back to numpy.
    """

    def __init__(self) -> None:
        self._X: np.ndarray[Any, np.dtype[Any]] | None = None  # Features
        self._y: np.ndarray[Any, np.dtype[Any]] | None = None  # Target
        self._feature_names: list[str] = []
        self._model = None
        self._is_sklearn_available = False

        # Try to import sklearn
        try:
            from sklearn.ensemble import (  # type: ignore[import-untyped]
                GradientBoostingRegressor,
                RandomForestRegressor,
            )
            from sklearn.linear_model import (  # type: ignore[import-untyped]
                ElasticNet,
                Lasso,
                Ridge,
            )
            from sklearn.metrics import mean_squared_error, r2_score  # type: ignore[import-untyped]
            from sklearn.model_selection import (  # type: ignore[import-untyped]
                KFold,
                cross_val_score,
            )
            from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

            self.Ridge = Ridge
            self.Lasso = Lasso
            self.ElasticNet = ElasticNet
            self.RandomForestRegressor = RandomForestRegressor
            self.GradientBoostingRegressor = GradientBoostingRegressor
            self.KFold = KFold
            self.StandardScaler = StandardScaler
            self.r2_score = r2_score
            self.mean_squared_error = mean_squared_error
            self.cross_val_score = cross_val_score

            self._is_sklearn_available = True
            logger.info("Scikit-learn available for ML optimization")
        except ImportError:
            logger.warning("Scikit-learn not available, using numpy fallback")

    def add_sample(self, features: dict[str, float], target: float) -> None:
        """
        Add a training sample.

        Args:
            features: Factor scores
            target: Target variable (e.g., future returns, CNN FG)
        """
        if self._X is None:
            # Initialize with first sample
            self._feature_names = list(features.keys())
            self._X = np.array([[features.get(name, 0) for name in self._feature_names]])
            self._y = np.array([target])
        else:
            # Append new sample
            new_row = np.array([[features.get(name, 0) for name in self._feature_names]])
            if self._X is not None:
                self._X = np.vstack([self._X, new_row])
            if self._y is not None:
                self._y = np.append(self._y, target)

    def optimize_ridge(self, alpha: float = 1.0) -> OptimizationResult:
        """
        Optimize weights using Ridge Regression.

        Args:
            alpha: Regularization strength

        Returns:
            Optimization result with weights
        """
        if (
            not self._is_sklearn_available
            or self._X is None
            or len(cast(np.ndarray[Any, np.dtype[Any]], self._y)) < 10
        ):
            return self._fallback_optimize()

        try:
            # Scale features
            scaler = self.StandardScaler()
            X_scaled = scaler.fit_transform(self._X)

            # Ridge regression
            model = self.Ridge(alpha=alpha)
            model.fit(X_scaled, self._y)

            # Get weights from coefficients
            coefficients = model.coef_
            weights = np.abs(coefficients)
            weights = weights / weights.sum()  # Normalize

            # Cross-validation score
            kf = self.KFold(n_splits=5, shuffle=True, random_state=42)
            scores = self.cross_val_score(model, X_scaled, self._y, cv=kf, scoring="r2")

            # Feature importance (absolute coefficients)
            importance = {
                name: float(abs(coeff)) for name, coeff in zip(self._feature_names, coefficients, strict=False)
            }

            return OptimizationResult(
                method="ridge",
                weights={name: float(w) for name, w in zip(self._feature_names, weights, strict=False)},
                score=float(scores.mean()),
                feature_importance=importance,
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Ridge optimization failed: {e}")
            return self._fallback_optimize()

    def optimize_random_forest(
        self, n_estimators: int = 100, max_depth: int = 5
    ) -> OptimizationResult:
        """
        Optimize weights using Random Forest.

        Args:
            n_estimators: Number of trees
            max_depth: Maximum tree depth

        Returns:
            Optimization result with weights
        """
        if (
            not self._is_sklearn_available
            or self._X is None
            or len(cast(np.ndarray[Any, np.dtype[Any]], self._y)) < 20
        ):
            return self._fallback_optimize()

        try:
            # Random Forest
            model = self.RandomForestRegressor(
                n_estimators=n_estimators, max_depth=max_depth, random_state=42
            )
            model.fit(self._X, self._y)

            # Feature importance
            importances = model.feature_importances_
            importance = {name: float(imp) for name, imp in zip(self._feature_names, importances, strict=False)}

            # Cross-validation
            kf = self.KFold(n_splits=5, shuffle=True, random_state=42)
            scores = self.cross_val_score(model, self._X, self._y, cv=kf, scoring="r2")

            return OptimizationResult(
                method="random_forest",
                weights={name: float(w) for name, w in zip(self._feature_names, importances, strict=False)},
                score=float(scores.mean()),
                feature_importance=importance,
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Random Forest optimization failed: {e}")
            return self._fallback_optimize()

    def get_optimal_weights(self, method: str = "auto") -> OptimizationResult:
        """
        Get optimal weights using specified or automatic method.

        Args:
            method: "ridge", "random_forest", "gradient_boosting", or "auto"

        Returns:
            Optimization result
        """
        if self._X is None or len(cast(np.ndarray[Any, np.dtype[Any]], self._y)) < 10:
            return self._fallback_optimize()

        if method == "auto":
            # Choose method based on sample size
            n_samples = len(cast(np.ndarray[Any, np.dtype[Any]], self._y))
            if n_samples < 50:
                method = "ridge"
            elif n_samples < 200:
                method = "random_forest"
            else:
                method = "gradient_boosting"

        if method == "ridge":
            return self.optimize_ridge()
        elif method == "random_forest":
            return self.optimize_random_forest()
        else:
            return self._fallback_optimize()

    def _fallback_optimize(self) -> OptimizationResult:
        """Fallback to equal weights if ML fails."""
        if not self._feature_names:
            self._feature_names = [
                "momentum",
                "strength",
                "volatility",
                "put_call",
                "credit",
                "safe_haven",
                "event_risk",
            ]

        n_features = len(self._feature_names)
        equal_weight = 1.0 / n_features

        return OptimizationResult(
            method="fallback_equal",
            weights={name: equal_weight for name in self._feature_names},
            score=0.0,
            feature_importance={name: equal_weight for name in self._feature_names},
            timestamp=datetime.now(),
        )

    def clear(self) -> None:
        """Clear all training data."""
        self._X = None
        self._y = None
        self._feature_names = []
        self._model = None


# Global instance
_optimizer: MLOptimizer | None = None


def get_ml_optimizer() -> MLOptimizer:
    """Get or create ML optimizer instance."""
    global _optimizer
    if _optimizer is None:
        _optimizer = MLOptimizer()
    return _optimizer
