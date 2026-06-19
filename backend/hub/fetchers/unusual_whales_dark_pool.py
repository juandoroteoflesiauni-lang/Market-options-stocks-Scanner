"""Unusual Whales dark-pool fetcher (Motor ⑭). # [PD-1][PD-3][TH]

Network helper for the ``MarketDataHub`` only — never imported by Phase B/C or
``decide()`` (PD-3). The API key is injected by the caller (PD-1: read from
``.env`` / settings, never hardcoded). The fetcher raises ``ValueError`` on a
missing key so the Hub can surface a clean ``Result.failure`` instead of a raw
exception.

Endpoint (Unusual Whales API v1):
    GET https://api.unusualwhales.com/api/darkpool/{ticker}
    → recent dark-pool prints for the ticker (trailing window). Auth via
      ``Authorization: Bearer <api_key>`` header.

An optional FMP fallback is provided for environments without a UW key; it hits
FMP's institutional block-trade-style endpoint and returns the raw payload for
the same normalizer to aggregate.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.config.logger_setup import get_logger
from backend.hub.rate_limiter import rate_limiter

logger = get_logger(__name__)

UW_DARKPOOL_URL = "https://api.unusualwhales.com/api/darkpool/{ticker}"
FMP_DARKPOOL_URL = "https://financialmodelingprep.com/api/v4/dark-pool/{ticker}"

_TIMEOUT_SECONDS = 10.0


async def fetch_uw_dark_pool_prints(
    client: httpx.AsyncClient,
    api_key: str,
    symbol: str,
) -> dict[str, Any]:
    """Fetch recent dark-pool prints from Unusual Whales for ``symbol``.

    Raises:
        ValueError: ``uw_api_key_missing`` when no key is supplied, or
            ``uw_dark_pool_invalid_format`` when the payload is unusable.
    """
    if not api_key or not api_key.strip():
        raise ValueError("uw_api_key_missing")

    await rate_limiter.acquire("unusual_whales")

    url = UW_DARKPOOL_URL.format(ticker=symbol.upper())
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    response = await client.get(url, headers=headers, timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, list):
        return {"data": data}
    if isinstance(data, dict):
        return data
    raise ValueError("uw_dark_pool_invalid_format")


async def fetch_fmp_dark_pool_prints(
    client: httpx.AsyncClient,
    api_key: str,
    symbol: str,
) -> dict[str, Any]:
    """FMP fallback dark-pool fetch (best-effort, optional).

    Raises:
        ValueError: ``fmp_api_key_missing`` when no key is supplied, or
            ``fmp_dark_pool_invalid_format`` when the payload is unusable.
    """
    if not api_key or not api_key.strip():
        raise ValueError("fmp_api_key_missing")

    await rate_limiter.acquire("fmp")

    url = FMP_DARKPOOL_URL.format(ticker=symbol.upper())
    params = {"apikey": api_key}
    response = await client.get(url, params=params, timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, list):
        return {"data": data}
    if isinstance(data, dict):
        return data
    raise ValueError("fmp_dark_pool_invalid_format")


__all__ = [
    "FMP_DARKPOOL_URL",
    "UW_DARKPOOL_URL",
    "fetch_fmp_dark_pool_prints",
    "fetch_uw_dark_pool_prints",
]
