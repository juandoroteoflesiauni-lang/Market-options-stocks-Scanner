"""Macro data snapshot provider for probabilistic regime priors."""

from __future__ import annotations

import os
import time

import httpx

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 3600
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"

FALLBACK_MACRO_DATA: dict[str, float | str] = {
    "vix_spot": 20.0,
    "vix_3m": 21.0,
    "hy_spread": 350.0,
    "ig_spread": 120.0,
    "yield_2y": 4.5,
    "yield_10y": 4.3,
    "sp500_200ma_pct": 2.0,
    "_source": "fallback",
}

_CACHE: dict[str, object] = {
    "timestamp": 0.0,
    "data": None,
}


def _copy_payload(payload: dict[str, float | str]) -> dict[str, float | str]:
    return dict(payload)


def _cached_payload(now: float) -> dict[str, float | str] | None:
    cached_data = _CACHE.get("data")
    cached_at = _CACHE.get("timestamp")
    if not isinstance(cached_data, dict) or not isinstance(cached_at, int | float):
        return None
    if now - float(cached_at) >= CACHE_TTL_SECONDS:
        return None
    return dict(cached_data)


def _store_cache(payload: dict[str, float | str], now: float) -> dict[str, float | str]:
    _CACHE["timestamp"] = now
    _CACHE["data"] = _copy_payload(payload)
    return payload


def _finnhub_api_key() -> str | None:
    raw = os.getenv("FINNHUB_API_KEY")
    if raw is None:
        return None
    key = raw.strip()
    return key or None


def _valid_price(value: object) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return price


def _fetch_finnhub_vix(api_key: str) -> float | None:
    for symbol in ("^VIX", "VIX", "CBOE:VIX"):
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    FINNHUB_QUOTE_URL,
                    params={"symbol": symbol, "token": api_key},
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning("Finnhub VIX quote failed for %s: %s", symbol, exc)
            continue

        price = _valid_price(payload.get("c") if isinstance(payload, dict) else None)
        if price is not None:
            return price

        logger.warning("Finnhub VIX quote returned no valid price for %s.", symbol)
    return None


def _fetch_yfinance_vix() -> float | None:
    try:
        import yfinance as yf

        fast_info = yf.Ticker("^VIX").fast_info
        return _valid_price(fast_info.get("lastPrice"))
    except Exception as exc:
        logger.warning("yfinance VIX fallback failed: %s", exc)
        return None


def _payload_with_vix(vix_spot: float, source: str) -> dict[str, float | str]:
    payload = _copy_payload(FALLBACK_MACRO_DATA)
    payload["vix_spot"] = float(vix_spot)
    payload["_source"] = source
    return payload


def get_macro_data() -> dict[str, float | str]:
    """Return a valid macro snapshot, using live VIX when available."""
    now = time.time()
    cached = _cached_payload(now)
    if cached is not None:
        return cached

    api_key = _finnhub_api_key()
    if api_key is not None:
        vix_spot = _fetch_finnhub_vix(api_key)
        if vix_spot is not None:
            return _store_cache(_payload_with_vix(vix_spot, "finnhub"), now)

        vix_spot = _fetch_yfinance_vix()
        if vix_spot is not None:
            return _store_cache(_payload_with_vix(vix_spot, "yfinance"), now)

    return _store_cache(_copy_payload(FALLBACK_MACRO_DATA), now)


def get_vix_spot() -> float:
    """Return the VIX spot level from the macro snapshot."""
    value = get_macro_data().get("vix_spot", 20.0)
    try:
        vix_spot = float(value)
    except (TypeError, ValueError):
        return 20.0
    return vix_spot if vix_spot > 0 else 20.0


async def fetch_macro_snapshot() -> dict[str, float | str]:
    """Async wrapper consumed by probabilistic_router."""
    return get_macro_data()


if __name__ == "__main__":
    logger.info("macro_data=%s", get_macro_data())
