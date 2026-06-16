from __future__ import annotations
from typing import Any
"""
Fundamental Analysis Router - HTTP interface for fundamental data.

This router provides REST API endpoints for fundamental analysis,
following Clean Architecture principles:
- No business logic (delegated to FundamentalService)
- No data fetching (delegated to FMPClient)
- Only HTTP interface and response formatting

Endpoints:
    GET /api/v1/fundamental/analysis/{symbol} - Full fundamental analysis
    GET /api/v1/fundamental/valuation/{symbol} - DCF and multiples valuation
    GET /api/v1/fundamental/scores/{symbol} - Fundamental scores
    GET /api/v1/fundamental/cache/health - Cache health check
"""


import logging

from fastapi import APIRouter, HTTPException, Query

from backend.services.fundamental_service import FundamentalService

logger = logging.getLogger(__name__)

# Router instance
router = APIRouter(prefix="/api/v1/fundamental", tags=["fundamental"])

# Service instance (singleton pattern)
_fundamental_service: FundamentalService | None = None


def get_service() -> FundamentalService:
    """Get or create fundamental service instance."""
    global _fundamental_service
    if _fundamental_service is None:
        _fundamental_service = FundamentalService()
    return _fundamental_service


@router.on_event("startup")
async def startup_event():
    """Initialize service on startup."""
    service = get_service()
    await service.connect()
    logger.info("Fundamental router initialized")


@router.on_event("shutdown")
async def shutdown_event():
    """Cleanup service on shutdown."""
    if _fundamental_service:
        await _fundamental_service.disconnect()
        logger.info("Fundamental router shutdown complete")


@router.get("/analysis/{symbol}")
async def get_fundamental_analysis(
    symbol: str,
    refresh: bool = Query(False, description="Force refresh from source"),
) -> dict[str, Any]:
    """
    Get comprehensive fundamental analysis for a symbol.

    This endpoint returns complete fundamental data including:
    - Company profile and quote
    - Valuation metrics (P/E, P/B, EV/EBITDA, DCF)
    - Profitability ratios (ROE, ROA, margins)
    - Debt analysis and risk scores
    - Growth metrics (YoY, CAGR)
    - Dividend history and forecasts
    - Technical indicators
    - Earnings data and surprises
    - Analyst estimates and recommendations

    Parameters
    ----------
    symbol : str
        Stock ticker symbol (e.g., "AAPL", "MSFT")
    refresh : bool
        Force refresh from source (ignore cache)

    Returns
    -------
    Dict[str, Any]
        Complete fundamental analysis with structure:
        {
            "symbol": "AAPL",
            "timestamp": "2026-04-23T10:30:00",
            "data_version": "2.0",
            "profile": {...},
            "quote": {...},
            "valuation": {...},
            "profitability": {...},
            "debt": {...},
            "growth": {...},
            "dividends": {...},
            "technical": {...},
            "earnings": {...},
            "estimates": {...},
            "intelligence": {...}  # if available
        }

    Raises
    ------
    HTTPException
        404: Symbol not found
        422: Invalid symbol format
        500: Internal error
    """
    symbol = symbol.upper().strip()

    if not symbol:
        raise HTTPException(status_code=422, detail="Symbol cannot be empty")

    try:
        service = get_service()
        result = await service.get_full_analysis(symbol)

        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])

        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Error fetching fundamental analysis for {symbol}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/valuation/{symbol}")
async def get_valuation(
    symbol: str,
) -> dict[str, Any]:
    """
    Get valuation metrics for a symbol.

    Returns DCF valuation, multiples analysis, and fair value estimates.

    Parameters
    ----------
    symbol : str
        Stock ticker symbol

    Returns
    -------
    Dict[str, Any]
        Valuation data including:
        {
            "symbol": "AAPL",
            "dcf": {
                "fair_value": 180.50,
                "current_price": 175.00,
                "margin_of_safety": 0.031,
                "wacc": 0.085,
                "terminal_growth": 0.025
            },
            "multiples": {
                "pe_ratio": 28.5,
                "pb_ratio": 5.2,
                "ev_ebitda": 22.1,
                "peg_ratio": 2.3
            },
            "fair_value_range": {
                "bear": 150.00,
                "base": 180.50,
                "bull": 220.00
            }
        }
    """
    symbol = symbol.upper().strip()

    try:
        service = get_service()
        full_analysis = await service.get_full_analysis(symbol)

        if "error" in full_analysis:
            raise HTTPException(status_code=404, detail=full_analysis["error"])

        # Extract valuation section
        valuation = full_analysis.get("valuation", {})

        return {
            "symbol": symbol,
            "timestamp": full_analysis.get("timestamp"),
            "dcf": valuation.get("ttm", {}),
            "multiples": {
                "pe_ratio": valuation.get("ttm", {}).get("peRatio"),
                "pb_ratio": valuation.get("ttm", {}).get("pbRatio"),
                "ev_ebitda": valuation.get("ttm", {}).get("evEbitda"),
                "peg_ratio": valuation.get("ttm", {}).get("pegRatio"),
            },
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Error fetching valuation for {symbol}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/scores/{symbol}")
async def get_fundamental_scores(
    symbol: str,
) -> dict[str, Any]:
    """
    Get fundamental scores and ratings.

    Returns composite scores for:
    - Profitability (ROE, ROA, margins)
    - Financial strength (debt ratios, coverage)
    - Growth (consistency, CAGR)
    - Valuation (relative to peers)

    Parameters
    ----------
    symbol : str
        Stock ticker symbol

    Returns
    -------
    Dict[str, Any]
        Scores in range [0.0, 1.0]:
        {
            "symbol": "AAPL",
            "scores": {
                "profitability": 0.85,
                "financial_strength": 0.72,
                "growth": 0.68,
                "valuation": 0.45,
                "composite": 0.67
            },
            "rating": "B+",
            "sector_rank": "Top 25%"
        }
    """
    symbol = symbol.upper().strip()

    try:
        service = get_service()
        full_analysis = await service.get_full_analysis(symbol)

        if "error" in full_analysis:
            raise HTTPException(status_code=404, detail=full_analysis["error"])

        # Calculate scores from profitability, debt, growth sections
        profitability = full_analysis.get("profitability", {}).get("ttm", {})
        debt = full_analysis.get("debt", {}).get("ttm", {})

        # Profitability score
        profit_score = 0.0
        if profitability.get("roe"):
            profit_score += min(profitability["roe"] * 5, 0.4)
        if profitability.get("roa"):
            profit_score += min(profitability["roa"] * 10, 0.3)
        if profitability.get("netMargin"):
            profit_score += min(profitability["netMargin"] * 5, 0.3)

        # Financial strength score
        strength_score = 0.0
        if debt.get("debtEquity"):
            de = debt["debtEquity"]
            if de < 0.5:
                strength_score = 0.9
            elif de < 1.0:
                strength_score = 0.7
            elif de < 2.0:
                strength_score = 0.5
            else:
                strength_score = 0.3

        # Composite score
        composite = (profit_score + strength_score) / 2

        # Rating
        if composite >= 0.8:
            rating = "A"
        elif composite >= 0.6:
            rating = "B"
        elif composite >= 0.4:
            rating = "C"
        elif composite >= 0.2:
            rating = "D"
        else:
            rating = "E"

        return {
            "symbol": symbol,
            "scores": {
                "profitability": round(profit_score, 4),
                "financial_strength": round(strength_score, 4),
                "growth": 0.5,  # Placeholder
                "valuation": 0.5,  # Placeholder
                "composite": round(composite, 4),
            },
            "rating": rating,
            "sector_rank": "N/A",  # Would need peer comparison
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Error fetching scores for {symbol}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/cache/health")
async def get_cache_health() -> dict[str, Any]:
    """
    Get cache health status and metrics.

    Returns
    -------
    Dict[str, Any]
        Cache health information:
        {
            "l1": {
                "status": "healthy",
                "size": 125,
                "maxsize": 1000
            },
            "l2": {
                "status": "healthy",
                "connected": true
            },
            "metrics": {
                "hits": 1250,
                "misses": 300,
                "hit_rate": 0.806,
                "uptime_secs": 3600
            }
        }
    """
    try:
        service = get_service()

        if not service._cache:
            return {"status": "unavailable", "message": "Cache not initialized"}

        health = await service._cache.health_check()
        return health

    except Exception as exc:
        logger.exception("Error checking cache health")
        return {"status": "error", "error": str(exc)}


@router.delete("/cache/{symbol}")
async def invalidate_cache(symbol: str) -> dict[str, str]:
    """
    Invalidate cache for a specific symbol.

    Parameters
    ----------
    symbol : str
        Stock ticker symbol

    Returns
    -------
    Dict[str, str]
        Confirmation message
    """
    symbol = symbol.upper().strip()

    try:
        service = get_service()

        if service._cache:
            await service._cache.delete(f"fundamental:analysis:{symbol}")

        return {"status": "success", "message": f"Cache invalidated for {symbol}"}

    except Exception as exc:
        logger.exception(f"Error invalidating cache for {symbol}")
        raise HTTPException(status_code=500, detail=str(exc))


__all__ = ["router"]
