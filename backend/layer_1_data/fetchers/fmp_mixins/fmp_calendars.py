from __future__ import annotations

from typing import TYPE_CHECKING

from backend.config.logger_setup import get_logger
from backend.domain.fmp_models import *

# ruff: noqa: F403, F405


logger = get_logger(__name__)

if TYPE_CHECKING:
    pass


class FMPCalendarsMixin:
    """Mixin for FMP Client."""

    async def get_earnings_calendar(
        self,
        date_from: str,
        date_to: str,
    ) -> list[FMPEarningsCalendarItem]:
        """
        Fetch upcoming earnings calendar.

        FMP endpoint: GET /earning_calendar
        https://financialmodelingprep.com/api/v3/earning_calendar

        Parameters
        ----------
        date_from : Start date 'YYYY-MM-DD'
        date_to   : End date   'YYYY-MM-DD'
        """
        data = await self._get(
            "/earning_calendar",
            module="CALENDARS",
            params={"from": date_from, "to": date_to},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPEarningsCalendarItem)

    async def get_historical_earnings(
        self,
        symbol: str,
        limit: int = 8,
    ) -> list[FMPEarningsCalendarItem]:
        """
        Fetch historical earnings surprises for symbol.

        FMP endpoint: GET /historical/earning_calendar/{symbol}
        https://financialmodelingprep.com/api/v3/historical/earning_calendar/{symbol}
        """
        data = await self._get(
            f"/historical/earning_calendar/{symbol.upper()}",
            module="CALENDARS",
            params={"limit": limit},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPEarningsCalendarItem)

    async def get_ipo_calendar(
        self,
        date_from: str,
        date_to: str,
    ) -> list[FMPIPOCalendarItem]:
        """
        Fetch IPO pipeline calendar.

        FMP endpoint: GET /ipo_calendar
        https://financialmodelingprep.com/api/v3/ipo_calendar

        Parameters
        ----------
        date_from : Start date 'YYYY-MM-DD'
        date_to   : End date   'YYYY-MM-DD'
        """
        data = await self._get(
            "/ipo_calendar",
            module="CALENDARS",
            params={"from": date_from, "to": date_to},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPIPOCalendarItem)

    async def get_dividend_calendar(
        self,
        date_from: str,
        date_to: str,
    ) -> list[FMPDividendCalendarItem]:
        """
        Fetch upcoming dividend calendar.

        FMP endpoint: GET /stock_dividend_calendar
        https://financialmodelingprep.com/api/v3/stock_dividend_calendar

        Parameters
        ----------
        date_from : Start date 'YYYY-MM-DD'
        date_to   : End date   'YYYY-MM-DD'
        """
        data = await self._get(
            "/stock_dividend_calendar",
            module="CALENDARS",
            params={"from": date_from, "to": date_to},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPDividendCalendarItem)

    async def get_historical_dividends(
        self,
        symbol: str,
    ) -> list[FMPDividendCalendarItem]:
        """
        Fetch historical dividend payments for symbol.

        FMP endpoint: GET /historical-price-full/stock_dividend/{symbol}
        https://financialmodelingprep.com/api/v3/historical-price-full/stock_dividend/{symbol}
        """
        data = await self._get(
            f"/historical-price-full/stock_dividend/{symbol.upper()}",
            module="CALENDARS",
            ttl_secs=86400.0,
        )
        # Response is {"symbol": ..., "historical": [...]}
        if isinstance(data, dict):
            historical = data.get("historical")
            if isinstance(historical, list):
                return self._parse_list(historical, FMPDividendCalendarItem)
        return []

    async def get_economic_calendar(
        self,
        date_from: str,
        date_to: str,
    ) -> list[FMPEconomicCalendarItem]:
        """
        Fetch macro event calendar (Fed meetings, CPI releases, etc.).

        FMP endpoint: GET /economic_calendar
        https://financialmodelingprep.com/api/v3/economic_calendar

        Parameters
        ----------
        date_from : Start date 'YYYY-MM-DD'
        date_to   : End date   'YYYY-MM-DD'
        """
        data = await self._get(
            "/economic_calendar",
            module="CALENDARS",
            params={"from": date_from, "to": date_to},
            ttl_secs=86400.0,
        )
        parsed = self._parse_list(data, FMPEconomicCalendarItem)
        if parsed:
            return parsed
        from backend.layer_1_data.fetchers.macro_fallback_fetcher import (
            fetch_economic_calendar_finnhub,
            macro_fallback_enabled,
        )

        if macro_fallback_enabled():
            return await fetch_economic_calendar_finnhub(date_from, date_to)
        return []

    async def get_earnings_surprises(self, symbol: str) -> list[FMPEarningsSurprise]:
        """Fetch historical EPS actual vs estimated. GET /v3/earnings-surprises/{symbol}."""
        data = await self._get(
            f"/earnings-surprises/{symbol.upper()}",
            module="CALENDARS",
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPEarningsSurprise)
