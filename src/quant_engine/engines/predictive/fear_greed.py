"""
backend/engine/metrics/fear_greed.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Fear & Greed Engine — Multi-Factor Market Sentiment Index.
Stateless and synchronous implementation.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.fear_greed")


class MarketSentimentInput(BaseModel):
    """Strict contract input for Fear & Greed sentiment calculation."""

    model_config = ConfigDict(frozen=True)

    spx_price: float = Field(..., description="Current S&P 500 price")
    spx_ma125: float = Field(..., description="125-day moving average of S&P 500")
    nyse_highs_pct: float = Field(..., description="% of NYSE stocks at 52-week highs")
    vix_current: float = Field(..., description="Current VIX value")
    vix_ma50: float = Field(..., description="50-day moving average of VIX")
    put_call_ratio: float = Field(..., description="CBOE Put/Call Ratio")
    credit_spread: float = Field(..., description="High-yield spread over Treasuries (in bps)")

    gold_price: float | None = Field(default=None, description="Current Gold price")
    gold_ma50: float | None = Field(default=None, description="50-day moving average of Gold")
    usd_index: float | None = Field(default=None, description="Current USD Index (DXY)")
    usd_ma50: float | None = Field(default=None, description="50-day moving average of USD Index")
    event_risk_score: float | None = Field(
        default=None, description="NLP-based news event risk score [0, 1]"
    )


class FearGreedResult(BaseModel):
    """Immutable sentiment analysis result."""

    model_config = ConfigDict(frozen=True)

    score: float  # [0, 100]
    label: str  # Extreme Fear, Fear, Neutral, Greed, Extreme Greed
    factors: dict[str, float]  # Individual factor scores
    factor_weights: dict[str, float]  # Weights used
    timestamp: str
    data_quality: str  # "excellent", "good", "fair", "poor"


class FearGreedEngine:
    """
    Multi-factor Fear & Greed index calculator.
    Purely stateless and synchronous.
    """

    DEFAULT_WEIGHTS: ClassVar[dict[str, float]] = {
        "momentum": 1 / 7,
        "strength": 1 / 7,
        "volatility": 1 / 7,
        "put_call": 1 / 7,
        "credit": 1 / 7,
        "safe_haven": 1 / 7,
        "event_risk": 1 / 7,
    }

    EXTREME_FEAR_THRESHOLD = 25
    FEAR_THRESHOLD = 45
    GREED_THRESHOLD = 55
    EXTREME_GREED_THRESHOLD = 75

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()

    def analyze(
        self,
        symbol: str,
        sentiment_input: MarketSentimentInput,
    ) -> Result[FearGreedResult]:
        """
        Compute Fear & Greed index from sentiment inputs.
        """
        try:
            # 1. Validation
            # Check for NaN values
            if (
                math.isnan(sentiment_input.spx_price)
                or math.isnan(sentiment_input.spx_ma125)
                or math.isnan(sentiment_input.nyse_highs_pct)
                or math.isnan(sentiment_input.vix_current)
                or math.isnan(sentiment_input.vix_ma50)
                or math.isnan(sentiment_input.put_call_ratio)
                or math.isnan(sentiment_input.credit_spread)
            ):
                return Result.failure(reason="Market sentiment input contains NaN values")

            # Check for invalid values
            if sentiment_input.spx_price <= 0.0 or sentiment_input.spx_ma125 <= 0.0:
                return Result.failure(reason="S&P 500 price and 125-day MA must be positive")

            if sentiment_input.nyse_highs_pct < 0.0:
                return Result.failure(reason="NYSE highs percentage cannot be negative")

            if sentiment_input.vix_current <= 0.0 or sentiment_input.vix_ma50 <= 0.0:
                return Result.failure(reason="VIX price and 50-day MA must be positive")

            if sentiment_input.put_call_ratio <= 0.0:
                return Result.failure(reason="Put/Call ratio must be positive")

            if sentiment_input.credit_spread < 0.0:
                return Result.failure(reason="Credit spread cannot be negative")

            # Validate optional safe haven inputs
            has_gold = (
                sentiment_input.gold_price is not None or sentiment_input.gold_ma50 is not None
            )
            if has_gold:
                if sentiment_input.gold_price is None or sentiment_input.gold_ma50 is None:
                    return Result.failure(
                        reason="Both gold_price and gold_ma50 must be provided if either is set"
                    )
                if math.isnan(sentiment_input.gold_price) or math.isnan(sentiment_input.gold_ma50):
                    return Result.failure(reason="Gold price or MA contains NaN values")
                if sentiment_input.gold_price <= 0.0 or sentiment_input.gold_ma50 <= 0.0:
                    return Result.failure(reason="Gold price and MA must be positive")

            has_usd = sentiment_input.usd_index is not None or sentiment_input.usd_ma50 is not None
            if has_usd:
                if sentiment_input.usd_index is None or sentiment_input.usd_ma50 is None:
                    return Result.failure(
                        reason="Both usd_index and usd_ma50 must be provided if either is set"
                    )
                if math.isnan(sentiment_input.usd_index) or math.isnan(sentiment_input.usd_ma50):
                    return Result.failure(reason="USD Index or MA contains NaN values")
                if sentiment_input.usd_index <= 0.0 or sentiment_input.usd_ma50 <= 0.0:
                    return Result.failure(reason="USD Index and MA must be positive")

            # Validate event risk NLP score
            if sentiment_input.event_risk_score is not None:
                if math.isnan(sentiment_input.event_risk_score):
                    return Result.failure(reason="event_risk_score contains NaN values")
                if not (0.0 <= sentiment_input.event_risk_score <= 1.0):
                    return Result.failure(reason="event_risk_score must be between 0.0 and 1.0")

            factors: dict[str, float] = {}
            data_quality_score = 0.0
            total_weight = 0.0

            # 2. Computations
            # Momentum
            momentum_score = self._compute_momentum(
                sentiment_input.spx_price, sentiment_input.spx_ma125
            )
            factors["momentum"] = momentum_score
            data_quality_score += self.weights["momentum"]
            total_weight += self.weights["momentum"]

            # Strength
            strength_score = self._compute_strength(sentiment_input.nyse_highs_pct)
            factors["strength"] = strength_score
            data_quality_score += self.weights["strength"]
            total_weight += self.weights["strength"]

            # Volatility
            vol_score = self._compute_volatility(
                sentiment_input.vix_current, sentiment_input.vix_ma50
            )
            factors["volatility"] = vol_score
            data_quality_score += self.weights["volatility"]
            total_weight += self.weights["volatility"]

            # Put-Call Ratio
            pc_score = self._compute_put_call(sentiment_input.put_call_ratio)
            factors["put_call"] = pc_score
            data_quality_score += self.weights["put_call"]
            total_weight += self.weights["put_call"]

            # Credit Spread
            credit_score = self._compute_credit(sentiment_input.credit_spread)
            factors["credit"] = credit_score
            data_quality_score += self.weights["credit"]
            total_weight += self.weights["credit"]

            # Safe Haven
            safe_haven_score = self._compute_safe_haven(sentiment_input)
            if safe_haven_score is not None:
                factors["safe_haven"] = safe_haven_score
                data_quality_score += self.weights["safe_haven"]
                total_weight += self.weights["safe_haven"]
            else:
                factors["safe_haven"] = 50.0

            # Event Risk
            if sentiment_input.event_risk_score is not None:
                event_score = 100.0 * (1.0 - sentiment_input.event_risk_score)
                factors["event_risk"] = event_score
                data_quality_score += self.weights["event_risk"]
                total_weight += self.weights["event_risk"]
            else:
                factors["event_risk"] = 50.0

            # Compute weighted average
            composite_score = 0.0
            for factor_name, factor_value in factors.items():
                weight = self.weights.get(factor_name, 0.0)
                composite_score += factor_value * weight

            # Normalize by actual weight used
            weights_sum = sum(self.weights.values())
            if total_weight > 0.0 and weights_sum > 0.0:
                composite_score = (composite_score / total_weight) * (total_weight / weights_sum)

            label = self._score_to_label(composite_score)

            # Quality ratio
            quality_ratio = data_quality_score / weights_sum if weights_sum > 0.0 else 0.0

            if quality_ratio >= 0.85:
                data_quality = "excellent"
            elif quality_ratio >= 0.65:
                data_quality = "good"
            elif quality_ratio >= 0.40:
                data_quality = "fair"
            else:
                data_quality = "poor"

            result = FearGreedResult(
                score=round(composite_score, 2),
                label=label,
                factors=factors,
                factor_weights=self.weights.copy(),
                timestamp=datetime.now(tz=UTC).isoformat(),
                data_quality=data_quality,
            )
            return Result.success(result)

        except Exception as e:
            logger.error("FearGreed engine analysis failed: %s", e)
            return Result.failure(reason=f"FearGreed engine analysis failed: {e}")

    def _compute_momentum(self, spx_price: float, spx_ma125: float) -> float:
        """Score based on how far SPX is above/below 125-day MA."""
        pct_above = (spx_price - spx_ma125) / spx_ma125 * 100
        # Map percentage to score [0, 100]
        # -10% -> 0, 0% -> 50, +10% -> 100
        score = 50.0 + pct_above * 5.0
        return max(0.0, min(100.0, score))

    def _compute_strength(self, nyse_highs_pct: float) -> float:
        """% of NYSE stocks at 52-week highs."""
        # Typical range: 2% (extreme fear) to 15% (extreme greed)
        # Map to score: 2% -> 0, 8.5% -> 50, 15% -> 100
        score = (nyse_highs_pct - 2.0) / 13.0 * 100.0
        return max(0.0, min(100.0, score))

    def _compute_volatility(self, vix_current: float, vix_ma50: float) -> float:
        """VIX relative to 50-day average."""
        ratio = vix_current / vix_ma50
        # ratio 0.5 -> 100, 1.0 -> 50, 2.0 -> 0
        score = 100.0 - (ratio - 0.5) / 1.5 * 100.0
        return max(0.0, min(100.0, score))

    def _compute_put_call(self, put_call_ratio: float) -> float:
        """Options market sentiment from Put/Call ratio."""
        # Invert: high put/call = fear
        # 0.5 -> 100, 1.0 -> 50, 1.5 -> 0
        score = 100.0 - (put_call_ratio - 0.5) * 100.0
        return max(0.0, min(100.0, score))

    def _compute_credit(self, credit_spread: float) -> float:
        """Credit spread (in bps)."""
        # 200bp -> 100, 500bp -> 50, 800bp -> 0
        score = 100.0 - (credit_spread - 200.0) / 600.0 * 100.0
        return max(0.0, min(100.0, score))

    def _compute_safe_haven(self, sentiment_input: MarketSentimentInput) -> float | None:
        """Based on gold and USD index relative to their moving averages."""
        gold_score = None
        usd_score = None

        if sentiment_input.gold_price is not None and sentiment_input.gold_ma50 is not None:
            gold_ratio = sentiment_input.gold_price / sentiment_input.gold_ma50
            # Gold above MA = safe haven demand = fear
            gold_score = 100.0 - (gold_ratio - 0.9) * 200.0
            gold_score = max(0.0, min(100.0, gold_score))

        if sentiment_input.usd_index is not None and sentiment_input.usd_ma50 is not None:
            usd_ratio = sentiment_input.usd_index / sentiment_input.usd_ma50
            # USD above MA = safe haven demand = fear
            usd_score = 100.0 - (usd_ratio - 0.9) * 200.0
            usd_score = max(0.0, min(100.0, usd_score))

        if gold_score is not None and usd_score is not None:
            return (gold_score + usd_score) / 2.0
        elif gold_score is not None:
            return gold_score
        elif usd_score is not None:
            return usd_score
        return None

    def _score_to_label(self, score: float) -> str:
        """Convert numeric score to human-readable label."""
        if score <= self.EXTREME_FEAR_THRESHOLD:
            return "Extreme Fear"
        elif score <= self.FEAR_THRESHOLD:
            return "Fear"
        elif score <= self.GREED_THRESHOLD:
            return "Neutral"
        elif score <= self.EXTREME_GREED_THRESHOLD:
            return "Greed"
        else:
            return "Extreme Greed"
