"""
backend/layer_1_data/fetchers/polygon_client.py
════════════════════════════════════════════════════════════════════════════════
Polygon.io Async Client — Market Data & Status.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

try:
    import httpx as _httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _httpx = None  # type: ignore
    _HTTPX_AVAILABLE = False

from backend.config.settings import load_settings
from backend.domain.polygon_models import PolygonMarketStatus, PolygonQuote, PolygonSnapshotResponse

logger = logging.getLogger("backend.layer_1_data.fetchers.polygon_client")

_POLYGON_BASE_URL = "https://api.polygon.io"
_DEFAULT_TIMEOUT = 8.0  # seconds
_MAX_RETRIES = 3
_BASE_DELAY = 0.5  # seconds


class PolygonClient:
    """
    Async client for Polygon.io REST API.
    Stateless and fail-graceful.
    """

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.settings = load_settings()
        self._timeout = timeout

    @property
    def api_key(self) -> str:
        return self.settings.polygon_key or ""

    def _is_active(self) -> bool:
        return bool(self.api_key) and _HTTPX_AVAILABLE

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self._is_active():
            return None

        merged: dict[str, Any] = {"apiKey": self.api_key}
        if params:
            merged.update(params)

        url = _POLYGON_BASE_URL + path
        for attempt in range(_MAX_RETRIES):
            try:
                async with _httpx.AsyncClient(timeout=self._timeout) as client:
                    res = await client.get(url, params=merged)
                    if res.status_code == 200:
                        return res.json()
                    if res.status_code in (429, 503, 504):
                        delay = _BASE_DELAY * (2**attempt) + random.uniform(0, 0.3)
                        await asyncio.sleep(delay)
                        continue
                    logger.debug("Polygon request failed: %s (status=%d)", path, res.status_code)
                    return None
            except Exception as exc:
                if attempt == _MAX_RETRIES - 1:
                    logger.debug("Polygon request error for %s: %s", path, exc)
                else:
                    delay = _BASE_DELAY * (2**attempt) + random.uniform(0, 0.3)
                    await asyncio.sleep(delay)
        return None

    async def get_market_status(self) -> PolygonMarketStatus | None:
        """
        Fetch real-time market status (Open/Closed/Holidays).
        """
        data = await self._get("/v1/marketstatus/now")
        if data:
            try:
                return PolygonMarketStatus(**data)
            except Exception as exc:
                logger.debug("Polygon market status parse error: %s", exc)
        return None

    async def get_snapshot(self, symbol: str) -> PolygonSnapshotResponse | None:
        """
        Fetch ticker snapshot (v2) from Polygon.
        """
        data = await self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol.upper()}")
        if data and data.get("status") == "OK" and "ticker" in data:
            try:
                return PolygonSnapshotResponse(**data)
            except Exception as exc:
                logger.debug("Polygon snapshot parse error for %s: %s", symbol, exc)
        return None

    async def get_quote(self, symbol: str) -> PolygonQuote | None:
        """
        Convenience wrapper to return a standardized Quote object.
        """
        snap = await self.get_snapshot(symbol)
        if snap and snap.ticker:
            ticker_data = snap.ticker
            # Priority: current day 'c' price -> current day 'p' (prev close) -> prevDay 'c'
            day_data = ticker_data.day or {}
            c_price = day_data.get("c")

            if not c_price and ticker_data.prevDay:
                c_price = ticker_data.prevDay.get("c")

            if c_price:
                return PolygonQuote(
                    symbol=symbol.upper(),
                    price=float(c_price),
                    change_pct=ticker_data.todaysChangePerc,
                    volume=day_data.get("v", 0),
                    timestamp=ticker_data.updated,
                )
        return None


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : polygon_client.py
# Sub-capa         : Fetchers (Real-time)
# Enfoque          : Cliente REST para Polygon.io (Legacy Migration).
# Integración      : Utiliza backend.config.settings (POLYGON_KEY).
# ─────────────────────────────────────────────────────────────────────
