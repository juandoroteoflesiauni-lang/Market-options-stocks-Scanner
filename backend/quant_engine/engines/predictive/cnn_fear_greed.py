from __future__ import annotations
from typing import Any
"""
backend/engine/metrics/cnn_fear_greed.py
Sector: Options / CNN Fear & Greed Index Fetcher & Comparator
[ARCH-1, PD-4]

Theoretical basis:
    Approximates the CNN Fear & Greed Index logic using market inputs (SPY and VIX)
    and compares it with our multi-factor index.
    Purely stateless, synchronous, and offline.
"""


import logging

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.cnn_fear_greed")

type FloatArray = npt.NDArray[np.float64]


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class AlternativeFGReport(BaseModel):
    """Fear & Greed Index approximation report."""

    model_config = ConfigDict(frozen=True)

    score: float
    label: str
    factors: dict[str, float]
    source: str = "approximated"
    factor_count: int


class ComparisonReport(BaseModel):
    """Comparison report between CNN Fear & Greed Index and our score."""

    model_config = ConfigDict(frozen=True)

    available: bool
    message: str | None = None
    cnn_score: float | None = None
    cnn_label: str | None = None
    our_score: float
    our_label: str
    difference: float | None = None
    discrepancy_pct: float | None = None
    agreement: str | None = None


# ── Fetcher & Comparator ─────────────────────────────────────────────────────────


class CNNFearGreedFetcher:
    """Fetcher comparator for CNN Fear & Greed Index."""

    def __init__(self) -> None:
        pass

    def compare_with_ours(
        self,
        cnn_data: dict[str, Any] | None,
        our_score: float,
        our_factors: dict[str, float],
    ) -> Result[ComparisonReport]:
        """Compare CNN Fear & Greed with our multi-factor calculation."""
        if not cnn_data:
            return Result.success(
                ComparisonReport(
                    available=False,
                    message="CNN data not available - using our multi-factor as primary",
                    our_score=our_score,
                    our_label=self._score_to_label(our_score),
                )
            )

        try:
            cnn_score = float(cnn_data.get("score", 50.0))
            difference = our_score - cnn_score

            discrepancy_pct = abs(difference) / cnn_score * 100.0 if cnn_score > 0.0 else 0.0
            abs_diff = abs(difference)
            agreement = "high" if abs_diff < 5.0 else "medium" if abs_diff < 10.0 else "low"

            return Result.success(
                ComparisonReport(
                    available=True,
                    cnn_score=cnn_score,
                    cnn_label=str(cnn_data.get("label", "Neutral")),
                    our_score=our_score,
                    our_label=self._score_to_label(our_score),
                    difference=round(difference, 4),
                    discrepancy_pct=round(discrepancy_pct, 4),
                    agreement=agreement,
                )
            )
        except Exception as e:
            logger.error(f"Failed to compare indices: {e}")
            return Result.failure(reason=f"Failed to compare indices: {e}")

    def _score_to_label(self, score: float) -> str:
        """Convert score to CNN-style label."""
        if score <= 25.0:
            return "Extreme Fear"
        elif score <= 45.0:
            return "Fear"
        elif score <= 55.0:
            return "Neutral"
        elif score <= 75.0:
            return "Greed"
        else:
            return "Extreme Greed"


# ── Alternative Fear & Greed Approximation ───────────────────────────────────────


class AlternativeFearGreedSource:
    """Alternative Fear & Greed source using NumPy vectors for calculation."""

    def __init__(self) -> None:
        pass

    def calculate_approximate_fg(
        self, spy_prices: FloatArray, vix_prices: FloatArray
    ) -> Result[AlternativeFGReport]:
        """Calculates the approximate Fear & Greed score.

        Uses vectorized SPY and VIX pricing arrays.
        """
        if spy_prices is None or vix_prices is None:
            return Result.failure(reason="spy_prices and vix_prices must not be None")

        if len(spy_prices) < 125:
            return Result.failure(
                reason=f"spy_prices must have at least 125 elements; got {len(spy_prices)}"
            )

        if len(vix_prices) < 50:
            return Result.failure(
                reason=f"vix_prices must have at least 50 elements; got {len(vix_prices)}"
            )

        try:
            factors = {}

            # 1. Market Momentum (SPY vs MA125)
            current_spy = spy_prices[0]
            ma125_spy = np.mean(spy_prices[:125])
            if ma125_spy <= 0.0:
                return Result.failure(reason="spy_prices 125-day moving average must be positive")

            momentum_score = 50.0 + ((current_spy - ma125_spy) / ma125_spy * 100.0) * 5.0
            factors["momentum"] = float(max(0.0, min(100.0, momentum_score)))

            # 2. Volatility (VIX vs MA50)
            current_vix = vix_prices[0]
            ma50_vix = np.mean(vix_prices[:50])
            if ma50_vix <= 0.0:
                return Result.failure(reason="vix_prices 50-day moving average must be positive")

            # VIX below MA = greed, above = fear
            vol_score = 50.0 + ((ma50_vix - current_vix) / ma50_vix * 100.0) * 3.0
            factors["volatility"] = float(max(0.0, min(100.0, vol_score)))

            composite = sum(factors.values()) / len(factors)

            return Result.success(
                AlternativeFGReport(
                    score=round(composite, 1),
                    label=self._score_to_label(composite),
                    factors=factors,
                    source="approximated",
                    factor_count=len(factors),
                )
            )
        except Exception as e:
            logger.error(f"Failed to calculate approximate Fear & Greed: {e}")
            return Result.failure(reason=f"Failed to calculate approximate Fear & Greed: {e}")

    def _score_to_label(self, score: float) -> str:
        """Convert score to label."""
        if score <= 25.0:
            return "Extreme Fear"
        elif score <= 45.0:
            return "Fear"
        elif score <= 55.0:
            return "Neutral"
        elif score <= 75.0:
            return "Greed"
        else:
            return "Extreme Greed"
