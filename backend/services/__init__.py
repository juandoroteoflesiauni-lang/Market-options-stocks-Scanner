"""
Backend Services Layer - Business logic extracted from data fetchers.

This layer implements Clean Architecture principles:
- Layer 1 (Data): FMPClient - raw data fetching only
- Layer 3 (Specialists): Business logic, transformations
- Services: Orchestration between layers

Services:
    - FundamentalService: Main service for fundamental analysis
    - ValuationService: DCF and multiples valuation
    - ScoringService: Fundamental scoring engine
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.fundamental_service import FundamentalService

__all__ = [
    "FundamentalService",
]


def __getattr__(name: str) -> object:
    if name == "FundamentalService":
        from backend.services.fundamental_service import FundamentalService

        return FundamentalService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
