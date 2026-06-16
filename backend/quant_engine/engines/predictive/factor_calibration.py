"""
backend/layer_3_specialists/ia_probabilistico/engines/factor_calibration.py
════════════════════════════════════════════════════════════════════════════════
Factor Calibration Engine — PCA and statistical analysis for optimal weights.

Uses Principal Component Analysis (PCA) and correlation studies to determine
optimal factor weights for Fear & Greed calculation.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import NotRequired, TypedDict

import numpy as np

logger = logging.getLogger(__name__)


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


class CalibrationReport(TypedDict):
    timestamp: str
    observation_count: int
    pca_weights: dict[str, float]
    correlation_matrix: dict[str, dict[str, float]]
    redundant_pairs: list[tuple[str, str]]
    optimized_weights: dict[str, float]
    equal_weights: dict[str, float]
    recommendations: list[str]
    # Populated by API layer after ``get_calibration_report()``.
    method_used: NotRequired[str]
    recommended_weights: NotRequired[dict[str, float]]


class FactorCalibrationEngine:
    """
    Calibrates factor weights using statistical analysis.

    Methods:
    - PCA: Identify dominant factors
    - Correlation analysis: Remove redundant factors
    - Optimization: Find weights that maximize predictive power
    """

    def __init__(self) -> None:
        self._historical_data: list[dict[str, float | datetime]] = []

    def add_observation(self, factors: dict[str, float], target: float) -> None:
        """
        Add an observation for calibration.

        Args:
            factors: Factor scores
            target: Target variable (e.g., future returns, CNN FG)
        """
        self._historical_data.append({**factors, "_target": target, "_timestamp": datetime.now()})

    def calculate_pca_weights(self) -> dict[str, float]:
        """
        Calculate factor weights using PCA.

        Returns:
            Dict mapping factor names to PCA-derived weights
        """
        if len(self._historical_data) < 30:
            logger.warning("Insufficient data for PCA (need 30+ observations)")
            return self._equal_weights()

        # Extract factor names
        factor_names = [k for k in self._historical_data[0].keys() if not k.startswith("_")]

        # Build data matrix
        data_matrix: list[list[float]] = []
        for obs in self._historical_data:
            row = [_as_float(obs.get(f, 0.0)) for f in factor_names]
            data_matrix.append(row)

        data_matrix_arr = np.asarray(data_matrix, dtype=np.float64)

        # Standardize
        mean = np.mean(data_matrix_arr, axis=0)
        std = np.std(data_matrix_arr, axis=0)
        standardized = (data_matrix_arr - mean) / (std + 1e-8)

        # PCA via SVD
        try:
            U, S, Vt = np.linalg.svd(standardized, full_matrices=False)

            # First principal component weights
            pc1 = Vt[0, :]
            weights = np.abs(pc1) / np.sum(np.abs(pc1))

            return {name: float(w) for name, w in zip(factor_names, weights, strict=False)}
        except Exception as e:
            logger.error(f"PCA failed: {e}")
            return self._equal_weights()

    def calculate_correlation_matrix(self) -> dict[str, dict[str, float]]:
        """
        Calculate correlation matrix between factors.

        Returns:
            Correlation matrix as nested dict
        """
        if len(self._historical_data) < 10:
            return {}

        factor_names = [k for k in self._historical_data[0].keys() if not k.startswith("_")]

        # Build data matrix
        data_matrix: list[list[float]] = []
        for obs in self._historical_data:
            row = [_as_float(obs.get(f, 0.0)) for f in factor_names]
            data_matrix.append(row)

        data_matrix_arr = np.asarray(data_matrix, dtype=np.float64)

        # Calculate correlation matrix
        corr_matrix = np.corrcoef(data_matrix_arr, rowvar=False)

        # Convert to dict
        result: dict[str, dict[str, float]] = {}
        for i, name1 in enumerate(factor_names):
            result[name1] = {}
            for j, name2 in enumerate(factor_names):
                result[name1][name2] = float(corr_matrix[i, j])

        return result

    def identify_redundant_factors(self, threshold: float = 0.9) -> list[tuple[str, str]]:
        """
        Identify highly correlated (redundant) factor pairs.

        Args:
            threshold: Correlation threshold for redundancy

        Returns:
            List of (factor1, factor2) tuples that are redundant
        """
        corr_matrix = self.calculate_correlation_matrix()
        if not corr_matrix:
            return []

        redundant = []
        seen = set()

        for f1, correlations in corr_matrix.items():
            for f2, corr in correlations.items():
                if f1 != f2 and abs(corr) > threshold:
                    pair = tuple(sorted([f1, f2]))
                    if pair not in seen:
                        seen.add(pair)
                        redundant.append((f1, f2))

        return redundant

    def optimize_for_prediction(self, target_key: str = "_target") -> dict[str, float]:
        """
        Optimize factor weights to maximize predictive power.

        Uses regression to find weights that best predict target.

        Args:
            target_key: Key for target variable in observations

        Returns:
            Optimized factor weights
        """
        if len(self._historical_data) < 50:
            logger.warning("Insufficient data for optimization (need 50+)")
            return self._equal_weights()

        factor_names = [
            k for k in self._historical_data[0].keys() if not k.startswith("_") and k != target_key
        ]

        # Build matrices
        X: list[list[float]] = []
        y: list[float] = []
        for obs in self._historical_data:
            if target_key in obs:
                row = [_as_float(obs.get(f, 0.0)) for f in factor_names]
                X.append(row)
                y.append(_as_float(obs[target_key]))

        if len(X) < 50:
            return self._equal_weights()

        X_arr = np.asarray(X, dtype=np.float64)
        y_arr = np.asarray(y, dtype=np.float64)

        # Simple linear regression via least squares
        try:
            # Add bias term
            X_bias = np.column_stack([np.ones(len(X_arr)), X_arr])

            # Solve normal equations
            coeffs = np.linalg.lstsq(X_bias, y_arr, rcond=None)[0]

            # Use absolute coefficients as weights
            weights = np.abs(coeffs[1:])  # Exclude bias
            weights = weights / weights.sum()  # Normalize

            return {name: float(w) for name, w in zip(factor_names, weights, strict=False)}
        except Exception as e:
            logger.error(f"Optimization failed: {e}")
            return self._equal_weights()

    def _equal_weights(self) -> dict[str, float]:
        """Return equal weights for all factors."""
        # Get factor names from first observation
        if not self._historical_data:
            return {
                "momentum": 1 / 7,
                "strength": 1 / 7,
                "volatility": 1 / 7,
                "put_call": 1 / 7,
                "credit": 1 / 7,
                "safe_haven": 1 / 7,
                "event_risk": 1 / 7,
            }

        factor_names = [k for k in self._historical_data[0].keys() if not k.startswith("_")]

        weight = 1.0 / len(factor_names)
        return {name: weight for name in factor_names}

    def get_calibration_report(self) -> CalibrationReport:
        """
        Generate comprehensive calibration report.

        Returns:
            Dict with all calibration metrics and recommendations
        """
        report: CalibrationReport = {
            "timestamp": datetime.now().isoformat(),
            "observation_count": len(self._historical_data),
            "pca_weights": self.calculate_pca_weights(),
            "correlation_matrix": self.calculate_correlation_matrix(),
            "redundant_pairs": self.identify_redundant_factors(),
            "optimized_weights": self.optimize_for_prediction(),
            "equal_weights": self._equal_weights(),
            "recommendations": [],
        }

        # Generate recommendations
        if report["redundant_pairs"]:
            report["recommendations"].append(
                f"Remove {len(report['redundant_pairs'])} redundant factor pairs "
                f"(correlation > 0.9)"
            )

        # Check if PCA weights differ significantly from equal
        pca_w = report["pca_weights"]
        eq_w = report["equal_weights"]
        if pca_w and eq_w:
            max_diff = max(abs(pca_w.get(k, 0) - eq_w.get(k, 0)) for k in eq_w.keys())
            if max_diff > 0.05:
                report["recommendations"].append("PCA suggests non-equal weights may be beneficial")

        if len(self._historical_data) < 100:
            report["recommendations"].append(
                "Collect more data (currently < 100 observations) for better calibration"
            )

        return report

    def clear_data(self) -> None:
        """Clear historical data."""
        self._historical_data.clear()


# Global instance
_calibration_engine: FactorCalibrationEngine | None = None


def get_calibration_engine() -> FactorCalibrationEngine:
    """Get or create calibration engine instance."""
    global _calibration_engine
    if _calibration_engine is None:
        _calibration_engine = FactorCalibrationEngine()
    return _calibration_engine
