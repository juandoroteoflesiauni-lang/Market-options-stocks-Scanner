from __future__ import annotations

from typing import Any, Final

"""
backend/layer_1_data/fetchers/massive_client.py
════════════════════════════════════════════════════════════════════════════════
Massive API Client — Polygon.io Institutional Suite.
════════════════════════════════════════════════════════════════════════════════
"""


import logging

try:
    import httpx as _httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _httpx = None  # type: ignore
    _HTTPX_AVAILABLE = False

from backend.config.settings import load_settings

logger = logging.getLogger("backend.layer_1_data.fetchers.massive_client")

_DEFAULT_MASSIVE_URL: Final[str] = "https://api.polygon.io"
_DEFAULT_TIMEOUT: Final[float] = 20.0


class MassiveClient:
    """
    Async client for Massive institutional options and market data.
    Stateless and fail-graceful.
    """

    _shared_client: _httpx.AsyncClient | None = None

    @classmethod
    def get_shared_client(cls, timeout: float) -> _httpx.AsyncClient:
        """Returns a singleton AsyncClient with optimized limits for HFT."""
        if getattr(cls, "_shared_client", None) is None or cls._shared_client.is_closed:
            limits = _httpx.Limits(max_keepalive_connections=50, max_connections=100)
            cls._shared_client = _httpx.AsyncClient(timeout=timeout, limits=limits)
        return cls._shared_client

    @classmethod
    async def aclose_shared_client(cls) -> None:
        """Gracefully closes the persistent HTTP connection pool."""
        if getattr(cls, "_shared_client", None) is not None and not cls._shared_client.is_closed:
            await cls._shared_client.aclose()
            cls._shared_client = None

    def __init__(self) -> None:
        self.settings = load_settings()

        # Use configured base URL or fallback to standard Polygon API
        raw_urls = getattr(self.settings, "massive_rest_base_urls", None)
        if raw_urls:
            # Pick the first one from CSV
            self.base_url = raw_urls.split(",")[0].strip().rstrip("/")
        else:
            self.base_url = _DEFAULT_MASSIVE_URL

    def _get_key(self, endpoint_type: str) -> str | None:
        """
        Retrieves the appropriate API key for the requested endpoint type.
        """
        s = self.settings
        key: str | None = None

        if endpoint_type == "options_primary":
            key = s.massive_key_options_primary
        elif endpoint_type == "options_secondary":
            key = s.massive_key_options_secondary
        elif endpoint_type == "distress":
            key = s.massive_key_distress
        elif endpoint_type == "macro":
            key = s.massive_key_macro

        # Fallback cascade
        if not key:
            # Try generic massive options key
            key = getattr(s, "massive_key_options", None)

        if not key:
            # Last resort: first key in the fallback list
            fallbacks = s.get_fallback_api_keys()
            key = fallbacks[0] if fallbacks else None

        return key

    async def _get(
        self, path: str, endpoint_type: str, params: dict[str, Any] | None = None
    ) -> Any:
        """
        Internal async GET requester.
        """
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx not installed — MassiveClient disabled.")
            return None

        key = self._get_key(endpoint_type)
        if not key:
            logger.debug("MassiveClient: No API key available for %s.", endpoint_type)
            return None

        url = self.base_url + path
        merged: dict[str, Any] = {"apiKey": key}
        if params:
            merged.update(params)

        try:
            client = self.get_shared_client(_DEFAULT_TIMEOUT)
            res = await client.get(url, params=merged)
            if res.status_code == 200:
                return res.json()

            logger.debug(
                "Massive request failed: %s (status=%d, type=%s)",
                path,
                res.status_code,
                endpoint_type,
            )
        except Exception as exc:
            logger.debug("Massive request error for %s: %s", path, exc)

        return None

    async def get_equity_last_price(
        self, ticker: str, endpoint_type: str = "options_primary"
    ) -> float | None:
        """Return the latest equity spot from Polygon/Massive snapshot (day close or prev close)."""
        data = await self._get(
            f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker.upper()}",
            endpoint_type,
        )
        if not isinstance(data, dict) or data.get("status") != "OK":
            return None
        ticker_data = data.get("ticker")
        if not isinstance(ticker_data, dict):
            return None
        day_data = ticker_data.get("day") or {}
        price = day_data.get("c")
        if price is None and isinstance(ticker_data.get("prevDay"), dict):
            price = ticker_data["prevDay"].get("c")
        try:
            return float(price) if price is not None else None
        except (TypeError, ValueError):
            return None

    async def get_options_chain(
        self, ticker: str, endpoint_type: str = "options_primary"
    ) -> list[dict[str, Any]] | None:
        """
        Fetch real-time options chain for a ticker using Massive Advanced keys.
        Returns the raw 'results' list from the Polygon v3 snapshot.
        """
        sym = ticker.upper().strip()
        try:
            from backend.hub.market_data_ttl_cache import (
                get_massive_options_chain,
                put_massive_options_chain,
            )

            cached = get_massive_options_chain(sym)
            if cached is not None:
                shaped, _src, _meta = cached
                if shaped and isinstance(shaped.get("data"), list):
                    return shaped["data"]
        except Exception as exc:
            logger.debug("massive_client.options_cache_read_failed sym=%s error=%s", sym, exc)

        data = await self._get(f"/v3/snapshot/options/{sym}", endpoint_type)
        if data and "results" in data:
            try:
                from backend.hub.market_data_ttl_cache import put_massive_options_chain

                put_massive_options_chain(sym, ({"data": data["results"]}, "massive_client", {}))
            except Exception as exc:
                logger.debug("massive_client.options_cache_write_failed sym=%s error=%s", sym, exc)
            return data["results"]
        return None

    async def get_macro_context(self) -> dict[str, Any] | None:
        """
        Fetch institutional macro indicators.
        """
        # Endpoint can be custom or passthrough to regular macro aggregates
        data = await self._get("/v1/macro/status", "macro")
        return data if data else None

    async def get_distress_sentiment(self, ticker: str) -> dict[str, Any] | None:
        """
        Fetch short interest, float distribution, and distress aggregates.
        """
        data = await self._get(f"/v1/distress/{ticker.upper()}", "distress")
        return data if data else None


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : massive_client.py
# Sub-capa         : Fetchers (Institutional)
# Enfoque          : Cliente para endpoints masivos de Polygon/Massive.
# Eliminado        : QuantumBetaSettings (legacy), imports de config v1.
# Preservado       : Lógica de rotación de claves, endpoints institucionales.
# Integración      : Utiliza backend.config.settings (load_settings).
# ─────────────────────────────────────────────────────────────────────
