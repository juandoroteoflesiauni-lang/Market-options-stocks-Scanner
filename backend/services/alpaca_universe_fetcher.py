"""Universo extendido de Alpaca (autónomo). # [PD-1][IM][TH]

Carga los tickers de acciones de mayor volumen desde Alpaca Market Data v2.
Es 100% independiente: no cruza ni hereda estado de otros módulos de trading.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.config.logger_setup import get_logger
from backend.models.market_snapshot import UniverseType

logger = get_logger(__name__)

_ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"
_SNAPSHOTS_URL = "https://data.alpaca.markets/v2/stocks/snapshots"
_MAX_SYMBOLS = 1000
_ASSETS_TIMEOUT = 15.0
_SNAPSHOTS_TIMEOUT = 20.0

# Cache global del universo extendido (poblado asíncronamente en bootstrap).
ALPACA_EXTENDED_CACHE: list[str] = []
TICKER_UNIVERSE_MAP: dict[str, UniverseType] = {}


def get_universe_type_for_ticker(ticker: str) -> UniverseType:
    return TICKER_UNIVERSE_MAP.get(ticker.upper(), UniverseType.ALPACA_EXTENDED)


def _headers(api_key: str, api_secret: str) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "Accept": "application/json",
    }


async def _fetch_tradable_symbols(client: httpx.AsyncClient, headers: dict[str, str]) -> list[str]:
    resp = await client.get(
        f"{_ASSETS_URL}?status=active&asset_class=us_equity",
        headers=headers,
        timeout=_ASSETS_TIMEOUT,
    )
    if resp.status_code != 200:
        logger.error("alpaca_universe.assets_failed status=%s", resp.status_code)
        return []
    return [a["symbol"] for a in resp.json() if a.get("tradable")][:_MAX_SYMBOLS]


def _rank_by_volume(snapshots: dict[str, dict[str, Any]], limit: int) -> list[str]:
    vol_data: list[tuple[str, float]] = []
    for sym, snap in snapshots.items():
        prev_bar = snap.get("prevDailyBar") or {}
        vol_data.append((sym, float(prev_bar.get("v", 0) or 0)))
    vol_data.sort(key=lambda x: x[1], reverse=True)
    return [sym for sym, _ in vol_data[:limit]]


async def fetch_alpaca_top_volume(
    api_key: str, api_secret: str, limit: int = _MAX_SYMBOLS
) -> list[str]:
    """Tickers con mayor volumen del día anterior (snapshot diario)."""
    headers = _headers(api_key, api_secret)
    try:
        async with httpx.AsyncClient() as client:
            symbols = await _fetch_tradable_symbols(client, headers)
            if not symbols:
                return []
            resp = await client.get(
                f"{_SNAPSHOTS_URL}?symbols={','.join(symbols)}",
                headers=headers,
                timeout=_SNAPSHOTS_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.error("alpaca_universe.snapshots_failed status=%s", resp.status_code)
                return []
            data = resp.json()
            snapshots = data.get("snapshots", data) if isinstance(data, dict) else {}
            return _rank_by_volume(snapshots, limit)
    except Exception as exc:
        logger.error("alpaca_universe.fetch_error %s", exc, exc_info=True)
        return []


async def ensure_alpaca_universe_loaded(api_key: str, api_secret: str) -> list[str]:
    """Carga el universo extendido en cache sin bloquear el arranque."""
    tickers = await fetch_alpaca_top_volume(api_key, api_secret)
    if not tickers:
        logger.warning("alpaca_universe.empty no tickers loaded")
        return []
    ALPACA_EXTENDED_CACHE.clear()
    ALPACA_EXTENDED_CACHE.extend(tickers)
    TICKER_UNIVERSE_MAP.clear()
    for ticker in tickers:
        TICKER_UNIVERSE_MAP[ticker] = UniverseType.ALPACA_EXTENDED
    logger.info("alpaca_universe.loaded count=%d", len(tickers))
    return tickers
