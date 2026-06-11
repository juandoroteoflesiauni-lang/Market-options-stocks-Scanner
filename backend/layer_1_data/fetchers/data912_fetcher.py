"""
backend/layer_1_data/fetchers/data912_fetcher.py
════════════════════════════════════════════════════════════════════════════════
Data912 — High-frequency connector for Argentine live market data.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx
from pydantic import BaseModel

from backend.config.settings import load_settings
from backend.domain.data912_models import (
    Data912CorporateAction,
    Data912Dividend,
    Data912Earnings,
    Data912HistoricalPoint,
    Data912LiveQuote,
    Data912OptionChainItem,
    Data912VolatilityMetrics,
)

logger = logging.getLogger("backend.layer_1_data.fetchers.data912")

_TIMEOUT_DEFAULT: Final[int] = 15


class Data912Fetcher:
    """
    Asynchronous fetcher for Data912.com.
    Stateless and fail-graceful.
    """

    def __init__(self, timeout: int = _TIMEOUT_DEFAULT) -> None:
        self.settings = load_settings()
        self._timeout = timeout

    async def _get(self, path: str) -> Any:
        """Generic async GET requester."""
        url = f"{self.settings.data912_base_url}/{path.lstrip('/')}"
        params = {}
        if self.settings.data912_api_key:
            params["api_key"] = self.settings.data912_api_key
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={
                        "User-Agent": "QuantumAnalyzer/1.0",
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            logger.debug("Data912 API error on %s: %s", path, exc)
            return []  # Always return list to avoid iteration errors
        except Exception as exc:
            logger.error("Unexpected error fetching Data912 path %s: %s", path, exc)
            return []

    # ── Live Data ────────────────────────────────────────────────────────────

    async def get_live_mep(self) -> list[Data912LiveQuote]:
        """Fetch live MEP quotes for CEDEARs and local assets."""
        data = await self._get("live/mep")
        return self._parse_quotes(data)

    async def get_live_ccl(self) -> list[Data912LiveQuote]:
        """Fetch live CCL quotes for CEDEARs and local assets."""
        data = await self._get("live/ccl")
        return self._parse_quotes(data)

    async def get_live_stocks(self) -> list[Data912LiveQuote]:
        """Fetch live local Argentine stocks."""
        data = await self._get("live/arg_stocks")
        return self._parse_quotes(data)

    async def get_live_bonds(self) -> list[Data912LiveQuote]:
        """Fetch live government bonds."""
        data = await self._get("live/arg_bonds")
        return self._parse_quotes(data)

    async def get_live_cedears(self) -> list[Data912LiveQuote]:
        """Fetch live CEDEARs quotes."""
        data = await self._get("live/arg_cedears")
        return self._parse_quotes(data)

    async def get_live_indices(self) -> list[Data912LiveQuote]:
        """Fetch live Indices (Using arg_stocks as proxy if needed)."""
        data = await self._get("live/arg_stocks")
        return self._parse_quotes(data)

    async def get_live_forex(self) -> list[Data912LiveQuote]:
        """Fetch live Forex."""
        data = await self._get("live/mep")
        return self._parse_quotes(data)

    async def get_live_commodities(self) -> list[Data912LiveQuote]:
        """Fetch live Commodities (Gold, Oil, Soy)."""
        data = await self._get("live/commodities")
        return self._parse_quotes(data)

    async def get_live_crypto(self) -> list[Data912LiveQuote]:
        """Fetch live Crypto quotes."""
        data = await self._get("live/crypto")
        return self._parse_quotes(data)

    async def get_live_us_stocks(self) -> list[Data912LiveQuote]:
        """Fetch live US Stocks (Top tickers)."""
        data = await self._get("live/usa_stocks")
        return self._parse_quotes(data)

    async def get_live_usa_adrs(self) -> list[Data912LiveQuote]:
        """Fetch live USA ADRs."""
        data = await self._get("live/usa_adrs")
        return self._parse_quotes(data)

    # ── Historical Data ──────────────────────────────────────────────────────

    async def get_historical_ohlcv(
        self, instrument_type: str, ticker: str
    ) -> list[Data912HistoricalPoint]:
        """
        Fetch historical OHLCV.
        instrument_type can be 'stocks', 'cedears', 'bonds'.
        """
        ticker = ticker.strip().upper()
        path = f"historical/{instrument_type}/{ticker}"
        data = await self._get(path)
        if not isinstance(data, list):
            return []

        points = []
        for item in data:
            try:
                points.append(Data912HistoricalPoint(**item))
            except Exception:
                continue
        return points

    # ── Analytics ────────────────────────────────────────────────────────────

    async def get_eod_volatilities(self, ticker: str) -> Data912VolatilityMetrics | None:
        """Fetch volatility metrics for a ticker."""
        ticker = ticker.strip().upper()
        data = await self._get(f"eod/volatilities/{ticker}")
        if not isinstance(data, dict):
            return None
        try:
            return Data912VolatilityMetrics(**data)
        except Exception:
            return None

    async def get_option_chain(self, ticker: str) -> list[Data912OptionChainItem]:
        """Fetch option chain for an underlying ticker."""
        ticker = ticker.strip().upper()
        data = await self._get(f"eod/option_chain/{ticker}")
        if not isinstance(data, list):
            return []

        chain = []
        for item in data:
            try:
                chain.append(Data912OptionChainItem(**item))
            except Exception:
                continue
        return chain

    async def get_corporate_actions(self, ticker: str) -> list[Data912CorporateAction]:
        """Fetch corporate actions for a ticker."""
        ticker = ticker.strip().upper()
        data = await self._get(f"eod/corporate_actions/{ticker}")
        return self._parse_items(data, Data912CorporateAction)

    async def get_dividends(self, ticker: str) -> list[Data912Dividend]:
        """Fetch dividends history."""
        ticker = ticker.strip().upper()
        data = await self._get(f"eod/dividends/{ticker}")
        return self._parse_items(data, Data912Dividend)

    async def get_earnings(self, ticker: str) -> list[Data912Earnings]:
        """Fetch earnings reports."""
        ticker = ticker.strip().upper()
        data = await self._get(f"eod/earnings/{ticker}")
        return self._parse_items(data, Data912Earnings)

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _parse_quotes(self, data: Any) -> list[Data912LiveQuote]:
        return self._parse_items(data, Data912LiveQuote)

    def _parse_items(self, data: Any, model_class: type[BaseModel]) -> list[Any]:
        if not isinstance(data, list):
            return []
        items = []
        for item in data:
            try:
                items.append(model_class(**item))
            except Exception:
                continue
        return items


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : data912_fetcher.py
# Sub-capa         : Fetchers
# Enfoque          : Conector asíncrono para Data912.
# ─────────────────────────────────────────────────────────────────────
