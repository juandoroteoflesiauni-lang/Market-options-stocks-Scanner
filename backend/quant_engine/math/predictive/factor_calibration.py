from __future__ import annotations
"""
backend/engine/metrics/factor_calibration.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Factor Calibration Engine — PCA and statistical analysis for optimal weights.
Stateless and vectorized implementations.
"""


import logging
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger(__name__)

FloatArray = npt.NDArray[np.float64]


class CalibrationReport(BaseModel):
    """Pydantic model representing a complete calibration report (Institutional Alignment)."""

    model_config = ConfigDict(frozen=True)

    timestamp: str
    observation_count: int
    pca_weights: dict[str, float]
    correlation_matrix: dict[str, dict[str, float]]
    redundant_pairs: list[tuple[str, str]]
    optimized_weights: dict[str, float]
    equal_weights: dict[str, float]
    recommendations: list[str]


class FactorCalibrationEngine:
    """
    Calibrates factor weights using statistical analysis.
    Purely stateless and vectorized.
    """

    def calculate_pca_weights(
        self,
        x: FloatArray,
        factor_names: list[str],
    ) -> Result[dict[str, float]]:
        """
        Calculate factor weights using PCA.
        """
        n = x.shape[0]
        if n < 30:
            return Result.failure(
                reason=f"Insufficient data for PCA: need at least 30 observations, got {n}"
            )
        if x.shape[1] != len(factor_names):
            return Result.failure(
                reason=(
                    f"Dimension mismatch: X has {x.shape[1]} columns, "
                    f"but got {len(factor_names)} factor names"
                )
            )

        try:
            # Standardize
            mean = np.mean(x, axis=0)
            std = np.std(x, axis=0)
            standardized = (x - mean) / (std + 1e-8)

            # PCA via SVD
            _, _, vt = np.linalg.svd(standardized, full_matrices=False)

            # First principal component weights
            pc1 = vt[0, :]
            abs_pc1 = np.abs(pc1)
            sum_abs = np.sum(abs_pc1)
            if sum_abs == 0.0:
                return Result.failure(reason="Sum of PCA weights is zero")

            weights = abs_pc1 / sum_abs

            res = {name: float(w) for name, w in zip(factor_names, weights, strict=True)}
            return Result.success(res)
        except Exception as e:
            logger.error("PCA failed: %s", e)
            return Result.failure(reason=f"PCA failed: {e}")

    def calculate_correlation_matrix(
        self,
        x: FloatArray,
        factor_names: list[str],
    ) -> Result[dict[str, dict[str, float]]]:
        """
        Calculate correlation matrix between factors.
        """
        n = x.shape[0]
        if n < 10:
            return Result.failure(
                reason=(
                    f"Insufficient data for correlation matrix: "
                    f"need at least 10 observations, got {n}"
                )
            )
        if x.shape[1] != len(factor_names):
            return Result.failure(
                reason=(
                    f"Dimension mismatch: X has {x.shape[1]} columns, "
                    f"but got {len(factor_names)} factor names"
                )
            )

        try:
            # Calculate correlation matrix
            corr_matrix = np.corrcoef(x, rowvar=False)

            # Convert to dict
            result: dict[str, dict[str, float]] = {}
            for i, name1 in enumerate(factor_names):
                result[name1] = {}
                for j, name2 in enumerate(factor_names):
                    val = corr_matrix[i, j] if corr_matrix.ndim == 2 else corr_matrix
                    result[name1][name2] = float(val)

            return Result.success(result)
        except Exception as e:
            logger.error("Correlation matrix calculation failed: %s", e)
            return Result.failure(reason=f"Correlation matrix calculation failed: {e}")

    def identify_redundant_factors(
        self,
        x: FloatArray,
        factor_names: list[str],
        threshold: float = 0.9,
    ) -> Result[list[tuple[str, str]]]:
        """
        Identify highly correlated (redundant) factor pairs.
        """
        corr_matrix_wrapped = self.calculate_correlation_matrix(x, factor_names)
        if corr_matrix_wrapped.is_failure:
            return Result.failure(
                reason=f"Cannot identify redundant factors: {corr_matrix_wrapped.reason}"
            )
        corr_matrix = corr_matrix_wrapped.unwrap()

        redundant = []
        seen = set()

        for f1, correlations in corr_matrix.items():
            for f2, corr in correlations.items():
                if f1 != f2 and abs(corr) > threshold:
                    pair = tuple(sorted([f1, f2]))
                    if pair not in seen:
                        seen.add(pair)
                        redundant.append((f1, f2))

        return Result.success(redundant)

    def optimize_for_prediction(
        self,
        x: FloatArray,
        y: FloatArray,
        factor_names: list[str],
    ) -> Result[dict[str, float]]:
        """
        Optimize factor weights to maximize predictive power.
        Uses regression to find weights that best predict target.
        """
        n = x.shape[0]
        if n < 30:
            return Result.failure(
                reason=f"Insufficient data for optimization: need at least 30 observations, got {n}"
            )
        if x.shape[1] != len(factor_names):
            return Result.failure(
                reason=(
                    f"Dimension mismatch: X has {x.shape[1]} columns, "
                    f"but got {len(factor_names)} factor names"
                )
            )
        if len(y) != n:
            return Result.failure(
                reason=f"Dimension mismatch: X has {n} rows, but y has {len(y)} elements"
            )

        try:
            # Simple linear regression via least squares
            # Add bias term
            x_bias = np.column_stack([np.ones(len(x)), x])

            # Solve normal equations
            coeffs = np.linalg.lstsq(x_bias, y, rcond=None)[0]

            # Use absolute coefficients as weights
            weights = np.abs(coeffs[1:])  # Exclude bias
            sum_weights = weights.sum()
            if sum_weights == 0.0:
                return Result.failure(reason="Sum of optimized weights is zero")

            weights = weights / sum_weights  # Normalize

            res = {name: float(w) for name, w in zip(factor_names, weights, strict=True)}
            return Result.success(res)
        except Exception as e:
            logger.error("Optimization failed: %s", e)
            return Result.failure(reason=f"Optimization failed: {e}")

    def _equal_weights(self, factor_names: list[str]) -> dict[str, float]:
        """Return equal weights for all factors."""
        if not factor_names:
            return {
                "momentum": 1 / 7,
                "strength": 1 / 7,
                "volatility": 1 / 7,
                "put_call": 1 / 7,
                "credit": 1 / 7,
                "safe_haven": 1 / 7,
                "event_risk": 1 / 7,
            }

        weight = 1.0 / len(factor_names)
        return {name: weight for name in factor_names}

    def get_calibration_report(
        self,
        x: FloatArray,
        y: FloatArray,
        factor_names: list[str],
    ) -> Result[CalibrationReport]:
        """
        Generate comprehensive calibration report.
        """
        try:
            n = x.shape[0]
            if n < 30:
                return Result.failure(
                    reason=f"Insufficient data for report: need at least 30 observations, got {n}"
                )

            pca_wrapped = self.calculate_pca_weights(x, factor_names)
            if pca_wrapped.is_failure:
                return Result.failure(reason=f"PCA calculation failed: {pca_wrapped.reason}")
            pca_weights = pca_wrapped.unwrap()

            corr_wrapped = self.calculate_correlation_matrix(x, factor_names)
            if corr_wrapped.is_failure:
                return Result.failure(reason=f"Correlation failed: {corr_wrapped.reason}")
            correlation_matrix = corr_wrapped.unwrap()

            redundant_wrapped = self.identify_redundant_factors(x, factor_names)
            if redundant_wrapped.is_failure:
                return Result.failure(reason=f"Redundancy check failed: {redundant_wrapped.reason}")
            redundant_pairs = redundant_wrapped.unwrap()

            opt_wrapped = self.optimize_for_prediction(x, y, factor_names)
            if opt_wrapped.is_failure:
                return Result.failure(reason=f"Optimization failed: {opt_wrapped.reason}")
            optimized_weights = opt_wrapped.unwrap()

            equal_weights = self._equal_weights(factor_names)

            # Generate recommendations
            recommendations: list[str] = []
            if redundant_pairs:
                recommendations.append(
                    f"Remove {len(redundant_pairs)} redundant factor pairs (correlation > 0.9)"
                )

            # Check if PCA weights differ significantly from equal
            max_diff = max(
                abs(pca_weights.get(k, 0.0) - equal_weights.get(k, 0.0)) for k in factor_names
            )
            if max_diff > 0.05:
                recommendations.append("PCA suggests non-equal weights may be beneficial")

            if n < 100:
                recommendations.append(
                    "Collect more data (currently < 100 observations) for better calibration"
                )

            report = CalibrationReport(
                timestamp=datetime.now(tz=UTC).isoformat(),
                observation_count=n,
                pca_weights=pca_weights,
                correlation_matrix=correlation_matrix,
                redundant_pairs=redundant_pairs,
                optimized_weights=optimized_weights,
                equal_weights=equal_weights,
                recommendations=recommendations,
            )
            return Result.success(report)
        except Exception as e:
            logger.error("Calibration report generation failed: %s", e)
            return Result.failure(reason=f"Calibration report generation failed: {e}")
