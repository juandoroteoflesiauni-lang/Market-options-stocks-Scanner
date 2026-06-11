"""
backend/engine/metrics/ml_optimizer.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Machine Learning Optimizer for Fear & Greed Factor Weights.
Stateless and vectorized implementations.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger(__name__)

FloatArray = npt.NDArray[np.float64]

# Try to import sklearn at module level
try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.linear_model import Ridge
    from sklearn.model_selection import KFold, cross_val_score
    from sklearn.preprocessing import StandardScaler

    _SKLEARN_AVAILABLE = True
    logger.info("Scikit-learn available for ML optimization")
except ImportError:
    _SKLEARN_AVAILABLE = False
    logger.warning("Scikit-learn not available")


class OptimizationResult(BaseModel):
    """Result of ML optimization."""

    model_config = ConfigDict(frozen=True)

    method: str
    weights: dict[str, float]
    score: float  # R² or other metric
    feature_importance: dict[str, float]
    timestamp: datetime


class MLOptimizer:
    """
    Machine Learning optimizer for factor weights.
    Purely stateless and vectorized.
    """

    def __init__(self) -> None:
        pass

    def optimize_ridge(
        self,
        x: FloatArray,
        y: FloatArray,
        feature_names: list[str],
        alpha: float = 1.0,
    ) -> Result[OptimizationResult]:
        """
        Optimize weights using Ridge Regression.
        """
        if not _SKLEARN_AVAILABLE:
            return Result.failure(reason="scikit-learn is not installed or available")

        n = x.shape[0]
        if n < 10:
            return Result.failure(
                reason=(
                    f"Insufficient data for Ridge optimization: "
                    f"need at least 10 observations, got {n}"
                )
            )
        if x.shape[1] != len(feature_names):
            return Result.failure(
                reason=(
                    f"Dimension mismatch: X has {x.shape[1]} columns, "
                    f"but got {len(feature_names)} feature names"
                )
            )
        if len(y) != n:
            return Result.failure(
                reason=f"Dimension mismatch: X has {n} rows, but y has {len(y)} elements"
            )

        try:
            # Scale features
            scaler = StandardScaler()
            x_scaled = scaler.fit_transform(x)

            # Ridge regression
            model = Ridge(alpha=alpha)
            model.fit(x_scaled, y)

            # Get weights from coefficients
            coefficients = model.coef_
            weights = np.abs(coefficients)
            sum_weights = weights.sum()
            if sum_weights == 0.0:
                return Result.failure(reason="Sum of Ridge coefficients is zero")

            weights = weights / sum_weights  # Normalize

            # Cross-validation score
            kf = KFold(n_splits=5, shuffle=True, random_state=42)
            scores = cross_val_score(model, x_scaled, y, cv=kf, scoring="r2")

            # Feature importance (absolute coefficients)
            importance = {
                name: float(abs(coeff))
                for name, coeff in zip(feature_names, coefficients, strict=True)
            }

            result = OptimizationResult(
                method="ridge",
                weights={name: float(w) for name, w in zip(feature_names, weights, strict=True)},
                score=float(scores.mean()),
                feature_importance=importance,
                timestamp=datetime.now(tz=UTC),
            )
            return Result.success(result)
        except Exception as e:
            logger.error("Ridge optimization failed: %s", e)
            return Result.failure(reason=f"Ridge optimization failed: {e}")

    def optimize_random_forest(
        self,
        x: FloatArray,
        y: FloatArray,
        feature_names: list[str],
        n_estimators: int = 100,
        max_depth: int = 5,
    ) -> Result[OptimizationResult]:
        """
        Optimize weights using Random Forest.
        """
        if not _SKLEARN_AVAILABLE:
            return Result.failure(reason="scikit-learn is not installed or available")

        n = x.shape[0]
        if n < 20:
            return Result.failure(
                reason=(
                    f"Insufficient data for Random Forest optimization: "
                    f"need at least 20 observations, got {n}"
                )
            )
        if x.shape[1] != len(feature_names):
            return Result.failure(
                reason=(
                    f"Dimension mismatch: X has {x.shape[1]} columns, "
                    f"but got {len(feature_names)} feature names"
                )
            )
        if len(y) != n:
            return Result.failure(
                reason=f"Dimension mismatch: X has {n} rows, but y has {len(y)} elements"
            )

        try:
            # Random Forest
            model = RandomForestRegressor(
                n_estimators=n_estimators,
                max_depth=max_depth,
                random_state=42,
            )
            model.fit(x, y)

            # Feature importance
            importances = model.feature_importances_
            importance = {
                name: float(imp) for name, imp in zip(feature_names, importances, strict=True)
            }

            # Cross-validation
            kf = KFold(n_splits=5, shuffle=True, random_state=42)
            scores = cross_val_score(model, x, y, cv=kf, scoring="r2")

            result = OptimizationResult(
                method="random_forest",
                weights={
                    name: float(w) for name, w in zip(feature_names, importances, strict=True)
                },
                score=float(scores.mean()),
                feature_importance=importance,
                timestamp=datetime.now(tz=UTC),
            )
            return Result.success(result)
        except Exception as e:
            logger.error("Random Forest optimization failed: %s", e)
            return Result.failure(reason=f"Random Forest optimization failed: {e}")

    def optimize_gradient_boosting(
        self,
        x: FloatArray,
        y: FloatArray,
        feature_names: list[str],
        n_estimators: int = 100,
        max_depth: int = 3,
    ) -> Result[OptimizationResult]:
        """
        Optimize weights using Gradient Boosting.
        """
        if not _SKLEARN_AVAILABLE:
            return Result.failure(reason="scikit-learn is not installed or available")

        n = x.shape[0]
        if n < 30:
            return Result.failure(
                reason=(
                    f"Insufficient data for Gradient Boosting optimization: "
                    f"need at least 30 observations, got {n}"
                )
            )
        if x.shape[1] != len(feature_names):
            return Result.failure(
                reason=(
                    f"Dimension mismatch: X has {x.shape[1]} columns, "
                    f"but got {len(feature_names)} feature names"
                )
            )
        if len(y) != n:
            return Result.failure(
                reason=f"Dimension mismatch: X has {n} rows, but y has {len(y)} elements"
            )

        try:
            from sklearn.ensemble import GradientBoostingRegressor

            model = GradientBoostingRegressor(
                n_estimators=n_estimators,
                max_depth=max_depth,
                random_state=42,
            )
            model.fit(x, y)

            # Feature importance
            importances = model.feature_importances_
            importance = {
                name: float(imp) for name, imp in zip(feature_names, importances, strict=True)
            }

            # Cross-validation
            kf = KFold(n_splits=5, shuffle=True, random_state=42)
            scores = cross_val_score(model, x, y, cv=kf, scoring="r2")

            result = OptimizationResult(
                method="gradient_boosting",
                weights={
                    name: float(w) for name, w in zip(feature_names, importances, strict=True)
                },
                score=float(scores.mean()),
                feature_importance=importance,
                timestamp=datetime.now(tz=UTC),
            )
            return Result.success(result)
        except Exception as e:
            logger.error("Gradient Boosting optimization failed: %s", e)
            return Result.failure(reason=f"Gradient Boosting optimization failed: {e}")

    def get_optimal_weights(
        self,
        x: FloatArray,
        y: FloatArray,
        feature_names: list[str],
        method: str = "auto",
    ) -> Result[OptimizationResult]:
        """
        Get optimal weights using specified or automatic method.
        """
        n_samples = x.shape[0]
        if n_samples < 10:
            return Result.failure(
                reason=(
                    f"Insufficient data for ML optimization: "
                    f"need at least 10 observations, got {n_samples}"
                )
            )

        if method == "auto":
            if n_samples < 50:
                method = "ridge"
            elif n_samples < 200:
                method = "random_forest"
            else:
                method = "gradient_boosting"

        if method == "ridge":
            return self.optimize_ridge(x, y, feature_names)
        elif method == "random_forest":
            return self.optimize_random_forest(x, y, feature_names)
        elif method == "gradient_boosting":
            return self.optimize_gradient_boosting(x, y, feature_names)
        else:
            return Result.failure(reason=f"Unknown optimization method: {method}")
