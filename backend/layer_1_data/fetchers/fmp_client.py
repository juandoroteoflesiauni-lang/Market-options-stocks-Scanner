"""Async client para Financial Modeling Prep (FMP)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger("backend.layer_1_data.datos.fmp_client")

_FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"
_DEFAULT_TIMEOUT = 15.0  # seconds
_FMP_SHARED_CACHE: dict[str, dict[str, Any]] = {}
_FMP_SHARED_INFLIGHT: dict[str, asyncio.Task[Any]] = {}

# ── httpx lazy import ─────────────────────────────────────────────────────────
try:
    import httpx as _httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _httpx = None  # type: ignore[assignment]
    _HTTPX_AVAILABLE = False
    logger.warning("httpx not installed — FMPClient disabled. Install: pip install httpx")



def _get_fmp_keys() -> dict[str, str]:
    """Retrieve all FMP keys from environment variables."""
    return {
        "QUOTES": os.getenv("FMP_KEY_QUOTES", "").strip(),
        "STATEMENTS": os.getenv("FMP_KEY_STATEMENTS", "").strip(),
        "NEWS": os.getenv("FMP_KEY_NEWS", "").strip(),
        "13F": os.getenv("FMP_KEY_13F", "").strip(),
        "ANALYST": os.getenv("FMP_KEY_ANALYST", "").strip(),
        "MARKET": os.getenv("FMP_KEY_MARKET", "").strip(),
        "MACRO": os.getenv("FMP_KEY_MACRO", "").strip(),
        "CALENDARS": os.getenv("FMP_KEY_CALENDARS", "").strip(),
        "TRANSCRIPTS": os.getenv("FMP_KEY_TRANSCRIPTS", "").strip(),
        "ETF": os.getenv("FMP_KEY_ETF", "").strip(),
        "FILINGS": os.getenv("FMP_KEY_FILINGS", "").strip(),
        "TECHNICAL": os.getenv("FMP_KEY_TECHNICAL", "").strip(),
        "PROFILES": os.getenv("FMP_KEY_PROFILES", "").strip(),
    }


from backend.layer_1_data.fetchers.fmp_mixins.fmp_calendars import FMPCalendarsMixin
from backend.layer_1_data.fetchers.fmp_mixins.fmp_macro import FMPMacroMixin
from backend.layer_1_data.fetchers.fmp_mixins.fmp_news import FMPNewsMixin
from backend.layer_1_data.fetchers.fmp_mixins.fmp_profiles import FMPProfilesMixin
from backend.layer_1_data.fetchers.fmp_mixins.fmp_quotes import FMPQuotesMixin
from backend.layer_1_data.fetchers.fmp_mixins.fmp_statements import FMPStatementsMixin
from backend.layer_1_data.fetchers.fmp_mixins.fmp_technical import FMPTechnicalMixin


class FMPClient(
    FMPStatementsMixin,
    FMPTechnicalMixin,
    FMPNewsMixin,
    FMPMacroMixin,
    FMPCalendarsMixin,
    FMPProfilesMixin,
    FMPQuotesMixin,
):
    """
    Async Financial Modeling Prep REST client.
    Uses 13 dedicated API keys per module according to FMP_Integration_Master_Prompt rules.
    Implements internal Stale-While-Revalidate caching per endpoint.
    """

    _shared_client: _httpx.AsyncClient | None = None

    @classmethod
    def get_shared_client(cls, timeout: float) -> _httpx.AsyncClient:
        """Returns a singleton AsyncClient with optimized limits for HFT."""
        if getattr(cls, "_shared_client", None) is None or cls._shared_client.is_closed:
            limits = _httpx.Limits(max_keepalive_connections=100, max_connections=200)
            cls._shared_client = _httpx.AsyncClient(timeout=timeout, limits=limits)
        return cls._shared_client

    @classmethod
    async def aclose_shared_client(cls) -> None:
        """Gracefully closes the persistent HTTP connection pool."""
        if getattr(cls, "_shared_client", None) is not None and not cls._shared_client.is_closed:
            await cls._shared_client.aclose()
            cls._shared_client = None

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._keys = _get_fmp_keys()
        self._timeout = timeout
        self._cache = _FMP_SHARED_CACHE

        # Determine if at least one key is active to enable client
        self._is_active_flag = any(self._keys.values()) and _HTTPX_AVAILABLE
        if not self._is_active_flag:
            logger.warning("FMPClient: No keys configured. Operations will be skipped.")

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _is_active(self) -> bool:
        return self._is_active_flag

    def _get_key_for_module(self, module: str) -> str:
        key = self._keys.get(module, "")
        if not key:
            # Fallback to QUOTES then to STATEMENTS as a safer primary default
            key = self._keys.get("QUOTES", "")
            if not key:
                key = self._keys.get("STATEMENTS", "")
        return key

    async def _get(
        self, path: str, module: str, params: dict[str, Any] | None = None, ttl_secs: float = 60.0
    ) -> Any:
        if not self._is_active():
            return None

        key = self._get_key_for_module(module)
        if not key:
            logger.warning(f"No key available for module {module} and no fallback.")
            return None

        # Build cache key
        safe_params = {k: v for k, v in (params or {}).items() if v is not None}
        cache_key = f"{module}:{path}:{json.dumps(safe_params, sort_keys=True)}"

        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached:
            age = now - cached["timestamp"]
            if age < cached["ttl_secs"]:
                logger.debug(
                    f"[FMP CACHE HIT] {cache_key} (ttl_remaining: {cached['ttl_secs'] - age:.1f}s)"
                )
                return cached["data"]

        inflight = _FMP_SHARED_INFLIGHT.get(cache_key)
        if inflight is not None and not inflight.done():
            return await asyncio.shield(inflight)

        task = asyncio.create_task(
            self._get_uncached(path=path, module=module, params=safe_params, key=key)
        )
        _FMP_SHARED_INFLIGHT[cache_key] = task
        try:
            data = await asyncio.shield(task)
            if data is not None:
                self._cache[cache_key] = {
                    "data": data,
                    "timestamp": time.monotonic(),
                    "ttl_secs": ttl_secs,
                }
            return data
        finally:
            _FMP_SHARED_INFLIGHT.pop(cache_key, None)

    async def _get_uncached(
        self,
        *,
        path: str,
        module: str,
        params: dict[str, Any],
        key: str,
    ) -> Any:
        merged = {"apikey": key}
        merged.update(params)

        base = "https://financialmodelingprep.com/api" if path.startswith("/v4") else _FMP_BASE_URL
        url = base + path

        max_retries = 3
        backoff = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                client = self.get_shared_client(self._timeout)
                resp = await client.get(url, params=merged)

                if resp.status_code == 429:
                    logger.warning(
                        f"FMP Rate Limit 429 for {module}. Reintentando en {backoff}s..."
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue

                if resp.status_code in {500, 502, 503, 504}:
                    logger.warning(
                        f"FMP Server Error {resp.status_code} for {module}. Reintentando ({attempt}/{max_retries})..."
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue

                if resp.status_code != 200:
                    logger.warning(
                        f"FMP {module} request failed: {path} (status={resp.status_code})"
                    )
                    return None

                return resp.json()
            except Exception as exc:
                logger.warning(f"FMP request error for {path}: {exc}")
                await asyncio.sleep(backoff)

        return None

    async def _get_stable(
        self,
        path: str,
        module: str,
        params: dict[str, Any] | None = None,
        ttl_secs: float = 60.0,
    ) -> Any:
        """
        FMP «stable» host (documented for segmentation, institutional summary, etc.).

        Base: https://financialmodelingprep.com/stable
        """
        if not self._is_active():
            return None

        key = self._get_key_for_module(module)
        if not key:
            logger.warning("No key available for module %s and no fallback.", module)
            return None

        safe_params = {k: v for k, v in (params or {}).items() if v is not None}
        cache_key = f"STABLE:{module}:{path}:{json.dumps(safe_params, sort_keys=True)}"

        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached:
            age = now - cached["timestamp"]
            if age < cached["ttl_secs"]:
                logger.debug(
                    "[FMP STABLE CACHE HIT] %s (ttl_remaining: %.1fs)",
                    cache_key,
                    cached["ttl_secs"] - age,
                )
                return cached["data"]

        merged: dict[str, Any] = {"apikey": key}
        merged.update(safe_params)
        p = path if path.startswith("/") else f"/{path}"
        url = f"https://financialmodelingprep.com/stable{p}"

        max_retries = 3
        backoff = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                client = self.get_shared_client(self._timeout)
                resp = await client.get(url, params=merged)

                if resp.status_code == 429:
                    logger.warning(
                        "FMP stable Rate Limit 429 for %s. Reintentando en %.1fs...",
                        module,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue

                if resp.status_code in {500, 502, 503, 504}:
                    logger.warning(
                        "FMP stable Server Error %s for %s (%s/%s)...",
                        resp.status_code,
                        module,
                        attempt,
                        max_retries,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue

                if resp.status_code != 200:
                    logger.warning(
                        "FMP stable request failed: %s (status=%s)", path, resp.status_code
                    )
                    return None

                data = resp.json()
                self._cache[cache_key] = {
                    "data": data,
                    "timestamp": time.monotonic(),
                    "ttl_secs": ttl_secs,
                }
                return data
            except Exception as exc:
                logger.warning("FMP stable request error for %s: %s", path, exc)
                await asyncio.sleep(backoff)

        return None

    def _parse_list(self, data: Any, model: type) -> list:
        """
        Parse a JSON array into a list of Pydantic model instances.

        Returns empty list on None, non-list, or parse errors.
        """
        if not isinstance(data, list) or not data:
            return []
        results = []
        for item in data:
            try:
                results.append(model(**item))
            except Exception as exc:
                logger.debug("FMP parse error for %s: %s", model.__name__, exc)
        return results

    # ──────────────────────────────────────────────────────────────────────────
    # §1  FUNDAMENTAL STATEMENTS
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §2  KEY METRICS & ENTERPRISE VALUE
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §3  VALUATION & RATING
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §4  REAL-TIME QUOTE
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §5  TECHNICAL INDICATORS
    # ──────────────────────────────────────────────────────────────────────────

    def _namespace_rows_by_symbol(self, data: Any) -> dict[str, SimpleNamespace]:
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        result: dict[str, SimpleNamespace] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            raw_symbol = item.get("symbol") or item.get("ticker")
            if not raw_symbol:
                continue
            result[str(raw_symbol).upper()] = SimpleNamespace(**item)
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # §6  NEWS
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §7  INSTITUTIONAL OWNERSHIP
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §8  CALENDARS
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §9  COMPANY PROFILE
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §10  RATIOS TTM & ANNUAL
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §11  ANALYST ESTIMATES & RECOMMENDATIONS
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §12  TRANSCRIPTS
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §13  HISTORICAL PRICES
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §14  EARNINGS SURPRISES
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §15  SMA INDICATORS (for Golden/Death Cross)
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §16  COMPOSITE ENRICHMENT (DataLake integration helper)
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §17  FULL FUNDAMENTAL ANALYSIS (13-module massive fetch)
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §18 INSTITUTIONAL SHORT METRICS (Phase 2)
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §19 EXPANSION MODULES (v4)
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §20 INSTITUTIONAL MASTER CLASS (Phase 4)
    # ──────────────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────────────
    # §21 COMPETITIVE EDGE & FORENSIC MASTERY (Phase 5)
    # ──────────────────────────────────────────────────────────────────────────


def fmp_client_configured() -> bool:
    """True when at least one FMP API key is available for fundamentals/macro modules."""
    return FMPClient()._is_active()


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: fmp_client.py
# Eliminado: encabezado previo e imports de config/modelos del sistema anterior
# Preservado: contratos públicos del cliente async, endpoints, parseo pydantic y lógica de cache/reintentos
# Pendientes: ninguno
# ─────────────────────────────────────────────────
