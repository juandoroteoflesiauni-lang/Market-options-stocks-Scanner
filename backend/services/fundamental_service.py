"""
Fundamental Analysis Service - Business logic for fundamental data processing.

This service extracts business logic from FMPClient (layer 1) and implements
Clean Architecture principles:
- FMPClient: Raw data fetching only (no transformations)
- FundamentalService: Business logic, transformations, calculations
- Router: HTTP interface (no business logic)

Performance optimizations:
- Multi-level caching (L1 + L2 Redis)
- Volatility-based TTL
- Batched concurrent requests with semaphore limiting
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from backend.infrastructure.cache.multi_level_cache import MultiLevelCache, get_cache
from backend.infrastructure.cache.volatility_ttl import get_ttl_for_endpoint
from backend.layer_1_data.fetchers.fmp_client import FMPClient

logger = logging.getLogger(__name__)


class FundamentalService:
    """
    Service for fundamental analysis with optimized caching and concurrency.

    This service:
    1. Fetches raw data from FMPClient (layer 1)
    2. Applies business logic transformations
    3. Caches results with volatility-based TTL
    4. Returns structured data for UI consumption

    Example
    -------
    >>> service = FundamentalService()
    >>> await service.connect()
    >>> analysis = await service.get_full_analysis("AAPL")
    >>> await service.disconnect()
    """

    def __init__(self, fmp_client: FMPClient | None = None):
        self._fmp = fmp_client or FMPClient()
        self._cache: MultiLevelCache | None = None
        self._semaphore = asyncio.Semaphore(10)  # Max 10 concurrent requests

    async def connect(self, redis_url: str = "redis://localhost:6379") -> None:
        """Initialize cache connection."""
        self._cache = get_cache()
        await self._cache.connect()
        logger.info("FundamentalService connected")

    async def disconnect(self) -> None:
        """Close cache connections."""
        if self._cache:
            await self._cache.disconnect()
        logger.info("FundamentalService disconnected")

    async def get_full_analysis(self, symbol: str) -> dict[str, Any]:
        """
        Get comprehensive fundamental analysis for a symbol.

        This is the main entry point for fundamental data. It:
        1. Checks cache first (L1 → L2 → Backend)
        2. Fetches from FMP if not cached
        3. Applies business logic transformations
        4. Caches result with appropriate TTL
        5. Returns structured data

        Parameters
        ----------
        symbol : str
            Stock ticker symbol (e.g., "AAPL")

        Returns
        -------
        Dict[str, Any]
            Complete fundamental analysis including:
            - Profile, Quote, Valuation metrics
            - Profitability, Growth, Debt metrics
            - Technical indicators, Earnings data
            - Analyst estimates, Dividends
        """
        symbol = symbol.upper().strip()
        cache_key = f"fundamental:analysis:{symbol}"

        # Try cache first
        if self._cache:
            cached = await self._cache.get(cache_key)
            if cached:
                logger.info(f"Cache HIT for {symbol}")
                return cached

        logger.info(f"Cache MISS for {symbol}, fetching from FMP...")

        # Fetch with semaphore to avoid rate limiting
        async with self._semaphore:
            result = await self._fmp.get_full_fundamental_analysis(symbol)

        # Apply business logic transformations
        transformed = self._transform_analysis(result, symbol)

        # Cache with volatility-based TTL
        if self._cache:
            ttl = get_ttl_for_endpoint("/profile/", "GET")  # 30 days for fundamentals
            await self._cache.set(cache_key, transformed, ttl=ttl)

        return transformed

    def _transform_analysis(self, data: dict[str, Any], symbol: str) -> dict[str, Any]:
        """
        Apply business logic transformations to raw FMP data.

        This moves logic from FMPClient.get_full_fundamental_analysis()
        to the service layer where it belongs.

        Transformations applied:
        1. Valuation metrics normalization
        2. Profitability ratios calculation
        3. Growth metrics derivation
        4. Debt analysis and scoring
        5. Technical indicator processing
        6. Earnings surprise calculations
        """
        if not data:
            return {"error": "No data available", "symbol": symbol}

        result = {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "data_version": "2.0",  # New transformed format
        }

        # Profile section
        if "profile" in data:
            result["profile"] = self._transform_profile(data["profile"])

        # Quote section
        if "quote" in data:
            result["quote"] = self._transform_quote(data["quote"], data.get("profile"))

        # Valuation section
        if "valuation" in data:
            result["valuation"] = self._transform_valuation(data["valuation"])

        # Profitability section
        if "profitability" in data:
            result["profitability"] = self._transform_profitability(data["profitability"])

        # Debt section
        if "debt" in data:
            result["debt"] = self._transform_debt(data["debt"])

        # Growth section
        if "growth" in data:
            result["growth"] = self._transform_growth(data["growth"])

        # Dividends section
        if "dividends" in data:
            result["dividends"] = self._transform_dividends(data["dividends"])

        # Technical section
        if "technical" in data:
            result["technical"] = self._transform_technical(data["technical"])

        # Earnings section
        if "earnings" in data:
            result["earnings"] = self._transform_earnings(data["earnings"])

        # Estimates section
        if "estimates" in data:
            result["estimates"] = self._transform_estimates(data["estimates"])

        # Intelligence v4 section (if available)
        if "intelligence_v4" in data:
            result["intelligence"] = data["intelligence_v4"]

        # Drawdown section
        if "drawdown" in data:
            result["drawdown"] = data["drawdown"]

        return result

    def _transform_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Transform profile data with business logic."""
        return {
            "companyName": profile.get("companyName"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "country": profile.get("country"),
            "marketCap": profile.get("mktCap"),
            "price": profile.get("price"),
            "beta": profile.get("beta"),
            "description": profile.get("description"),
            "ceo": profile.get("ceo"),
            "employees": profile.get("fullTimeEmployees"),
            "website": profile.get("website"),
        }

    def _transform_quote(
        self, quote: dict[str, Any], profile: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Transform quote data with fallback logic."""
        price = quote.get("price") or (profile.get("price") if profile else None)

        return {
            "price": price,
            "change": quote.get("change"),
            "changePercent": quote.get("changePercent"),
            "marketCap": quote.get("marketCap"),
            "volume": quote.get("volume"),
            "avgVolume": quote.get("avgVolume"),
            "yearHigh": quote.get("yearHigh"),
            "yearLow": quote.get("yearLow"),
            "pe": quote.get("pe"),
            "eps": quote.get("eps"),
            "timestamp": quote.get("timestamp"),
        }

    def _transform_valuation(self, valuation: dict[str, Any]) -> dict[str, Any]:
        """Transform valuation metrics with derived calculations."""
        ttm = valuation.get("ttm", {})
        historical = valuation.get("historico", [])

        # Add derived metrics
        if ttm.get("peRatio") and ttm.get("pegRatio"):
            ttm["impliedGrowth"] = ttm["peRatio"] / ttm["pegRatio"] if ttm["pegRatio"] > 0 else None

        return {
            "ttm": ttm,
            "historical": historical,
        }

    def _transform_profitability(self, profitability: dict[str, Any]) -> dict[str, Any]:
        """Transform profitability metrics with scoring."""
        ttm = profitability.get("ttm", {})

        # Add profitability score
        score = 0.0
        if ttm.get("roe"):
            score += min(ttm["roe"] * 10, 0.4)  # 40% weight on ROE
        if ttm.get("roa"):
            score += min(ttm["roa"] * 10, 0.3)  # 30% weight on ROA
        if ttm.get("netMargin"):
            score += min(ttm["netMargin"] * 5, 0.3)  # 30% weight on margin

        ttm["score"] = round(score, 4)

        return {
            "ttm": ttm,
            "historical": profitability.get("historico", []),
        }

    def _transform_debt(self, debt: dict[str, Any]) -> dict[str, Any]:
        """Transform debt metrics with risk assessment."""
        ttm = debt.get("ttm", {})

        # Add debt risk score
        risk_score = 0.0
        if ttm.get("debtEquity"):
            de = ttm["debtEquity"]
            if de < 0.5:
                risk_score += 0.9
            elif de < 1.0:
                risk_score += 0.7
            elif de < 2.0:
                risk_score += 0.4
            else:
                risk_score += 0.2

        if ttm.get("interestCoverage"):
            ic = ttm["interestCoverage"]
            if ic > 10:
                risk_score += 0.1  # Add to reach 1.0 max
            elif ic > 5:
                risk_score += 0.05

        ttm["riskScore"] = round(min(risk_score, 1.0), 4)

        return {
            "ttm": ttm,
            "historical": debt.get("historico", []),
        }

    def _transform_growth(self, growth: dict[str, Any]) -> dict[str, Any]:
        """Transform growth metrics with consistency analysis."""
        return growth  # Pass through for now

    def _transform_dividends(self, dividends: dict[str, Any]) -> dict[str, Any]:
        """Transform dividend metrics with yield analysis."""
        return dividends  # Pass through for now

    def _transform_technical(self, technical: dict[str, Any]) -> dict[str, Any]:
        """Transform technical indicators with signals."""
        signals = []

        # RSI signal
        if technical.get("rsi"):
            rsi = technical["rsi"]
            if rsi < 30:
                signals.append("OVERSOLD")
            elif rsi > 70:
                signals.append("OVERBOUGHT")

        # Golden/Death cross
        if technical.get("goldenCross") is not None:
            if technical["goldenCross"]:
                signals.append("GOLDEN_CROSS")
            else:
                signals.append("DEATH_CROSS")

        technical["signals"] = signals

        return technical

    def _transform_earnings(self, earnings: dict[str, Any]) -> dict[str, Any]:
        """Transform earnings data with surprise analysis."""
        historical = earnings.get("historial", [])

        # Calculate beat rate
        if historical:
            beats = sum(1 for e in historical if e.get("beat"))
            earnings["beatRate"] = round(beats / len(historical), 4)

        return earnings

    def _transform_estimates(self, estimates: dict[str, Any]) -> dict[str, Any]:
        """Transform analyst estimates with consensus scoring."""
        consenso = estimates.get("consenso", {})

        # Add consensus score
        if consenso.get("buy") and consenso.get("sell"):
            total = consenso["buy"] + consenso["hold"] + consenso["sell"]
            if total > 0:
                consenso["consensusScore"] = round((consenso["buy"] - consenso["sell"]) / total, 4)

        return estimates


__all__ = ["FundamentalService"]
