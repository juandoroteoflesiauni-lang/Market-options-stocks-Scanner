"""
backend/layer_3_specialists/ia_probabilistico/engines/cnn_fear_greed.py
════════════════════════════════════════════════════════════════════════════════
CNN Fear & Greed Index Fetcher & Comparator.

Fetches CNN Fear & Greed Index data for comparison with our multi-factor index.
Enables backtesting and correlation analysis.

CNN Fear & Greed Factors:
1. Market Momentum (SPX vs 125-day MA) - 16.67%
2. Stock Price Strength (NYSE highs) - 16.67%
3. Market Volatility (VIX) - 16.67%
4. Put-Call Ratio - 16.67%
5. Junk Bond Demand - 16.67%
6. Market Breadth - 16.67%
7. Safe Haven Demand - 16.67%
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import aiohttp

logger = logging.getLogger(__name__)


class FMPClientLike(Protocol):
    async def get_historical_prices(
        self,
        symbol: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[Any]:
        ...


@dataclass
class CNNFearGreedReading:
    """CNN Fear & Greed reading."""
    date: datetime
    score: float
    label: str
    factors: dict[str, float]


class CNNFearGreedFetcher:
    """
    Fetches CNN Fear & Greed Index data.

    Note: CNN doesn't provide an official API. This uses web scraping
    or alternative data sources where available.
    """

    CNN_FEAR_GREED_URL = "https://money.cnn.com/data/fear-and-greed/"

    def __init__(self) -> None:
        self._cache: dict[str, tuple[dict[str, Any] | None, datetime]] = {}
        self._cache_ttl = timedelta(hours=1)

    async def fetch_current(self) -> dict[str, Any] | None:
        """
        Fetch current CNN Fear & Greed Index.

        Returns:
            Dict with score, label, and factors if available
        """
        # Check cache first
        cache_key = "current"
        now = datetime.now()

        if cache_key in self._cache:
            cached_data, cache_time = self._cache[cache_key]
            if now - cache_time < self._cache_ttl:
                return cached_data

        try:
            # CNN doesn't provide official API
            # This would require web scraping which is beyond scope
            # Return None to indicate unavailability
            logger.warning("CNN Fear & Greed requires web scraping - use alternative source")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch CNN Fear & Greed: {e}")
            return None

    async def fetch_historical(self, days: int = 30) -> list[dict[str, Any]]:
        """
        Fetch historical CNN Fear & Greed data.

        Args:
            days: Number of days of history

        Returns:
            List of historical readings
        """
        # Historical data would require scraping or paid API
        logger.warning("CNN historical data requires paid API access")
        return []

    def compare_with_ours(
        self,
        cnn_data: dict[str, Any] | None,
        our_score: float,
        our_factors: dict[str, float],
    ) -> dict[str, Any]:
        """
        Compare CNN Fear & Greed with our calculation.

        Args:
            cnn_data: CNN data (if available)
            our_score: Our FG score
            our_factors: Our factor breakdown

        Returns:
            Comparison metrics
        """
        if not cnn_data:
            return {
                "available": False,
                "message": "CNN data not available - using our multi-factor as primary"
            }

        cnn_score = cnn_data.get("score", 50)
        difference = our_score - cnn_score

        return {
            "available": True,
            "cnn_score": cnn_score,
            "cnn_label": cnn_data.get("label", "Neutral"),
            "our_score": our_score,
            "our_label": self._score_to_label(our_score),
            "difference": difference,
            "discrepancy_pct": abs(difference) / cnn_score * 100 if cnn_score > 0 else 0,
            "agreement": "high" if abs(difference) < 5 else "medium" if abs(difference) < 10 else "low",
        }

    def _score_to_label(self, score: float) -> str:
        """Convert score to CNN-style label."""
        if score <= 25:
            return "Extreme Fear"
        elif score <= 45:
            return "Fear"
        elif score <= 55:
            return "Neutral"
        elif score <= 75:
            return "Greed"
        else:
            return "Extreme Greed"


class AlternativeFearGreedSource:
    """
    Alternative Fear & Greed sources when CNN is unavailable.

    Uses market data to approximate CNN methodology:
    - Market momentum (SPY vs MA)
    - Volatility (VIX)
    - Market breadth (advance/decline)
    - Put-call ratios
    - Credit spreads
    """

    def __init__(self, fmp_client: FMPClientLike) -> None:
        """
        Initialize with FMP client.

        Args:
            fmp_client: FMPClient instance
        """
        self.fmp = fmp_client

    async def calculate_approximate_fg(self) -> dict[str, Any]:
        """
        Calculate Fear & Greed approximation using available data.

        Returns:
            Approximate FG score and factors
        """
        factors = {}

        # 1. Market Momentum (SPY vs MA125)
        try:
            spy_hist = await self.fmp.get_historical_prices(
                "SPY",
                date_from=(datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d"),
                date_to=datetime.now().strftime("%Y-%m-%d")
            )
            if spy_hist:
                prices = [p.close for p in spy_hist if p.close is not None]
                if len(prices) > 125:
                    current = prices[0]
                    ma125 = sum(prices[:125]) / 125
                    momentum_score = 50 + ((current - ma125) / ma125 * 100) * 5
                    factors["momentum"] = max(0, min(100, momentum_score))
        except Exception as e:
            logger.debug(f"Momentum fetch failed: {e}")

        # 2. Volatility (VIX)
        try:
            vix_hist = await self.fmp.get_historical_prices(
                "^VIX",
                date_from=(datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d"),
                date_to=datetime.now().strftime("%Y-%m-%d")
            )
            if vix_hist:
                prices = [p.close for p in vix_hist if p.close is not None]
                if len(prices) > 50:
                    current_vix = prices[0]
                    ma50 = sum(prices[:50]) / 50
                    # VIX below MA = greed, above = fear
                    vol_score = 50 + ((ma50 - current_vix) / ma50 * 100) * 3
                    factors["volatility"] = max(0, min(100, vol_score))
        except Exception as e:
            logger.debug(f"Volatility fetch failed: {e}")

        # Calculate composite if we have factors
        if factors:
            composite = sum(factors.values()) / len(factors)
            return {
                "score": round(composite, 1),
                "label": self._score_to_label(composite),
                "factors": factors,
                "source": "approximated",
                "factor_count": len(factors),
            }

        return {
            "score": 50.0,
            "label": "Neutral",
            "factors": {},
            "source": "default",
            "factor_count": 0,
        }

    def _score_to_label(self, score: float) -> str:
        """Convert score to label."""
        if score <= 25:
            return "Extreme Fear"
        elif score <= 45:
            return "Fear"
        elif score <= 55:
            return "Neutral"
        elif score <= 75:
            return "Greed"
        else:
            return "Extreme Greed"


# Global instances
_cnn_fetcher: CNNFearGreedFetcher | None = None
_alt_source: AlternativeFearGreedSource | None = None


def get_cnn_fetcher() -> CNNFearGreedFetcher:
    """Get CNN Fear & Greed fetcher instance."""
    global _cnn_fetcher
    if _cnn_fetcher is None:
        _cnn_fetcher = CNNFearGreedFetcher()
    return _cnn_fetcher


def get_alternative_source(fmp_client: FMPClientLike) -> AlternativeFearGreedSource:
    """Get alternative Fear & Greed source."""
    global _alt_source
    if _alt_source is None:
        _alt_source = AlternativeFearGreedSource(fmp_client)
    return _alt_source
