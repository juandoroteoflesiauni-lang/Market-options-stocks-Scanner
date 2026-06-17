"""BingX REST + WebSocket client — QuantumAnalyzer Layer 1.

Async, typed client for the BingX exchange covering:

* Public market data: K-lines (candles), ticker, order book (REST).
* Public WebSocket: K-line stream subscriptions (gzip-compressed JSON frames).
* Private endpoints (HMAC-SHA256 signed): account balance, place/cancel orders.

The client supports two execution modes via the ``dry_run`` flag:

* ``dry_run=True`` (default for safety): order placement and cancellation never
  hit the exchange — they are intercepted, logged, and a simulated response is
  returned. All read-only public endpoints still execute normally.
* ``dry_run=False``: signed requests are sent to BingX. Requires ``api_key`` and
  ``secret_key``.

This module belongs to Layer 1 (data ingestion / venue I/O). It must not import
from Layer 2+. Cross-cutting infra (logger) is imported via
``backend.config.logger_setup``.

References:
    https://bingx-api.github.io/docs-v3/#/en/info
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

try:  # pragma: no cover - import shim for non-package execution.
    import websockets
    from websockets.client import ClientConnection
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover - websockets is in requirements.txt
    websockets = None  # type: ignore[assignment]
    ClientConnection = Any  # type: ignore[assignment,misc]
    ConnectionClosed = Exception  # type: ignore[assignment,misc]

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# ─── Endpoints ────────────────────────────────────────────────────────────────
BINGX_REST_BASE = "https://open-api.bingx.com"
BINGX_REST_VST_BASE = "https://open-api-vst.bingx.com"
BINGX_WS_MARKET_URL = "wss://open-api-ws.bingx.com/market"
BINGX_DEFAULT_SOURCE_KEY = "BX-AI-SKILL"

VALID_KLINE_INTERVAL = Literal[
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "3d", "1w", "1M"
]

# Conservative defaults appropriate for a 10-USDT micro account.
_DEFAULT_TIMEOUT_SECONDS = 30.0  # Aumentado a 30s para soportar VST/Demo y gather concurrente
_DEFAULT_MAX_KLINES = 500
_SPOT_KLINE_PAGE_LIMIT = 1_000
_PERP_KLINE_PAGE_LIMIT = 1_440
_DEFAULT_DEPTH_LIMIT = 20
_HTTP_MAX_KEEPALIVE = 16
_HTTP_MAX_CONNECTIONS = 48
_HTTP_POOL_TIMEOUT_SECONDS = 30.0

# Symbol roots that live on the swap/perpetuals endpoint, not spot.
# BingX lists US synthetic stock perpetuals here; update as new instruments launch.
_SYNTHETIC_STOCK_ROOTS: frozenset[str] = frozenset(
    {
        "AAPL",
        "AVGO",
        "CSCO",
        "MSFT",
        "AMZN",
        "GOOGL",
        "GOOG",
        "META",
        "TSLA",
        "NVDA",
        "NFLX",
        "BA",
        "JPM",
        "JNJ",
        "WMT",
        "V",
        "MA",
        "UNH",
        "BAC",
        "GS",
        "AMD",
        "INTC",
        "DIS",
        "PYPL",
        "SQ",
        "UBER",
        "LYFT",
        "SNAP",
        "HOOD",
        "PLTR",
        "COIN",
        "IREN",
        "CRWV",
        "AMC",
        "GME",
        "RBLX",
        "ROKU",
        "TWLO",
        "ZM",
        "SHOP",
        "NET",
        "DDOG",
        "SNOW",
        "MCD",
        "KO",
        "PEP",
        "T",
        "VZ",
        "XOM",
        "CVX",
        "BABA",
        "NIO",
        "XPEV",
        "LI",
        "SPX",
        "SPY",
        "QQQ",
        "NDX",
        "DJI",
        "IWM",
        "VIX",
        "NQ",
        "ES",
        "YM",
        "RTY",
        "US30",
        "US500",
        "US100",
    }
)


def is_perp_symbol(symbol: str) -> bool:
    """Return True if ``symbol`` routes to BingX's swap/perpetuals endpoint.

    Checks the root (prefix before ``-`` or ``/``) against the known set of
    synthetic stock perpetuals. Regular crypto (BTC, ETH, SOL …) returns False.
    """
    root = symbol.split("-")[0].split("/")[0].upper()
    return root in _SYNTHETIC_STOCK_ROOTS


@dataclass(frozen=True)
class BingXKline:
    """Single OHLCV bar normalised to a typed contract."""

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


@dataclass(frozen=True)
class BingXOrderRequest:
    """Typed order intent passed to ``place_order``."""

    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    quantity: float | None = None
    quote_order_qty: float | None = None
    price: float | None = None
    time_in_force: Literal["GTC", "IOC", "FOK"] | None = None
    client_order_id: str | None = None


@dataclass(frozen=True)
class BingXPerpOrderRequest:
    """Typed order intent for BingX perpetual futures (swap endpoint).

    ``stop_loss_price`` / ``take_profit_price``: optional protection prices
    sent atomically with the entry order. BingX accepts these as JSON-encoded
    ``STOP_MARKET`` / ``TAKE_PROFIT_MARKET`` sub-orders attached to the
    parent — when set, the venue creates the protection alongside the entry
    so a position is never naked on the book. Risk Desk should always set
    these for live equity perp orders.
    """

    symbol: str
    side: Literal["BUY", "SELL"]
    position_side: Literal["LONG", "SHORT", "BOTH"] = "BOTH"
    order_type: str = "MARKET"
    quantity: float | None = None
    price: float | None = None
    stop_price: float | None = None
    time_in_force: Literal["GTC", "IOC", "FOK", "PostOnly"] | None = None
    client_order_id: str | None = None
    reduce_only: bool = False
    stop_loss_price: float | None = None
    take_profit_price: float | None = None


def _bingx_omit_reduce_only() -> bool:
    """When True, skip ``reduceOnly`` on closes (debug only — accumulates exposure)."""
    return os.getenv("BINGX_OMIT_REDUCE_ONLY", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_one_way_position_side(order: BingXPerpOrderRequest) -> Literal["LONG", "SHORT"]:
    """Resolve BingX one-way ``positionSide`` (LONG bull / SHORT bear).

    In one-way mode, ``BOTH`` is invalid — infer from ``side`` for entries and
    from the closing ``side`` for ``reduce_only`` exits.
    """
    ps = str(order.position_side).upper()
    if ps in {"LONG", "SHORT"}:
        return ps  # type: ignore[return-value]
    if order.reduce_only:
        return "LONG" if order.side.upper() == "SELL" else "SHORT"
    return "LONG" if order.side.upper() == "BUY" else "SHORT"


@dataclass(frozen=True)
class BingXContractMetadata:
    """Precision + sizing constraints for one BingX perp contract.

    Parsed from ``/openApi/swap/v2/quote/contracts``. Risk Desk uses these to
    round quantity/price to the venue's tick/lot grid before placing an
    order — sending an off-grid value triggers a venue reject and costs a
    round-trip on a tight micro-account.
    """

    display_name: str
    api_symbol: str
    tick_size: float  # price increment
    step_size: float  # quantity increment
    min_qty: float  # minimum order quantity
    min_notional: float  # minimum notional in quote currency
    max_leverage: int  # venue-imposed leverage ceiling
    quantity_precision: int  # decimal places for quantity (derived from step_size)
    price_precision: int  # decimal places for price (derived from tick_size)


@dataclass(frozen=True)
class BingXOrderResponse:
    """Result of an order placement attempt (live or dry-run)."""

    ok: bool
    dry_run: bool
    symbol: str
    side: str
    order_type: str
    requested_qty: float | None
    requested_quote_qty: float | None
    price: float | None
    venue_order_id: str | None
    client_order_id: str | None
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    fill_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "requested_qty": self.requested_qty,
            "requested_quote_qty": self.requested_quote_qty,
            "price": self.price,
            "fill_price": self.fill_price,
            "venue_order_id": self.venue_order_id,
            "client_order_id": self.client_order_id,
            "raw": dict(self.raw),
            "error": self.error,
        }


class BingXClient:
    """Async REST client for BingX with built-in dry-run safety net."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        *,
        base_url: str = BINGX_REST_BASE,
        dry_run: bool = True,
        allow_env_dry_run_override: bool = True,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        recv_window_ms: int = 5_000,
        source_key: str | None = None,
    ) -> None:
        trading_env = os.getenv("BINGX_BOT_TRADING_ENV")
        if trading_env == "paper" and allow_env_dry_run_override:
            base_url = BINGX_REST_VST_BASE
            dry_run = False
            allow_env_dry_run_override = False

        env_dry_run = os.getenv("BINGX_DRY_RUN")
        if allow_env_dry_run_override and env_dry_run is not None:
            dry_run = env_dry_run.strip().lower() not in {"0", "false", "no", "live"}
        self._api_key: str | None = api_key or os.getenv("BINGX_API_KEY")
        self._secret_key: str | None = secret_key or os.getenv("BINGX_SECRET")
        self._base_url: str = base_url.rstrip("/")
        self._dry_run: bool = bool(dry_run)
        self._timeout: float = float(timeout_seconds)
        self._recv_window_ms: int = int(recv_window_ms)
        resolved_source = (
            source_key or os.getenv("BINGX_SOURCE_KEY") or BINGX_DEFAULT_SOURCE_KEY
        ).strip()
        self._source_key: str = resolved_source or BINGX_DEFAULT_SOURCE_KEY
        self._client: httpx.AsyncClient | None = None
        # Lazy cache: displayName ("AAPL-USDT") → internal API symbol ("NCSKAAPL2USD-USDT").
        self._perp_symbol_map: dict[str, str] | None = None
        self._perp_symbol_map_lock = asyncio.Lock()
        self._contract_metadata_cache: dict[str, BingXContractMetadata] | None = None
        self._contract_metadata_lock = asyncio.Lock()

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def trading_environment(self) -> str:
        if self._dry_run:
            return "paper"
        if self._base_url.rstrip("/") == BINGX_REST_VST_BASE:
            return "prod-vst"
        return "prod-live"

    async def __aenter__(self) -> BingXClient:
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
                base_url=self._base_url,
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

    # ── Public market data ────────────────────────────────────────────────────
    async def fetch_klines(
        self,
        symbol: str,
        interval: VALID_KLINE_INTERVAL = "5m",
        *,
        limit: int = _DEFAULT_MAX_KLINES,
    ) -> list[BingXKline]:
        """Return OHLCV K-lines for ``symbol`` on ``interval``.

        BingX spot endpoint: ``GET /openApi/spot/v2/market/kline``.
        Each row is an array: ``[openTime, open, high, low, close, volume, closeTime, ...]``.
        """
        return await self._fetch_kline_pages(
            "/openApi/spot/v2/market/kline",
            symbol=symbol.strip(),
            interval=interval,
            limit=limit,
            page_limit=_SPOT_KLINE_PAGE_LIMIT,
        )

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        """Return latest 24h ticker for ``symbol`` (spot)."""
        params = {"symbol": symbol.strip()}
        payload = await self._public_get("/openApi/spot/v1/ticker/24hr", params)
        return _unwrap_data(payload)

    async def fetch_order_book(
        self, symbol: str, *, limit: int = _DEFAULT_DEPTH_LIMIT, force_spot: bool = False
    ) -> dict[str, Any]:
        """Return aggregated order book (bids/asks) for ``symbol``.

        Auto-routes to the perpetual-swap depth endpoint for synthetic stock
        perp symbols (``is_perp_symbol``) and to the spot depth endpoint for
        everything else.  Use ``force_spot=True`` to bypass routing and always
        hit the spot depth endpoint (useful for fallback paths).

        Returns a structured dict with:

        .. code:: python

            {
                "symbol": "AAPL-USDT",
                "bids": [["150.25", "0.85"], ...],   # raw rows from venue
                "asks": [["150.35", "1.20"], ...],
                "parsed_bids": [(150.25, 0.85), ...],  # parsed (price, qty)
                "parsed_asks": [(150.35, 1.20), ...],
                "timestamp_ms": 1715000000000,
                "source": "bingx_perp_depth" | "bingx_spot_depth",
            }

        The raw rows are always included so callers can handle custom parsing.
        """
        sym = symbol.strip()
        limit_clamped = max(1, min(int(limit), 1_000))
        if not force_spot and is_perp_symbol(sym):
            api_symbol = await self._resolve_perp_symbol(sym)
            payload = await self._public_get(
                "/openApi/swap/v2/quote/depth",
                {"symbol": api_symbol, "limit": limit_clamped},
            )
            raw = _unwrap_data(payload)
            source = "bingx_perp_depth"
        else:
            payload = await self._public_get(
                "/openApi/spot/v1/market/depth",
                {"symbol": sym, "limit": limit_clamped},
            )
            raw = _unwrap_data(payload)
            source = "bingx_spot_depth"

        parsed_bids = _parse_depth_levels(raw.get("bids"))
        parsed_asks = _parse_depth_levels(raw.get("asks"))
        return {
            "symbol": sym,
            "bids": raw.get("bids", []),
            "asks": raw.get("asks", []),
            "parsed_bids": parsed_bids,
            "parsed_asks": parsed_asks,
            "timestamp_ms": int(time.time() * 1000),
            "source": source,
        }

    # ── Perpetual / swap market data ──────────────────────────────────────────
    async def fetch_perp_symbol_map(self) -> dict[str, str]:
        """Return a mapping of display name → internal API symbol for active perp contracts.

        Example: ``{"AAPL-USDT": "NCSKAAPL2USD-USDT", "BTC-USDT": "BTC-USDT", ...}``.
        Result is cached on the instance — called at most once per client lifetime.
        """
        payload = await self._public_get("/openApi/swap/v2/quote/contracts", {})
        contracts = payload.get("data", [])
        if not isinstance(contracts, list):
            return {}
        result: dict[str, str] = {}
        for c in contracts:
            display = str(c.get("displayName") or "")
            api_sym = str(c.get("symbol") or "")
            if display and api_sym and c.get("apiStateOpen") == "true":
                result[display] = api_sym
        logger.info("bingx_client.perp_symbol_map loaded contracts=%d", len(result))
        return result

    async def fetch_perp_contracts(self) -> list[dict[str, Any]]:
        """Return raw perpetual contract metadata from BingX."""
        payload = await self._public_get("/openApi/swap/v2/quote/contracts", {})
        return _extract_dict_list(payload)

    async def _resolve_perp_symbol(self, display_name: str) -> str:
        """Resolve a user-facing display name to the BingX internal API symbol.

        Fetches and caches the contracts map on first call. Falls back to the
        original name if not found (safe for native-format symbols like BTC-USDT).
        """
        if self._perp_symbol_map is None:
            async with self._perp_symbol_map_lock:
                if self._perp_symbol_map is None:
                    self._perp_symbol_map = await self.fetch_perp_symbol_map()
        resolved = self._perp_symbol_map.get(display_name, display_name)
        if resolved != display_name:
            logger.debug(
                "bingx_client.perp_symbol_resolved display=%s api=%s", display_name, resolved
            )
        return resolved

    async def fetch_contract_metadata(self, display_name: str) -> BingXContractMetadata:
        """Return precision + sizing constraints for a BingX perp contract.

        Uses a shared lazy cache (one HTTP round-trip per client lifetime). Raises
        ``KeyError`` if ``display_name`` is not found in the active contract list.
        Accepts display names (``AAPL-USDT``) and internal API symbols
        (``NCSKAAPL2USD-USDT``).
        """
        if self._contract_metadata_cache is None:
            async with self._contract_metadata_lock:
                if self._contract_metadata_cache is None:
                    self._contract_metadata_cache = await self._load_contract_metadata()
        cache = self._contract_metadata_cache
        token = display_name.strip()
        if token in cache:
            return cache[token]
        from backend.services.bingx_symbol_linker import display_name_from_bingx_symbol

        resolved = display_name_from_bingx_symbol(token)
        if resolved in cache:
            return cache[resolved]
        raise KeyError(token)

    async def _load_contract_metadata(self) -> dict[str, BingXContractMetadata]:
        payload = await self._public_get("/openApi/swap/v2/quote/contracts", {})
        contracts = payload.get("data", [])
        if not isinstance(contracts, list):
            return {}
        result: dict[str, BingXContractMetadata] = {}
        for c in contracts:
            display = str(c.get("displayName") or "")
            api_sym = str(c.get("symbol") or "")
            if not display or not api_sym:
                continue
            try:
                step_size = _precision_to_step(int(c.get("quantityPrecision") or 0))
                min_qty = float(c.get("tradeMinQuantity") or 0.0)
                min_notional = float(c.get("tradeMinUSDT") or 0.0)
                max_leverage = int(float(c.get("maxLeverage") or 1))
                price_prec = int(c.get("pricePrecision") or 0)
                qty_prec = int(c.get("quantityPrecision") or 0)
            except (TypeError, ValueError):
                continue
            result[display] = BingXContractMetadata(
                display_name=display,
                api_symbol=api_sym,
                tick_size=_precision_to_step(price_prec),
                step_size=step_size,
                min_qty=min_qty,
                min_notional=min_notional,
                max_leverage=max_leverage,
                quantity_precision=qty_prec,
                price_precision=price_prec,
            )
        logger.info("bingx_client.contract_metadata loaded contracts=%d", len(result))
        return result

    async def fetch_klines_perp(
        self,
        symbol: str,
        interval: VALID_KLINE_INTERVAL = "5m",
        *,
        limit: int = _DEFAULT_MAX_KLINES,
    ) -> list[BingXKline]:
        """Return OHLCV K-lines for a perpetual futures ``symbol``.

        BingX swap endpoint: ``GET /openApi/swap/v2/quote/klines``.
        Accepts both display names (``AAPL-USDT``) and internal API symbols
        (``NCSKAAPL2USD-USDT``) — display names are auto-resolved via the
        contracts registry on first call.
        """
        api_symbol = await self._resolve_perp_symbol(symbol.strip())
        return await self._fetch_kline_pages(
            "/openApi/swap/v2/quote/klines",
            symbol=api_symbol,
            interval=interval,
            limit=limit,
            page_limit=_PERP_KLINE_PAGE_LIMIT,
        )

    async def fetch_ticker_perp(self, symbol: str) -> dict[str, Any]:
        """Return latest ticker for a perpetual futures ``symbol``."""
        api_symbol = await self._resolve_perp_symbol(symbol.strip())
        params = {"symbol": api_symbol}
        payload = await self._public_get("/openApi/swap/v2/quote/ticker", params)
        return _unwrap_data(payload)

    async def fetch_latest_price(self, symbol: str) -> float | None:
        """Return latest spot price for ``symbol``."""
        payload = await self._public_get(
            "/openApi/spot/v1/ticker/price",
            {"symbol": symbol.strip()},
        )
        data = _unwrap_data(payload)
        return _to_float(data.get("price") or data.get("lastPrice"))

    async def fetch_latest_price_perp(self, symbol: str) -> float | None:
        """Return latest perpetual futures price for ``symbol``."""
        ticker = await self.fetch_ticker_perp(symbol)
        return _to_float(ticker.get("lastPrice") or ticker.get("price") or ticker.get("close"))

    async def fetch_all_tickers_perp(self) -> list[dict[str, Any]]:
        """Return all perpetual futures tickers for liquidity screening."""
        payload = await self._public_get("/openApi/swap/v2/quote/ticker", {})
        return _extract_dict_list(payload)

    async def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
        """Return mark/index price and current funding data for a perp symbol."""
        api_symbol = await self._resolve_perp_symbol(symbol.strip())
        payload = await self._public_get(
            "/openApi/swap/v2/quote/premiumIndex",
            {"symbol": api_symbol},
        )
        return _unwrap_data(payload)

    async def fetch_recent_trades(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent spot trades."""
        payload = await self._public_get(
            "/openApi/spot/v1/market/trades",
            {"symbol": symbol.strip(), "limit": max(1, min(int(limit), 500))},
        )
        return _extract_dict_list(payload)

    async def fetch_recent_trades_perp(
        self,
        symbol: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent perpetual futures trades."""
        api_symbol = await self._resolve_perp_symbol(symbol.strip())
        payload = await self._public_get(
            "/openApi/swap/v2/quote/trades",
            {"symbol": api_symbol, "limit": max(1, min(int(limit), 500))},
        )
        return _extract_dict_list(payload)

    async def fetch_order_book_perp(
        self,
        symbol: str,
        *,
        limit: int = _DEFAULT_DEPTH_LIMIT,
    ) -> dict[str, Any]:
        """Return perpetual futures order book depth (normalized)."""
        return await self.fetch_order_book(symbol, limit=limit)

    async def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
        """Return perpetual open interest for ``symbol``."""
        api_symbol = await self._resolve_perp_symbol(symbol.strip())
        payload = await self._public_get(
            "/openApi/swap/v2/quote/openInterest",
            {"symbol": api_symbol},
        )
        return _unwrap_data(payload)

    async def fetch_spot_symbols(self) -> list[dict[str, Any]]:
        """Return spot instrument metadata."""
        payload = await self._public_get("/openApi/spot/v1/common/symbols", {})
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("symbols"), list):
            return [row for row in data["symbols"] if isinstance(row, dict)]
        return _extract_dict_list(payload)

    # ── Private endpoints (signed) ────────────────────────────────────────────
    async def fetch_account_balance(self) -> dict[str, Any]:
        """Return signed spot account balance.

        In ``dry_run`` mode, returns a simulated stub indicating the call was
        intercepted — no key is required.
        """
        if self._dry_run:
            logger.info("bingx_client.fetch_account_balance dry_run=True intercepted")
            return {"dry_run": True, "balances": []}
        payload = await self._signed_request("GET", "/openApi/spot/v1/account/balance", {})
        return _unwrap_data(payload)

    async def fetch_perp_balance(self) -> dict[str, Any]:
        """Return signed perpetual futures account balance."""
        if self._dry_run:
            logger.info("bingx_client.fetch_perp_balance dry_run=True intercepted")
            return {
                "dry_run": True,
                "balance": {
                    "asset": "USDT",
                    "equity": "10",
                    "availableBalance": "10",
                    "availableMargin": "10",
                    "usedMargin": "0",
                    "unrealizedProfit": "0",
                },
            }
        payload = await self._signed_request("GET", "/openApi/swap/v2/user/balance", {})
        return _unwrap_data(payload)

    async def fetch_perp_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Return open perpetual futures positions."""
        if self._dry_run:
            logger.info("bingx_client.fetch_perp_positions dry_run=True intercepted")
            return []
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = await self._resolve_perp_symbol(symbol.strip())
        payload = await self._signed_request("GET", "/openApi/swap/v2/user/positions", params)
        return _extract_dict_list(payload)

    async def fetch_open_orders_perp(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Return open perpetual futures orders."""
        if self._dry_run:
            logger.info("bingx_client.fetch_open_orders_perp dry_run=True intercepted")
            return []
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = await self._resolve_perp_symbol(symbol.strip())
        payload = await self._signed_request("GET", "/openApi/swap/v2/trade/openOrders", params)
        return _extract_dict_list(payload)

    async def fetch_open_orders_spot(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Return open spot orders."""
        if self._dry_run:
            logger.info("bingx_client.fetch_open_orders_spot dry_run=True intercepted")
            return []
        params: dict[str, Any] = {"symbol": symbol.strip()} if symbol else {}
        payload = await self._signed_request("GET", "/openApi/spot/v1/trade/openOrders", params)
        return _extract_dict_list(payload)

    async def place_order(self, order: BingXOrderRequest) -> BingXOrderResponse:
        """Place a spot order. Dry-run intercepts and returns a simulated response."""
        client_order_id = order.client_order_id or f"qa-{uuid.uuid4().hex[:16]}"
        if self._dry_run:
            logger.info(
                "bingx_client.place_order DRY_RUN symbol=%s side=%s type=%s qty=%s quote_qty=%s",
                order.symbol,
                order.side,
                order.order_type,
                order.quantity,
                order.quote_order_qty,
            )
            return BingXOrderResponse(
                ok=True,
                dry_run=True,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                requested_qty=order.quantity,
                requested_quote_qty=order.quote_order_qty,
                price=order.price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={"intercepted": True, "reason": "dry_run"},
            )

        params: dict[str, str | float] = {
            "symbol": order.symbol,
            "side": order.side,
            "type": order.order_type,
            "newClientOrderId": client_order_id,
        }
        if order.quantity is not None:
            params["quantity"] = float(order.quantity)
        if order.quote_order_qty is not None:
            params["quoteOrderQty"] = float(order.quote_order_qty)
        if order.price is not None:
            params["price"] = float(order.price)
        if order.time_in_force is not None:
            params["timeInForce"] = order.time_in_force

        try:
            payload = await self._signed_request("POST", "/openApi/spot/v1/trade/order", params)
        except Exception as exc:
            logger.error(
                "bingx_client.place_order live_error symbol=%s error=%s", order.symbol, exc
            )
            return BingXOrderResponse(
                ok=False,
                dry_run=False,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                requested_qty=order.quantity,
                requested_quote_qty=order.quote_order_qty,
                price=order.price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={},
                error=str(exc),
            )

        data = _unwrap_data(payload)
        venue_order_id = str(data.get("orderId") or data.get("orderID") or "") or None
        return BingXOrderResponse(
            ok=True,
            dry_run=False,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            requested_qty=order.quantity,
            requested_quote_qty=order.quote_order_qty,
            price=order.price,
            venue_order_id=venue_order_id,
            client_order_id=client_order_id,
            raw=data,
        )

    async def cancel_order(self, symbol: str, *, venue_order_id: str) -> dict[str, Any]:
        """Cancel an existing open order. Dry-run intercepts."""
        if self._dry_run:
            logger.info(
                "bingx_client.cancel_order DRY_RUN symbol=%s order_id=%s intercepted",
                symbol,
                venue_order_id,
            )
            return {"dry_run": True, "symbol": symbol, "orderId": venue_order_id}
        params = {"symbol": symbol, "orderId": venue_order_id}
        payload = await self._signed_request("DELETE", "/openApi/spot/v1/trade/order", params)
        return _unwrap_data(payload)

    async def cancel_order_perp(self, symbol: str, *, venue_order_id: str) -> dict[str, Any]:
        """Cancel an existing perpetual futures order. Dry-run intercepts."""
        if self._dry_run:
            logger.info(
                "bingx_client.cancel_order_perp DRY_RUN symbol=%s order_id=%s intercepted",
                symbol,
                venue_order_id,
            )
            return {"dry_run": True, "symbol": symbol, "orderId": venue_order_id}
        params = {
            "symbol": await self._resolve_perp_symbol(symbol.strip()),
            "orderId": venue_order_id,
        }
        payload = await self._signed_request("DELETE", "/openApi/swap/v2/trade/order", params)
        return _unwrap_data(payload)

    async def cancel_all_orders_perp(self, symbol: str | None = None) -> dict[str, Any]:
        """Cancel all open perpetual futures orders, optionally scoped by symbol."""
        if self._dry_run:
            logger.info("bingx_client.cancel_all_orders_perp dry_run=True intercepted")
            return {"dry_run": True, "symbol": symbol, "cancelled": True}
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = await self._resolve_perp_symbol(symbol.strip())
        payload = await self._signed_request(
            "DELETE",
            "/openApi/swap/v2/trade/allOpenOrders",
            params,
        )
        return _unwrap_data(payload)

    async def close_all_positions(self, *, confirm: bool = False) -> dict[str, Any]:
        """Emergency close-all positions endpoint.

        ``confirm`` is required at the caller boundary to avoid accidental live use.
        Dry-run still returns an intercepted response.
        """
        if not confirm:
            raise ValueError("close_all_positions requires confirm=True")
        if self._dry_run:
            logger.info("bingx_client.close_all_positions dry_run=True intercepted")
            return {"dry_run": True, "closed": True}
        payload = await self._signed_request(
            "POST",
            "/openApi/swap/v2/trade/closeAllPositions",
            {},
        )
        return _unwrap_data(payload)

    async def set_leverage_perp(
        self,
        symbol: str,
        leverage: int,
        side: str = "BOTH",
    ) -> dict[str, Any]:
        """Set perpetual futures leverage for a symbol."""
        lev = max(1, min(int(leverage), 125))
        if self._dry_run:
            logger.info("bingx_client.set_leverage_perp dry_run=True intercepted")
            return {"dry_run": True, "symbol": symbol, "leverage": lev, "side": side}
        params = {
            "symbol": await self._resolve_perp_symbol(symbol.strip()),
            "leverage": lev,
            "side": side,
        }
        payload = await self._signed_request("POST", "/openApi/swap/v2/user/leverage", params)
        return _unwrap_data(payload)

    async def set_margin_type_perp(self, symbol: str, margin_type: str) -> dict[str, Any]:
        """Set margin type (CROSSED/ISOLATED) for a perpetual symbol."""
        normalized = margin_type.strip().upper()
        if normalized not in {"CROSSED", "ISOLATED"}:
            raise ValueError("margin_type must be CROSSED or ISOLATED")
        if self._dry_run:
            logger.info("bingx_client.set_margin_type_perp dry_run=True intercepted")
            return {"dry_run": True, "symbol": symbol, "marginType": normalized}
        params = {
            "symbol": await self._resolve_perp_symbol(symbol.strip()),
            "marginType": normalized,
        }
        payload = await self._signed_request("POST", "/openApi/swap/v2/user/marginType", params)
        return _unwrap_data(payload)

    async def fetch_trade_history_perp(
        self,
        symbol: str,
        *,
        limit: int = 100,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return perpetual futures trade fills for ``symbol``."""
        if self._dry_run:
            logger.info("bingx_client.fetch_trade_history_perp dry_run=True intercepted")
            return []
        params: dict[str, Any] = {
            "symbol": await self._resolve_perp_symbol(symbol.strip()),
            "limit": max(1, min(int(limit), 500)),
        }
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        payload = await self._signed_request("GET", "/openApi/swap/v2/trade/allFillOrders", params)
        return _extract_dict_list(payload)

    async def create_listen_key(self) -> str | None:
        """Create a private user data stream listen key."""
        if self._dry_run:
            return "dry-run-listen-key"
        payload = await self._signed_request("POST", "/openApi/user/auth/userDataStream", {})
        data = _unwrap_data(payload)
        key = data.get("listenKey") or data.get("listen_key")
        return str(key) if key else None

    async def refresh_listen_key(self, listen_key: str) -> dict[str, Any]:
        """Refresh a private user data stream listen key."""
        if self._dry_run:
            return {"dry_run": True, "listenKey": listen_key, "refreshed": True}
        payload = await self._signed_request(
            "PUT",
            "/openApi/user/auth/userDataStream",
            {"listenKey": listen_key},
        )
        return _unwrap_data(payload)

    async def place_order_perp(self, order: BingXPerpOrderRequest) -> BingXOrderResponse:
        """Place a perpetual futures order on the swap endpoint. Dry-run intercepts.

        Accepts display names (``AAPL-USDT``) — symbol is auto-resolved to the
        BingX internal API symbol before sending the request.
        """
        api_symbol = await self._resolve_perp_symbol(order.symbol.strip())
        client_order_id = order.client_order_id or f"qa-{uuid.uuid4().hex[:16]}"
        if self._dry_run:
            logger.info(
                "bingx_client.place_order_perp DRY_RUN symbol=%s side=%s pos=%s qty=%s",
                order.symbol,
                order.side,
                order.position_side,
                order.quantity,
            )
            return BingXOrderResponse(
                ok=True,
                dry_run=True,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                requested_qty=order.quantity,
                requested_quote_qty=None,
                price=order.price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={"intercepted": True, "reason": "dry_run", "market": "perp"},
            )

        params: dict[str, Any] = {
            "symbol": api_symbol,
            "side": order.side,
            "positionSide": resolve_one_way_position_side(order),
            "type": order.order_type,
            "newClientOrderId": client_order_id,
        }
        if order.quantity is not None:
            params["quantity"] = float(order.quantity)
        if order.price is not None:
            params["price"] = float(order.price)
        if order.stop_price is not None:
            params["stopPrice"] = float(order.stop_price)
        if order.time_in_force is not None:
            params["timeInForce"] = order.time_in_force
        if order.reduce_only:
            if _bingx_omit_reduce_only():
                logger.warning(
                    "bingx_client.place_order_perp reduce_only_omitted symbol=%s "
                    "(BINGX_OMIT_REDUCE_ONLY=true)",
                    order.symbol,
                )
            else:
                params["reduceOnly"] = "true"
                logger.info(
                    "bingx_client.place_order_perp reduce_only symbol=%s side=%s pos=%s qty=%s",
                    order.symbol,
                    order.side,
                    params["positionSide"],
                    order.quantity,
                )
        if order.stop_loss_price is not None:
            params["stopLoss"] = json.dumps(
                {
                    "type": "STOP_MARKET",
                    "stopPrice": float(order.stop_loss_price),
                    "workingType": "MARK_PRICE",
                },
                separators=(",", ":"),
            )
        if order.take_profit_price is not None:
            params["takeProfit"] = json.dumps(
                {
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": float(order.take_profit_price),
                    "workingType": "MARK_PRICE",
                },
                separators=(",", ":"),
            )

        try:
            payload = await self._signed_request("POST", "/openApi/swap/v2/trade/order", params)
        except Exception as exc:
            logger.error(
                "bingx_client.place_order_perp live_error symbol=%s error=%s", order.symbol, exc
            )
            return BingXOrderResponse(
                ok=False,
                dry_run=False,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                requested_qty=order.quantity,
                requested_quote_qty=None,
                price=order.price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={},
                error=str(exc),
            )

        api_ok, api_err = _bingx_api_ok(payload)
        if not api_ok:
            logger.error(
                "bingx_client.place_order_perp api_error symbol=%s code=%s",
                order.symbol,
                api_err,
            )
            return BingXOrderResponse(
                ok=False,
                dry_run=False,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                requested_qty=order.quantity,
                requested_quote_qty=None,
                price=order.price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw=payload,
                error=api_err,
            )

        data = _unwrap_data(payload)
        venue_order_id = _extract_order_id(data)
        if not venue_order_id:
            logger.warning(
                "bingx_client.place_order_perp missing_order_id symbol=%s raw=%s",
                order.symbol,
                data,
            )
        from backend.layer_1_data.datos.bingx_fill_price import resolve_fill_price_from_row

        fill_price = resolve_fill_price_from_row(data)
        return BingXOrderResponse(
            ok=True,
            dry_run=False,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            requested_qty=order.quantity,
            requested_quote_qty=None,
            price=order.price,
            venue_order_id=venue_order_id,
            client_order_id=client_order_id,
            raw=data,
            fill_price=fill_price,
        )

    # ── HTTP plumbing ─────────────────────────────────────────────────────────
    async def _public_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        client = await self._ensure_client()
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await client.get(path, params=params)
                response.raise_for_status()
                return _safe_json(response)
            except httpx.PoolTimeout as exc:
                detail = str(exc).strip() or exc.__class__.__name__
                logger.debug("bingx_client.public_get pool_timeout path=%s error=%s", path, detail)
                raise RuntimeError(
                    f"BingX public request pool timeout: {detail} path={path}"
                ) from exc
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else 500
                if status_code in {500, 502, 503, 504} and attempt < max_retries - 1:
                    sleep_time = 2**attempt
                    logger.warning(
                        "Temporary HTTP status %d on path=%s. Retry %d/%d in %ds",
                        status_code,
                        path,
                        attempt + 1,
                        max_retries,
                        sleep_time,
                    )
                    await asyncio.sleep(sleep_time)
                    continue
                body = exc.response.text[:200] if exc.response is not None else ""
                logger.warning(
                    "bingx_client.public_get failed path=%s status=%s body=%s",
                    path,
                    status_code,
                    body,
                )
                raise RuntimeError(
                    f"BingX public request failed: status={status_code} " f"path={path} body={body}"
                ) from exc
            except httpx.HTTPError as exc:
                detail = str(exc).strip() or exc.__class__.__name__
                if attempt == max_retries - 1:
                    logger.warning("bingx_client.public_get failed path=%s error=%s", path, detail)
                    raise RuntimeError(
                        f"BingX public request failed: {detail} path={path}"
                    ) from exc
                sleep_time = 2**attempt
                logger.warning(
                    "HTTP/Timeout error on path=%s. Retry %d/%d in %ds: %s",
                    path,
                    attempt + 1,
                    max_retries,
                    sleep_time,
                    detail,
                )
                await asyncio.sleep(sleep_time)
        raise RuntimeError(f"BingX public request failed after {max_retries} attempts path={path}")

    async def _fetch_kline_pages(
        self,
        path: str,
        *,
        symbol: str,
        interval: VALID_KLINE_INTERVAL,
        limit: int,
        page_limit: int,
    ) -> list[BingXKline]:
        target = max(1, int(limit))
        per_page = max(1, int(page_limit))
        out_by_open_time: dict[int, BingXKline] = {}
        end_time: int | None = None

        while len(out_by_open_time) < target:
            remaining = target - len(out_by_open_time)
            overlap_allowance = 1 if end_time is not None else 0
            request_limit = min(per_page, remaining + overlap_allowance)
            params: dict[str, str | int] = {
                "symbol": symbol,
                "interval": interval,
                "limit": request_limit,
            }
            if end_time is not None:
                params["endTime"] = end_time

            payload = await self._public_get(path, params)
            parsed_rows: list[BingXKline] = []
            for row in _extract_rows(payload):
                parsed = _parse_kline_row(row)
                if parsed is not None:
                    parsed_rows.append(parsed)

            if not parsed_rows:
                break

            before_count = len(out_by_open_time)
            for kline in parsed_rows:
                out_by_open_time[kline.open_time_ms] = kline

            oldest_open = min(kline.open_time_ms for kline in parsed_rows)
            if len(out_by_open_time) == before_count or oldest_open <= 0:
                break
            end_time = oldest_open

            if len(parsed_rows) < request_limit:
                break

        rows = sorted(out_by_open_time.values(), key=lambda item: item.open_time_ms)
        return rows[-target:]

    async def _signed_request(
        self, method: str, path: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._api_key or not self._secret_key:
            raise RuntimeError(
                "BingX signed request requires api_key/secret_key (or enable dry_run=True)."
            )
        client = await self._ensure_client()

        merged = dict(params)
        merged.setdefault("timestamp", int(time.time() * 1000))
        merged.setdefault("recvWindow", self._recv_window_ms)

        normalized = self._normalize_signed_params(merged)
        query_string, signature = self._build_query_and_signature(merged)
        signed_payload = f"{query_string}&signature={signature}"

        headers = {
            "X-BX-APIKEY": self._api_key,
            "X-SOURCE-KEY": self._source_key,
        }

        method_upper = method.upper()
        try:
            if method_upper == "POST":
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                response = await client.request(
                    method_upper,
                    path,
                    content=signed_payload.encode("utf-8"),
                    headers=headers,
                )
            else:
                # GET/DELETE: signed parameters belong in the query string, not the body.
                url_query = self._format_signed_url_query(query_string, signature, normalized)
                response = await client.request(
                    method_upper,
                    f"{path}?{url_query}",
                    headers=headers,
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "bingx_client.signed_request failed method=%s path=%s error=%s", method, path, exc
            )
            raise
        return _safe_json(response)

    def _normalize_signed_params(self, params: dict[str, Any]) -> dict[str, str]:
        """Normalize request parameters to the canonical string form used for signing."""
        merged: dict[str, Any] = dict(params)
        merged.setdefault("timestamp", int(time.time() * 1000))
        merged.setdefault("recvWindow", self._recv_window_ms)

        normalized: dict[str, str] = {}
        for key, value in merged.items():
            if isinstance(value, dict | list):
                normalized[key] = json.dumps(value, separators=(",", ":"))
            else:
                normalized[key] = str(value)
        return normalized

    def _format_signed_url_query(
        self,
        query_string: str,
        signature: str,
        normalized: dict[str, str],
    ) -> str:
        """Build the transport query string for GET/DELETE signed requests."""
        if "[" in query_string or "{" in query_string:
            pairs: list[str] = []
            for key in sorted(normalized):
                value = normalized[key]
                if "[" in value or "{" in value:
                    pairs.append(f"{key}={urllib.parse.quote(value, safe='')}")
                else:
                    pairs.append(f"{key}={value}")
            pairs.append(f"signature={signature}")
            return "&".join(pairs)
        return f"{query_string}&signature={signature}"

    def _build_query_and_signature(self, params: dict[str, Any]) -> tuple[str, str]:
        """Build raw query string and HMAC-SHA256 signature per BingX Swap API spec.

        **Critical**: All dictionary/list parameters are serialized to compact JSON
        without whitespace using ``json.dumps(..., separators=(',', ':'))`` before
        building the query string. This prevents BingX from rejecting the signature
        (error 100001) due to unexpected spaces in the canonical string.

        Returns
        -------
        tuple[str, str]
            (query_string, signature) where query_string is the raw canonical form
            used for HMAC and ready to send in the URL, and signature is the
            hex-encoded HMAC-SHA256 hash.
        """
        normalized = self._normalize_signed_params(params)
        sorted_items = sorted(normalized.items())
        query_string = "&".join(f"{k}={v}" for k, v in sorted_items)

        secret = (self._secret_key or "").encode("utf-8")
        signature = hmac.new(secret, query_string.encode("utf-8"), hashlib.sha256).hexdigest()

        return query_string, signature

    def _sign_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Legacy: kept for backward compatibility. Use _build_query_and_signature for new code."""
        query_string, signature = self._build_query_and_signature(params)
        # Parse query_string back into dict for backward compatibility
        result: dict[str, Any] = {}
        if query_string:
            for pair in query_string.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    result[k] = v
        result["signature"] = signature
        return result


# ── WebSocket client ──────────────────────────────────────────────────────────
class BingXWebSocketClient:
    """Minimal async wrapper over BingX market WebSocket (gzip frames).

    Only the public K-line channel is exposed — sufficient for the lightweight
    scanner. The client is intentionally small: callers iterate with
    ``async for message in client.stream_klines(symbol, interval): ...``.
    """

    def __init__(self, url: str = BINGX_WS_MARKET_URL) -> None:
        self._url: str = url
        self._connection: ClientConnection | None = None

    async def stream_klines(
        self,
        symbol: str,
        interval: VALID_KLINE_INTERVAL = "5m",
        *,
        max_messages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to K-line updates and yield each decoded payload."""
        if websockets is None:  # pragma: no cover - defensive
            raise RuntimeError("websockets package not available; install requirements.txt")
        sub_id = f"qa-{uuid.uuid4().hex[:12]}"
        channel = f"{symbol.strip()}@kline_{interval}"
        sub_payload = {"id": sub_id, "reqType": "sub", "dataType": channel}

        async with websockets.connect(self._url, ping_interval=20, ping_timeout=20) as ws:
            self._connection = ws  # type: ignore[assignment]
            await ws.send(json.dumps(sub_payload))
            logger.info(
                "bingx_ws.subscribed symbol=%s interval=%s channel=%s", symbol, interval, channel
            )
            count = 0
            try:
                async for raw in ws:
                    decoded = _decode_ws_frame(raw)
                    if decoded is None:
                        continue
                    if decoded.get("ping"):
                        # BingX sends ping frames as JSON; echo back as pong.
                        await ws.send(json.dumps({"pong": decoded["ping"]}))
                        continue
                    yield decoded
                    count += 1
                    if max_messages is not None and count >= max_messages:
                        break
            except ConnectionClosed as exc:  # pragma: no cover - network-dependent
                logger.warning("bingx_ws.connection_closed symbol=%s error=%s", symbol, exc)
            finally:
                self._connection = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _precision_to_step(precision: int) -> float:
    """Convert decimal-place precision (e.g. 2) to a step size (e.g. 0.01)."""
    return 10.0 ** -max(0, int(precision))


def _parse_depth_levels(raw_rows: object) -> list[tuple[float, float]]:
    """Parse raw BingX depth rows into ``(price, quantity)`` tuples.

    Each row is expected to be ``["price", "quantity"]``.  Rows with
    unparseable or non-positive prices are silently skipped.
    """
    out: list[tuple[float, float]] = []
    if not isinstance(raw_rows, list):
        return out
    for row in raw_rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        try:
            price = float(row[0])
            qty = float(row[1])
        except (TypeError, ValueError):
            continue
        if price > 0.0:
            out.append((price, qty))
    return out


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"BingX returned non-JSON payload: {response.text[:200]}") from exc
    if not isinstance(data, dict):
        return {"data": data}
    return data


def _unwrap_data(payload: dict[str, Any]) -> dict[str, Any]:
    inner = payload.get("data")
    if isinstance(inner, dict):
        return inner
    if isinstance(inner, list):
        return {"items": inner}
    return payload


def _bingx_api_ok(payload: dict[str, Any]) -> tuple[bool, str]:
    """Valida código de respuesta BingX (0 = éxito)."""
    code = payload.get("code")
    if code is not None and str(code) not in {"0", "00000"}:
        msg = str(payload.get("msg") or payload.get("message") or code)
        return False, msg
    return True, ""


def _extract_order_id(data: dict[str, Any]) -> str | None:
    """Extrae orderId de respuestas swap/spot (varios formatos)."""
    for key in ("orderId", "orderID"):
        if data.get(key):
            return str(data[key])
    order = data.get("order")
    if isinstance(order, dict):
        for key in ("orderId", "orderID"):
            if order.get(key):
                return str(order[key])
    return None


def _extract_dict_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data: object = payload.get("data", payload)
    if isinstance(data, dict):
        data = data.get("items") or data.get("orders") or data.get("positions") or []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _extract_rows(payload: dict[str, Any]) -> list[list[Any]]:
    data = payload.get("data", payload)
    if isinstance(data, dict):
        data = data.get("items") or data.get("klines") or []
    if not isinstance(data, list):
        return []
    out: list[list[Any]] = []
    for row in data:
        if isinstance(row, list):
            out.append(row)
        elif isinstance(row, dict):
            # Swap rows use "time" for open_time; spot rows use "openTime".
            out.append(
                [
                    row.get("openTime") or row.get("t") or row.get("time") or 0,
                    row.get("open") or row.get("o"),
                    row.get("high") or row.get("h"),
                    row.get("low") or row.get("l"),
                    row.get("close") or row.get("c"),
                    row.get("volume") or row.get("v"),
                    row.get("closeTime") or row.get("T") or None,
                ]
            )
    return out


def _parse_kline_row(row: Iterable[Any]) -> BingXKline | None:
    seq = list(row)
    if len(seq) < 6:
        return None
    try:
        open_time = int(seq[0])
        o = float(seq[1])
        h = float(seq[2])
        lo = float(seq[3])
        c = float(seq[4])
        v = float(seq[5])
        raw_ct = seq[6] if len(seq) > 6 else None
        close_time = int(raw_ct) if raw_ct else open_time
    except (TypeError, ValueError):
        return None
    if not all(x > 0 for x in (o, h, lo, c)):
        return None
    return BingXKline(
        open_time_ms=open_time,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=max(v, 0.0),
        close_time_ms=close_time,
    )


def _decode_ws_frame(raw: bytes | str) -> dict[str, Any] | None:
    """BingX WS frames are gzip-compressed JSON; pings arrive as plain JSON."""
    try:
        if isinstance(raw, bytes | bytearray):
            try:
                text = gzip.decompress(bytes(raw)).decode("utf-8")
            except OSError:
                text = bytes(raw).decode("utf-8", errors="replace")
        else:
            text = str(raw)
        parsed = json.loads(text)
    except (ValueError, OSError):
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


async def healthcheck(*, timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Quick connectivity probe — fetches BTC/USDT 1m klines."""
    async with BingXClient(dry_run=True, timeout_seconds=timeout_seconds) as client:
        try:
            klines = await client.fetch_klines("BTC-USDT", interval="1m", limit=5)
            return {
                "ok": bool(klines),
                "bars": len(klines),
                "latest_close": klines[-1].close if klines else None,
            }
        except (httpx.HTTPError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}


if __name__ == "__main__":  # pragma: no cover - manual probe
    result = asyncio.run(healthcheck())
    logger.info("bingx_client.healthcheck %s", json.dumps(result, sort_keys=True))
