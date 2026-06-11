# ruff: noqa: F403, F405
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.config.logger_setup import get_logger
from backend.domain.fmp_models import *

logger = get_logger(__name__)

if TYPE_CHECKING:
    pass


class FMPTechnicalMixin:
    """Mixin for FMP Client."""

    async def get_technical_indicator(
        self,
        symbol: str,
        indicator_type: str,
        period: int = 14,
        limit: int = 50,
        timeframe: str = "daily",
    ) -> list[FMPTechnicalIndicator]:
        """
        Fetch technical indicator values for symbol.

        FMP endpoint: GET /technical_indicator/{timeframe}/{symbol}
        https://financialmodelingprep.com/api/v3/technical_indicator/{timeframe}/{symbol}

        Parameters
        ----------
        symbol         : Ticker (e.g. 'AAPL')
        indicator_type : 'ema' | 'rsi' | 'sma' | 'williams' | 'adx' | 'standardDeviation'
                         Note: 'macd' is blocked on free tier.
        period         : Lookback period (e.g. 14 for RSI, 20 for EMA)
        limit          : Number of bars to return
        timeframe      : 'daily' | '1min' | '5min' | '15min' | '30min' | '1hour' | '4hour'
        """
        data = await self._get(
            f"/technical_indicator/{timeframe}/{symbol.upper()}",
            module="TECHNICAL",
            params={"type": indicator_type, "period": period, "limit": limit},
            ttl_secs=3600.0,
        )
        return self._parse_list(data, FMPTechnicalIndicator)

    async def get_ema(
        self,
        symbol: str,
        period: int = 20,
        limit: int = 50,
    ) -> list[FMPTechnicalIndicator]:
        """
        Fetch EMA values for symbol.

        FMP endpoint: GET /technical_indicator/daily/{symbol}?type=ema
        https://financialmodelingprep.com/api/v3/technical_indicator/daily/{symbol}
        """
        return await self.get_technical_indicator(
            symbol=symbol,
            indicator_type="ema",
            period=period,
            limit=limit,
        )

    async def get_rsi(
        self,
        symbol: str,
        period: int = 14,
        limit: int = 50,
    ) -> list[FMPTechnicalIndicator]:
        """
        Fetch RSI values for symbol.

        FMP endpoint: GET /technical_indicator/daily/{symbol}?type=rsi
        https://financialmodelingprep.com/api/v3/technical_indicator/daily/{symbol}
        """
        return await self.get_technical_indicator(
            symbol=symbol,
            indicator_type="rsi",
            period=period,
            limit=limit,
        )

    async def get_historical_prices(
        self,
        symbol: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[FMPHistoricalPrice]:
        """Fetch daily OHLCV history. GET /v3/historical-price-full/{symbol}."""
        params: dict[str, Any] = {}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to
        data = await self._get(
            f"/historical-price-full/{symbol.upper()}",
            module="TECHNICAL",
            params=params,
            ttl_secs=3600.0,
        )
        if isinstance(data, dict):
            historical = data.get("historical")
            if isinstance(historical, list):
                return self._parse_list(historical, FMPHistoricalPrice)
        return []

    async def get_historical_chart(
        self,
        symbol: str,
        interval: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch intraday OHLCV history. Prefer GET /stable/historical-chart/{interval}."""
        params: dict[str, Any] = {}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to
        params["symbol"] = symbol.upper()
        data = await self._get_stable(
            f"/historical-chart/{interval}",
            module="MARKET",
            params=params,
            ttl_secs=300.0,
        )
        if not isinstance(data, list) or not data:
            legacy_params = {k: v for k, v in params.items() if k != "symbol"}
            data = await self._get(
                f"/historical-chart/{interval}/{symbol.upper()}",
                module="TECHNICAL",
                params=legacy_params,
                ttl_secs=300.0,
            )
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    async def get_sma(
        self, symbol: str, period: int = 50, limit: int = 1
    ) -> list[FMPTechnicalIndicator]:
        """Fetch SMA values. GET /v3/technical_indicator/daily/{symbol}?type=sma."""
        return await self.get_technical_indicator(
            symbol=symbol,
            indicator_type="sma",
            period=period,
            limit=limit,
        )

    async def get_short_volume(self, symbol: str) -> list[FMPShortVolume]:
        """
        Fetch historical short volume data for symbol (v4).
        """
        data = await self._get(
            f"/v4/short-volume/{symbol.upper()}", module="MARKET", ttl_secs=86400.0
        )
        return self._parse_list(data, FMPShortVolume)

    async def get_options_iv_history(self, symbol: str) -> list[FMPOptionsIVHistorical]:
        """Fetch historical options implied volatility. GET /v4/options-iv-historical."""
        data = await self._get(
            "/v4/options-iv-historical",
            module="STATEMENTS",
            params={"symbol": symbol.upper()},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPOptionsIVHistorical)

    async def get_short_interest(self, symbol: str) -> list[FMPShortInterest]:
        """
        Fetch historical short interest data for symbol (v4).
        """
        data = await self._get(
            f"/v4/short-interest/{symbol.upper()}", module="MARKET", ttl_secs=86400.0
        )
        return self._parse_list(data, FMPShortInterest)
