"""Alpaca Markets WebSocket Client - Real-time stock aggregates and trades."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import websockets
from websockets.client import ClientConnection
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class AlpacaWSClient:
    """
    WebSocket client for Alpaca Market Data v2 (IEX or SIP stream URL).
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        base_url: str = "wss://stream.data.alpaca.markets/v2/iex",
        *,
        bars_feed: str = "iex",
        data_rest_base: str | None = None,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        bf = (bars_feed or "iex").strip().lower()
        self._bars_feed: str = bf if bf in ("iex", "sip") else "iex"
        drb = (data_rest_base or "https://data.alpaca.markets").strip().rstrip("/")
        self._data_rest_base: str = drb or "https://data.alpaca.markets"
        self.websocket: ClientConnection | None = None
        self.subscriptions: dict[str, list[str]] = {"trades": [], "bars": []}
        self.on_message_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._running = False
        self._authenticated = False
        self._reconnect_delay = 5.0
        self._rest_client: httpx.AsyncClient | None = None

    async def _ensure_rest_client(self) -> httpx.AsyncClient:
        """Cliente HTTP persistente para REST (evita TLS handshake en cada backfill)."""
        if self._rest_client is None or self._rest_client.is_closed:
            self._rest_client = httpx.AsyncClient(
                timeout=60.0,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._rest_client

    async def aclose_rest(self) -> None:
        if self._rest_client is not None:
            await self._rest_client.aclose()
            self._rest_client = None

    async def connect(self):
        """Establish connection and authenticate."""
        try:
            logger.info(f"Connecting to Alpaca WS: {self.base_url}")
            self.websocket = await websockets.connect(self.base_url)

            # 1. Welcome message
            welcome = await self.websocket.recv()
            logger.debug(f"Alpaca WS welcome: {welcome}")

            # 2. Authenticate
            auth_msg = {"action": "auth", "key": self.api_key, "secret": self.secret_key}
            await self.websocket.send(json.dumps(auth_msg))

            auth_resp_raw = await self.websocket.recv()
            auth_resp = json.loads(auth_resp_raw)

            if isinstance(auth_resp, list) and auth_resp[0].get("T") == "error":
                raise Exception(f"Alpaca Auth Failed: {auth_resp[0].get('msg')}")

            if isinstance(auth_resp, list) and auth_resp[0].get("msg") == "authenticated":
                logger.info("Alpaca WS authenticated successfully")
                self._authenticated = True
                self._running = True
                # Resubscribe if needed
                await self._send_subscriptions()
            else:
                raise Exception(f"Unexpected auth response: {auth_resp}")

        except Exception as e:
            logger.error(f"Failed to connect to Alpaca WS: {e}")
            self._authenticated = False
            raise

    @staticmethod
    def _alpaca_bar_to_internal(b: dict[str, Any]) -> dict[str, Any]:
        ts_str = str(b.get("t", "")).replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        ts_ms = int(dt.timestamp() * 1000)
        return {
            "ev": "AM",
            "s": ts_ms,
            "o": b.get("o"),
            "h": b.get("h"),
            "l": b.get("l"),
            "c": b.get("c"),
            "v": b.get("v"),
        }

    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        *,
        max_bars: int = 10_000,
        lookback_days: int = 1825,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch historical bars from Alpaca REST API (feed ``iex`` or ``sip``, see ``bars_feed``).

        Uses an explicit UTC window so higher timeframes are not limited to "today"
        (Alpaca defaults otherwise yield very few daily/weekly bars).

        Paginates with ``page_token`` until ``max_bars`` is reached or data ends.

        Uses ``sort=desc`` so the first page is the **newest** bars; with ``sort=asc`` the
        first page fills the limit with the **oldest** slice and pagination stopped early
        (``remaining`` hit zero), leaving the chart stuck years in the past.
        """
        if limit is not None:
            max_bars = min(limit, 10_000)

        max_bars = max(1, min(int(max_bars), 50_000))
        lookback_days = max(1, min(int(lookback_days), 365 * 20))

        url = f"{self._data_rest_base}/v2/stocks/bars"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }

        end = datetime.now(UTC)
        start = end - timedelta(days=lookback_days)
        start_s = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_s = end.strftime("%Y-%m-%dT%H:%M:%SZ")

        by_ts: dict[int, dict[str, Any]] = {}
        page_token: str | None = None
        max_pages = 30
        sym = symbol.upper()

        http = await self._ensure_rest_client()
        try:
            for _ in range(max_pages):
                slots = max_bars - len(by_ts)
                if slots <= 0:
                    break

                params: dict[str, Any] = {
                    "symbols": sym,
                    "timeframe": timeframe,
                    "start": start_s,
                    "end": end_s,
                    "limit": min(10_000, slots),
                    "adjustment": "raw",
                    "feed": self._bars_feed,
                    "sort": "desc",
                }
                if page_token:
                    params["page_token"] = page_token

                r = await http.get(url, headers=headers, params=params)
                if r.status_code != 200:
                    logger.error("Alpaca REST error: %s %s", r.status_code, r.text)
                    break

                data = r.json()
                raw = data.get("bars", {}).get(sym) or []

                for b in raw:
                    row = self._alpaca_bar_to_internal(b)
                    by_ts[row["s"]] = row

                page_token = data.get("next_page_token")
                if not page_token or not raw:
                    break

            keys_sorted = sorted(by_ts.keys())
            return [by_ts[k] for k in keys_sorted[-max_bars:]]

        except Exception as e:
            logger.error("Alpaca REST request failed: %s", e)
            return []

    async def subscribe(self, symbols: list[str], channels: list[str] = ["bars"]):
        """
        Subscribe to channels. Alpaca channels: 'trades', 'quotes', 'bars', 'updatedBars', 'dailyBars'.
        Default: 'bars' (Minute aggregates).
        """
        for ch in channels:
            if ch not in self.subscriptions:
                self.subscriptions[ch] = []
            for s in symbols:
                if s.upper() not in self.subscriptions[ch]:
                    self.subscriptions[ch].append(s.upper())

        if self.websocket and self._authenticated:
            await self._send_subscriptions()

    async def _send_subscriptions(self):
        sub_msg = {"action": "subscribe"}
        for ch, syms in self.subscriptions.items():
            if syms:
                sub_msg[ch] = syms

        if len(sub_msg) > 1:  # Beyond just 'action'
            await self.websocket.send(json.dumps(sub_msg))
            logger.info(f"Alpaca Subscribed to: {sub_msg}")

    async def start(self, callback: Callable[[dict[str, Any]], Awaitable[None]]):
        """Start the message loop."""
        self.on_message_callback = callback

        while self._running:
            try:
                if not self.websocket or not self._authenticated:
                    await self.connect()

                async for message in self.websocket:
                    data = json.loads(message)
                    if isinstance(data, list):
                        for item in data:
                            msg_type = item.get("T")
                            # Map Alpaca format to internal Candle format
                            if msg_type == "b":  # Bar (Minute Aggregate)
                                # Alpaca 't' is '2021-04-12T14:33:00Z'
                                ts_str = item.get("t").replace("Z", "+00:00")
                                dt = datetime.fromisoformat(ts_str)
                                ts_ms = int(dt.timestamp() * 1000)

                                candle = {
                                    "ev": "AM",
                                    "s": ts_ms,
                                    "o": item.get("o"),
                                    "h": item.get("h"),
                                    "l": item.get("l"),
                                    "c": item.get("c"),
                                    "v": item.get("v"),
                                }
                                await self.on_message_callback(candle)

                            elif (
                                msg_type == "t"
                            ):  # Trade — Alpaca: p=price, s=size, t=RFC3339 timestamp
                                trade = {
                                    "ev": "T",
                                    "price": item.get("p"),
                                    "size": item.get("s"),
                                    "ts": item.get("t"),
                                }
                                await self.on_message_callback(trade)
                            elif msg_type == "error":
                                logger.error(f"Alpaca WS server error: {item.get('msg')}")

            except (ConnectionClosed, Exception) as e:
                logger.error(
                    f"Alpaca WS connection error: {e}. Reconnecting in {self._reconnect_delay}s..."
                )
                self.websocket = None
                self._authenticated = False
                await asyncio.sleep(self._reconnect_delay)

    async def stop(self):
        """Cleanly stop the client."""
        self._running = False
        await self.aclose_rest()
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        logger.info("Alpaca WS client stopped.")
