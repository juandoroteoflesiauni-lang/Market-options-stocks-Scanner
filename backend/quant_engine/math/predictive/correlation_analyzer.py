from __future__ import annotations
"""
backend/engine/metrics/correlation_analyzer.py
Sector: Quantitative Engine / Correlation Analyzer
[ARCH-1, PD-4]

Theoretical basis:
    Analyzes the statistical correlation and predictive power of Fear & Greed
    sentiment indicators relative to asset returns across varying time horizons.
"""


import logging

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.correlation_analyzer")

type FloatArray = npt.NDArray[np.float64]


class CorrelationAnalysis(BaseModel):
    """Immutable result of correlation analysis."""

    model_config = ConfigDict(frozen=True)

    fgspy_correlation: float
    factor_correlations: dict[str, float]
    predictive_power: dict[str, float]
    optimal_horizon: int
    sample_size: int


class CorrelationAnalyzer:
    """
    Analyzes correlation between Fear & Greed and market returns.
    Purely stateless and vectorized.
    """

    def analyze_correlation(
        self,
        data: FloatArray,
        horizon: int = 5,
        min_samples: int = 30,
    ) -> Result[CorrelationAnalysis]:
        """
        Analyzes the correlation between FG scores and future asset returns.

        Parameters
        ----------
        data : FloatArray
            2D NumPy array with shape (N, 2) where:
            0 = fg_score
            1 = asset_price
        horizon : int
            Holding period horizon in days/samples.
        min_samples : int
            Minimum number of samples required to run the analysis.

        Returns
        -------
        Result[CorrelationAnalysis]
            The CorrelationAnalysis report wrapped in a Result monad.
        """
        try:
            # 1. Validations
            if not isinstance(data, np.ndarray):
                return Result.failure(reason="data must be a numpy ndarray")

            if data.ndim != 2 or data.shape[1] != 2:
                return Result.failure(
                    reason=f"data must be a 2D array of shape (N, 2), got shape {data.shape}"
                )

            if np.any(np.isnan(data)):
                return Result.failure(reason="data contains NaN values")

            fg_scores = data[:, 0]
            prices = data[:, 1]

            if np.any(prices <= 0.0):
                return Result.failure(reason="asset_price cannot contain zero or negative values")

            n = len(data)
            if n < min_samples + horizon:
                return Result.failure(
                    reason=(
                        f"Insufficient data samples ({n} < required "
                        f"{min_samples + horizon} [min_samples={min_samples} + horizon={horizon}])"
                    )
                )

            # 2. Vectorized returns using slicing
            future_returns = (prices[horizon:] - prices[:-horizon]) / prices[:-horizon]
            fg_aligned = fg_scores[:-horizon]

            # 3. Calculate correlation
            corr_matrix = np.corrcoef(fg_aligned, future_returns)
            correlation = float(corr_matrix[0, 1])

            # If variance is zero (e.g. constant prices or constant scores), corrcoef returns NaN
            if np.isnan(correlation):
                correlation = 0.0

            # 4. Build report
            report = CorrelationAnalysis(
                fgspy_correlation=correlation,
                factor_correlations={},
                predictive_power={},
                optimal_horizon=horizon,
                sample_size=len(future_returns),
            )
            return Result.success(report)

        except Exception as e:
            logger.error("Correlation analysis failed: %s", e)
            return Result.failure(reason=f"Correlation analysis failed: {e}")

    def find_optimal_horizon(
        self,
        data: FloatArray,
        horizons: list[int] | None = None,
        min_samples: int = 30,
    ) -> Result[int]:
        """
        Finds the optimal holding period horizon maximizing correlation absolute value.

        Parameters
        ----------
        data : FloatArray
            2D NumPy array with shape (N, 2) of (fg_score, asset_price).
        horizons : list[int], optional
            Time horizons to test.
        min_samples : int
            Minimum number of samples required for analysis.

        Returns
        -------
        Result[int]
            The optimal horizon wrapped in a Result monad.
        """
        try:
            if horizons is None:
                horizons = [1, 3, 5, 10, 20, 60]

            best_horizon = horizons[0] if horizons else 5
            best_correlation = 0.0
            found_any = False

            for horizon in horizons:
                res = self.analyze_correlation(data, horizon=horizon, min_samples=min_samples)
                if res.is_success:
                    found_any = True
                    corr = res.unwrap().fgspy_correlation
                    if abs(corr) > abs(best_correlation):
                        best_correlation = corr
                        best_horizon = horizon

            if not found_any:
                return Result.failure(
                    reason="Could not calculate correlation for any of the tested horizons"
                )

            return Result.success(best_horizon)

        except Exception as e:
            logger.error("Find optimal horizon failed: %s", e)
            return Result.failure(reason=f"Find optimal horizon failed: {e}")


def get_correlation_analysis(
    data: FloatArray,
    horizon: int = 5,
    min_samples: int = 30,
) -> Result[CorrelationAnalysis]:
    """Stateless entry point for correlation analysis."""
    analyzer = CorrelationAnalyzer()
    return analyzer.analyze_correlation(data, horizon, min_samples)
