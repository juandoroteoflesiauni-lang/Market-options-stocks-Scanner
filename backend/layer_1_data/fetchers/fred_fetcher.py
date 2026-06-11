"""FRED API fetcher for macroeconomic data."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, cast

from backend.config.settings import load_settings
from backend.layer_1_data.fetchers.async_market_core import (
    AsyncTTLCache,
    CircuitBreaker,
    fetch_json_singleflight,
)

logger = logging.getLogger("backend.layer_1_data.fetchers.fred_fetcher")


class FredFetcher:
    """Fetcher for FRED (Federal Reserve Economic Data) API."""

    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self) -> None:
        self.settings = load_settings()
        self.cache = AsyncTTLCache()
        self.circuit_breaker = CircuitBreaker()
        self._client_name = "fred_fetcher"

    async def _get_api_key(self) -> str | None:
        """Get FRED API key from settings."""
        api_key = getattr(self.settings, "fred_api_key", None)
        if not api_key:
            logger.debug("FRED_API_KEY not available")
            return None
        return str(api_key)

    async def _fetch_series_observations(
        self,
        series_id: str,
        limit: int = 13,
        sort_order: str = "desc",
    ) -> dict[str, Any] | None:
        """Fetch observations for a FRED series with caching."""
        # Check if API key is available
        api_key = await self._get_api_key()
        if not api_key:
            return None

        cache_key = f"fred:{series_id}:{limit}:{sort_order}"

        payload = await fetch_json_singleflight(
            client_name=self._client_name,
            url=self.BASE_URL,
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "limit": limit,
                "sort_order": sort_order,
            },
            cache=self.cache,
            cache_key=cache_key,
            ttl_secs=3600,  # 1 hour TTL as specified
            circuit=self.circuit_breaker,
            timeout=10.0,
            max_retries=3,
            stale_on_error=True,
        )
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else None

    def _extract_latest_value(self, observations: list[dict[str, Any]]) -> float | None:
        """Extract the latest non-missing value from observations."""
        for obs in observations:
            value = obs.get("value", ".")
            if value != ".":
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

    def _calculate_yoy(self, observations: list[dict[str, Any]]) -> float | None:
        """Calculate year-over-year change from monthly observations."""
        if len(observations) < 13:
            return None

        # Get latest value (most recent due to sort_order=desc)
        latest_value = self._extract_latest_value(observations)
        if latest_value is None:
            return None

        # Get value from 12 months ago
        year_ago_value = None
        for i, obs in enumerate(observations):
            if i == 12:  # 13th element (0-indexed) is 12 months ago
                value = obs.get("value", ".")
                if value != ".":
                    with contextlib.suppress(ValueError):
                        year_ago_value = float(value)
                break

        if year_ago_value is None or year_ago_value == 0:
            return None

        return ((latest_value - year_ago_value) / year_ago_value) * 100.0

    async def get_fed_funds_rate(self) -> float | None:
        """Get federal funds rate (FEDFUNDS)."""
        data = await self._fetch_series_observations("FEDFUNDS", limit=1)
        if not data:
            return None

        observations = data.get("observations", [])
        return self._extract_latest_value(observations)

    async def get_cpi_yoy(self) -> float | None:
        """Get CPI year-over-year change (CPIAUCSL)."""
        data = await self._fetch_series_observations("CPIAUCSL", limit=13)
        if not data:
            return None

        observations = data.get("observations", [])
        return self._calculate_yoy(observations)

    async def get_unemployment_rate(self) -> float | None:
        """Get unemployment rate (UNRATE)."""
        data = await self._fetch_series_observations("UNRATE", limit=1)
        if not data:
            return None

        observations = data.get("observations", [])
        return self._extract_latest_value(observations)

    async def get_yield_spread_10y2y(self) -> float | None:
        """Get 10Y-2Y yield spread (DGS10 - DGS2)."""
        # Fetch both series concurrently
        dgs10_task = self._fetch_series_observations("DGS10", limit=1)
        dgs2_task = self._fetch_series_observations("DGS2", limit=1)

        dgs10_data, dgs2_data = await asyncio.gather(dgs10_task, dgs2_task)

        if not dgs10_data or not dgs2_data:
            return None

        dgs10_obs = dgs10_data.get("observations", [])
        dgs2_obs = dgs2_data.get("observations", [])

        dgs10_value = self._extract_latest_value(dgs10_obs)
        dgs2_value = self._extract_latest_value(dgs2_obs)

        if dgs10_value is None or dgs2_value is None:
            return None

        return dgs10_value - dgs2_value

    async def get_pce_inflation(self) -> float | None:
        """Get PCE inflation year-over-year change (PCEPI)."""
        data = await self._fetch_series_observations("PCEPI", limit=13)
        if not data:
            return None

        observations = data.get("observations", [])
        return self._calculate_yoy(observations)

    async def get_vix_close(self) -> dict[str, Any] | None:
        """Get the latest daily CBOE VIX close from FRED (VIXCLS)."""
        data = await self._fetch_series_observations("VIXCLS", limit=5)
        if not data:
            return None

        observations = data.get("observations", [])
        if not isinstance(observations, list):
            return None
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            value = obs.get("value", ".")
            if value == ".":
                continue
            try:
                return {
                    "value": float(value),
                    "date": obs.get("date"),
                    "source": "fred_vixcls",
                }
            except ValueError:
                continue
        return None

    async def get_macro_snapshot(self) -> dict[str, float | None]:
        """Get a snapshot of all key macro indicators."""
        # Run all fetches in parallel
        fed_funds, cpi_yoy, unemployment, yield_spread, pce_inflation = await asyncio.gather(
            self.get_fed_funds_rate(),
            self.get_cpi_yoy(),
            self.get_unemployment_rate(),
            self.get_yield_spread_10y2y(),
            self.get_pce_inflation(),
        )

        return {
            "fed_funds_rate": fed_funds,
            "cpi_yoy": cpi_yoy,
            "unemployment_rate": unemployment,
            "yield_spread_10y2y": yield_spread,
            "pce_inflation": pce_inflation,
        }
