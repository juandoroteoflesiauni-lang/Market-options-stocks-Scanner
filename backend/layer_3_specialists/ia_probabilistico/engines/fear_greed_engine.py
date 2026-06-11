"""
backend/layer_3_specialists/ia_probabilistico/engines/fear_greed_engine.py
════════════════════════════════════════════════════════════════════════════════
Fear & Greed Engine — Multi-Factor Market Sentiment Index.

Inspired by CNN Money's Fear & Greed methodology (7 equal-weight buckets: momentum,
strength, breadth, put/call, VIX vs MA, credit, safe haven, plus our event_risk),
but using public FMP (and internal proxies when a series is missing—not a byte-for-byte
CNN reproduction). See ``market_data_fetcher`` for inputs (VIX, SPY, NYA/SPY breadth
proxy, optional FMP economic put/call, JNK/TLT, gold, DXY).

This engine computes a composite sentiment score from multiple market factors:
  1. Market Momentum (SPX vs moving average)
  2. Stock Price Strength (NYSE highs/lows)
  3. Market Volatility (VIX relative to average)
  4. Put-Call Ratio (options sentiment)
  5. Credit Spreads (junk bond demand proxy)
  6. Safe Haven Demand (gold, USD flows)
  7. Event Risk (NLP sentiment from news/transcripts)

Each factor is normalized to [0, 100] and combined with equal weights.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FearGreedResult:
    """Complete Fear & Greed analysis result."""

    score: float  # [0, 100]
    label: str  # Extreme Fear, Fear, Neutral, Greed, Extreme Greed
    factors: dict[str, float]  # Individual factor scores
    factor_weights: dict[str, float]  # Weights used
    timestamp: str
    data_quality: str  # "excellent", "good", "fair", "poor"


class FearGreedEngine:
    """
    Multi-factor Fear & Greed index calculator.

    Factors (equal-weighted by default):
    1. Market Momentum (SPX ROC vs 125-day MA)
    2. Stock Price Strength (% of stocks at 52-week highs)
    3. Market Volatility (VIX vs 50-day MA)
    4. Put-Call Ratio (options market sentiment)
    5. Credit Spreads (high-yield bond demand)
    6. Safe Haven Demand (gold/USD flows)
    7. Event Risk (NLP-based, our unique addition)
    """

    # Factor weights (can be customized)
    DEFAULT_WEIGHTS: dict[str, float] = {
        "momentum": 1 / 7,
        "strength": 1 / 7,
        "volatility": 1 / 7,
        "put_call": 1 / 7,
        "credit": 1 / 7,
        "safe_haven": 1 / 7,
        "event_risk": 1 / 7,
    }

    # Thresholds for labeling
    EXTREME_FEAR_THRESHOLD = 25
    FEAR_THRESHOLD = 45
    GREED_THRESHOLD = 55
    EXTREME_GREED_THRESHOLD = 75

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()

    async def compute(
        self,
        symbol: str = "SPY",
        market_data: dict[str, Any] | None = None,
        event_risk_score: float | None = None,
        **kwargs: Any,
    ) -> FearGreedResult:
        """
        Compute Fear & Greed index from available market data.

        Args:
            symbol: Primary symbol to analyze (default SPY)
            market_data: Dict containing:
                - spx_price: Current S&P 500 price
                - spx_ma125: 125-day moving average
                - nyse_highs_pct: % of NYSE stocks at 52-week highs
                - vix_current: Current VIX value
                - vix_ma50: 50-day MA of VIX
                - put_call_ratio: CBOE put-call ratio
                - credit_spread: High-yield spread over Treasuries
                - gold_price: Gold price (optional)
                - usd_index: DXY value (optional)
            event_risk_score: Pre-computed NLP event risk [0, 1]

        Returns:
            FearGreedResult with composite score and factor breakdown
        """
        factors: dict[str, float] = {}
        data_quality_score = 0.0
        total_weight = 0.0

        # 1. Market Momentum Factor
        if market_data and "spx_price" in market_data and "spx_ma125" in market_data:
            momentum_score = self._compute_momentum(
                market_data["spx_price"], market_data["spx_ma125"]
            )
            factors["momentum"] = momentum_score
            data_quality_score += self.weights["momentum"]
            total_weight += self.weights["momentum"]
        else:
            # Fallback: use neutral value
            factors["momentum"] = 50.0

        # 2. Stock Price Strength Factor
        if market_data and "nyse_highs_pct" in market_data:
            strength_score = self._compute_strength(market_data["nyse_highs_pct"])
            factors["strength"] = strength_score
            data_quality_score += self.weights["strength"]
            total_weight += self.weights["strength"]
        else:
            factors["strength"] = 50.0

        # 3. Market Volatility Factor
        if market_data and "vix_current" in market_data and "vix_ma50" in market_data:
            vol_score = self._compute_volatility(
                market_data["vix_current"], market_data["vix_ma50"]
            )
            factors["volatility"] = vol_score
            data_quality_score += self.weights["volatility"]
            total_weight += self.weights["volatility"]
        else:
            factors["volatility"] = 50.0

        # 4. Put-Call Ratio Factor
        if market_data and "put_call_ratio" in market_data:
            pc_score = self._compute_put_call(market_data["put_call_ratio"])
            factors["put_call"] = pc_score
            data_quality_score += self.weights["put_call"]
            total_weight += self.weights["put_call"]
        else:
            factors["put_call"] = 50.0

        # 5. Credit Spreads Factor
        if market_data and "credit_spread" in market_data:
            credit_score = self._compute_credit(market_data["credit_spread"])
            factors["credit"] = credit_score
            data_quality_score += self.weights["credit"]
            total_weight += self.weights["credit"]
        else:
            factors["credit"] = 50.0

        # 6. Safe Haven Demand Factor
        if market_data and ("gold_price" in market_data or "usd_index" in market_data):
            safe_haven_score = self._compute_safe_haven(market_data)
            factors["safe_haven"] = safe_haven_score
            data_quality_score += self.weights["safe_haven"]
            total_weight += self.weights["safe_haven"]
        else:
            factors["safe_haven"] = 50.0

        # 7. Event Risk Factor (NLP-based)
        if event_risk_score is not None:
            # Invert: high event risk -> low fear/greed score
            event_score = 100.0 * (1.0 - event_risk_score)
            factors["event_risk"] = event_score
            data_quality_score += self.weights["event_risk"]
            total_weight += self.weights["event_risk"]
        else:
            factors["event_risk"] = 50.0

        # Compute weighted average
        composite_score = 0.0
        for factor_name, factor_value in factors.items():
            weight = self.weights.get(factor_name, 0)
            composite_score += factor_value * weight

        # Normalize by actual weight used
        if total_weight > 0:
            composite_score = (
                composite_score / total_weight * (total_weight / sum(self.weights.values()))
            )

        # Determine label
        label = self._score_to_label(composite_score)

        # Determine data quality
        quality_ratio = data_quality_score / sum(self.weights.values())
        if quality_ratio >= 0.85:
            data_quality = "excellent"
        elif quality_ratio >= 0.65:
            data_quality = "good"
        elif quality_ratio >= 0.40:
            data_quality = "fair"
        else:
            data_quality = "poor"

        return FearGreedResult(
            score=round(composite_score, 2),
            label=label,
            factors=factors,
            factor_weights=self.weights.copy(),
            timestamp=datetime.now().isoformat(),
            data_quality=data_quality,
        )

    def _compute_momentum(self, spx_price: float, spx_ma125: float) -> float:
        """
        Factor 1: Market Momentum
        Score based on how far SPX is above/below 125-day MA
        """
        if spx_ma125 == 0:
            return 50.0

        pct_above = (spx_price - spx_ma125) / spx_ma125 * 100

        # Map percentage to score [0, 100]
        # -10% -> 0, 0% -> 50, +10% -> 100
        score = 50.0 + pct_above * 5
        return max(0.0, min(100.0, score))

    def _compute_strength(self, nyse_highs_pct: float) -> float:
        """
        Factor 2: Stock Price Strength
        % of NYSE stocks at 52-week highs
        """
        # Typical range: 2% (extreme fear) to 15% (extreme greed)
        # Map to score: 2% -> 0, 8.5% -> 50, 15% -> 100
        score = (nyse_highs_pct - 2.0) / 13.0 * 100
        return max(0.0, min(100.0, score))

    def _compute_volatility(self, vix_current: float, vix_ma50: float) -> float:
        """
        Factor 3: Market Volatility
        VIX relative to 50-day average
        """
        if vix_ma50 == 0:
            return 50.0

        # Ratio > 1 means VIX above average (fear)
        ratio = vix_current / vix_ma50

        # ratio 0.5 -> 100, 1.0 -> 50, 2.0 -> 0
        score = 100.0 - (ratio - 0.5) / 1.5 * 100
        return max(0.0, min(100.0, score))

    def _compute_put_call(self, put_call_ratio: float) -> float:
        """
        Factor 4: Put-Call Ratio
        Typical range: 0.5 (greed) to 1.5 (fear)
        """
        # Invert: high put/call = fear
        # 0.5 -> 100, 1.0 -> 50, 1.5 -> 0
        score = 100.0 - (put_call_ratio - 0.5) * 100
        return max(0.0, min(100.0, score))

    def _compute_credit(self, credit_spread: float) -> float:
        """
        Factor 5: Credit Spreads
        High-yield spread over Treasuries
        Typical range: 200bp (greed) to 800bp (fear)
        """
        # 200bp -> 100, 500bp -> 50, 800bp -> 0
        score = 100.0 - (credit_spread - 200) / 600 * 100
        return max(0.0, min(100.0, score))

    def _compute_safe_haven(self, market_data: dict[str, Any]) -> float:
        """
        Factor 6: Safe Haven Demand
        Based on gold and USD flows
        """
        gold_score = 50.0
        usd_score = 50.0
        weight = 0.0

        if "gold_price" in market_data and "gold_ma50" in market_data:
            gold_ratio = market_data["gold_price"] / market_data["gold_ma50"]
            # Gold above MA = safe haven demand = fear
            gold_score = 100.0 - (gold_ratio - 0.9) * 200
            weight += 0.5

        if "usd_index" in market_data and "usd_ma50" in market_data:
            usd_ratio = market_data["usd_index"] / market_data["usd_ma50"]
            # USD above MA = safe haven demand = fear
            usd_score = 100.0 - (usd_ratio - 0.9) * 200
            weight += 0.5

        if weight > 0:
            return (gold_score + usd_score) / (2 * weight)
        return 50.0

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


# Convenience function for quick access
async def compute_fear_greed(
    symbol: str = "SPY",
    market_data: dict[str, Any] | None = None,
    event_risk_score: float | None = None,
) -> FearGreedResult:
    """Quick access function for Fear & Greed computation."""
    engine = FearGreedEngine()
    return await engine.compute(symbol, market_data, event_risk_score)
