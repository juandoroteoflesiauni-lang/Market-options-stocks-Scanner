"""
backend/layer_3_specialists/ia_probabilistico/engines/correlation_analyzer.py
════════════════════════════════════════════════════════════════════════════════
Correlation Analyzer — Statistical analysis of Fear & Greed vs market returns.

Analyzes:
- Correlation between FG score and future SPY returns
- Predictive power of each factor
- Optimal holding period for FG signals
- Regime-dependent correlations
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)


class FMPClientLike(Protocol):
    async def get_historical_prices(
        self,
        symbol: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[object]: ...


@dataclass
class CorrelationAnalysis:
    """Result of correlation analysis."""

    fgspy_correlation: float  # Correlation with SPY returns
    factor_correlations: dict[str, float]  # Per-factor correlation
    predictive_power: dict[str, float]  # R² for each factor
    optimal_horizon: int  # Best holding period (days)
    sample_size: int


class CorrelationAnalyzer:
    """
    Analyzes correlation between Fear & Greed and market returns.
    """

    def __init__(self, fmp_client: FMPClientLike) -> None:
        """
        Initialize with FMP client.

        Args:
            fmp_client: FMPClient instance for fetching SPY data
        """
        self.fmp = fmp_client
        self._fg_history: list[float] = []
        self._spy_history: list[float] = []
        self._dates: list[datetime] = []

    def add_observation(self, date: datetime, fg_score: float, spy_price: float) -> None:
        """
        Add an observation for correlation analysis.

        Args:
            date: Observation date
            fg_score: Fear & Greed score
            spy_price: SPY price
        """
        self._fg_history.append(fg_score)
        self._spy_history.append(spy_price)
        self._dates.append(date)

    def analyze_correlation(
        self, horizon: int = 5, min_samples: int = 30  # Days
    ) -> CorrelationAnalysis | None:
        """
        Analyze correlation between FG and future SPY returns.

        Args:
            horizon: Holding period in days
            min_samples: Minimum samples required

        Returns:
            Correlation analysis or None if insufficient data
        """
        if len(self._fg_history) < min_samples:
            logger.warning(
                f"Insufficient data for correlation analysis ({len(self._fg_history)} < {min_samples})"
            )
            return None

        # Convert to numpy arrays
        fg_array = np.array(self._fg_history[:-horizon])  # FG at time t
        spy_array = np.array(self._spy_history)

        # Calculate future returns
        future_returns: list[float] = []
        for i in range(len(fg_array)):
            if i + horizon < len(spy_array):
                ret = (spy_array[i + horizon] - spy_array[i]) / spy_array[i]
                future_returns.append(ret)

        if len(future_returns) < min_samples:
            return None

        future_returns_arr = np.asarray(future_returns, dtype=np.float64)

        # Calculate correlation
        correlation = np.corrcoef(fg_array, future_returns_arr)[0, 1]

        # Factor correlations (if factor data available)
        factor_corr: dict[str, float] = {}

        return CorrelationAnalysis(
            fgspy_correlation=float(correlation),
            factor_correlations=factor_corr,
            predictive_power={},
            optimal_horizon=horizon,
            sample_size=len(future_returns_arr),
        )

    def find_optimal_horizon(self, horizons: list[int] | None = None) -> int:
        """
        Find optimal holding period for FG signals.

        Args:
            horizons: List of horizons to test

        Returns:
            Optimal horizon in days
        """
        if horizons is None:
            horizons = [1, 3, 5, 10, 20, 60]  # 1 day to 3 months

        best_horizon = 5
        best_correlation = 0.0

        for horizon in horizons:
            analysis = self.analyze_correlation(horizon=horizon)
            if analysis and abs(analysis.fgspy_correlation) > abs(best_correlation):
                best_correlation = analysis.fgspy_correlation
                best_horizon = horizon

        return best_horizon

    def get_factor_correlations(self) -> dict[str, float]:
        """
        Get correlation of each factor with future returns.

        Returns:
            Dict mapping factor names to correlations
        """
        # Placeholder - would need factor-level data
        return {}


# Global instance
_analyzer: CorrelationAnalyzer | None = None


def get_correlation_analyzer(fmp_client: FMPClientLike) -> CorrelationAnalyzer:
    """Get or create correlation analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = CorrelationAnalyzer(fmp_client)
    return _analyzer
