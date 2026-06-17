"""Binance REST client — QuantumAnalyzer Layer 1.

Async, typed client for Binance Spot and USD-M Futures.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# ─── Endpoints ────────────────────────────────────────────────────────────────
BINANCE_SPOT_REST_BASE = "https://api.binance.com"
BINANCE_SPOT_REST_TESTNET = "https://testnet.binance.vision"

BINANCE_FUTURES_REST_BASE = "https://fapi.binance.com"
BINANCE_FUTURES_REST_TESTNET = "https://testnet.binancefuture.com"

VALID_KLINE_INTERVAL = Literal[
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"
]

_DEFAULT_TIMEOUT_SECONDS = 30.0
_HTTP_MAX_KEEPALIVE = 16
_HTTP_MAX_CONNECTIONS = 48
_HTTP_POOL_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class BinanceKline:
    """Single OHLCV bar."""

    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time_ms: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "open_time_ms": self.open_time_ms,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "close_time_ms": self.close_time_ms,
        }


class BinanceClient:
    """Async REST client for Binance with built-in dry-run safety net."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        *,
        dry_run: bool = True,
        allow_env_dry_run_override: bool = True,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        trading_env = os.getenv("BINANCE_BOT_TRADING_ENV", "paper")

        if trading_env == "paper" and allow_env_dry_run_override:
            self._spot_base_url = BINANCE_SPOT_REST_TESTNET
            self._futures_base_url = BINANCE_FUTURES_REST_TESTNET
            dry_run = False
            allow_env_dry_run_override = False
        else:
            self._spot_base_url = BINANCE_SPOT_REST_BASE
            self._futures_base_url = BINANCE_FUTURES_REST_BASE

        env_dry_run = os.getenv("BINANCE_BOT_PAPER_TRADING")
        if allow_env_dry_run_override and env_dry_run is not None:
            dry_run = env_dry_run.strip().lower() not in {"0", "false", "no", "live"}

        self._api_key: str | None = api_key or os.getenv("BINANCE_API_KEY")
        self._secret_key: str | None = secret_key or os.getenv("BINANCE_API_SECRET")
        self._dry_run: bool = bool(dry_run)
        self._timeout: float = float(timeout_seconds)
        self._client: httpx.AsyncClient | None = None

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def spot_base_url(self) -> str:
        return self._spot_base_url

    @property
    def futures_base_url(self) -> str:
        return self._futures_base_url

    @property
    def trading_environment(self) -> str:
        if self._dry_run:
            return "paper"
        if self._spot_base_url == BINANCE_SPOT_REST_TESTNET:
            return "prod-vst"
        return "prod-live"

    async def __aenter__(self) -> BinanceClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            timeout = httpx.Timeout(
                self._timeout,
                pool=_HTTP_POOL_TIMEOUT_SECONDS,
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(
                    max_keepalive_connections=_HTTP_MAX_KEEPALIVE,
                    max_connections=_HTTP_MAX_CONNECTIONS,
                ),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _public_request(
        self, method: str, base_url: str, endpoint: str, params: dict[str, Any]
    ) -> dict[str, Any] | list[Any]:
        client = await self._ensure_client()
        url = f"{base_url}{endpoint}"
        try:
            resp = await client.request(method, url, params=params)
            resp.raise_for_status()
            return resp.json()  # type: ignore
        except httpx.HTTPStatusError as e:
            logger.error(
                "binance_client.http_error endpoint=%s status=%d body=%s",
                endpoint,
                e.response.status_code,
                e.response.text,
            )
            raise
        except Exception as e:
            logger.error("binance_client.request_failed endpoint=%s error=%s", endpoint, e)
            raise

    # ── Public Market Data ──────────────────────────────────────────────────
    async def fetch_klines(
        self,
        symbol: str,
        interval: VALID_KLINE_INTERVAL = "5m",
        limit: int = 500,
    ) -> list[BinanceKline]:
        """Fetch Spot K-lines."""
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        raw_klines = await self._public_request(
            "GET", self._spot_base_url, "/api/v3/klines", params
        )
        if not isinstance(raw_klines, list):
            return []

        result = []
        for row in raw_klines:
            result.append(
                BinanceKline(
                    open_time_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    close_time_ms=int(row[6]),
                )
            )
        return result

    async def fetch_klines_perp(
        self,
        symbol: str,
        interval: VALID_KLINE_INTERVAL = "5m",
        limit: int = 500,
    ) -> list[BinanceKline]:
        """Fetch USD-M Futures K-lines."""
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        raw_klines = await self._public_request(
            "GET", self._futures_base_url, "/fapi/v1/klines", params
        )
        if not isinstance(raw_klines, list):
            return []

        result = []
        for row in raw_klines:
            result.append(
                BinanceKline(
                    open_time_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    close_time_ms=int(row[6]),
                )
            )
        return result
