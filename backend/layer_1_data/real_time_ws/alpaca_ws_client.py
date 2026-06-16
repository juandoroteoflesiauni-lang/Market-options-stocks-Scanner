"""Cliente WebSocket canónico de Alpaca Market Data v2. # [IM][TH]

Implementación única para streaming en tiempo real (trades/bars) y backfill
REST de barras históricas. Estandariza auth y logging; mapea los mensajes de
Alpaca a un formato interno con símbolo incluido.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import websockets

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

MessageCallback = Callable[[dict[str, Any]], Awaitable[None]]

_RECONNECT_DELAY_S = 5.0
_DEFAULT_WS_URL = "wss://stream.data.alpaca.markets/v2/iex"
_DEFAULT_DATA_REST = "https://data.alpaca.markets"
_MAX_BARS_CAP = 50_000
_MAX_PAGES = 30


class AlpacaWSClient:
    """WebSocket + REST client para Alpaca Market Data v2 (feed IEX/SIP)."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        base_url: str = _DEFAULT_WS_URL,
        *,
        bars_feed: str = "iex",
        data_rest_base: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        feed = (bars_feed or "iex").strip().lower()
        self._bars_feed = feed if feed in ("iex", "sip") else "iex"
        rest = (data_rest_base or _DEFAULT_DATA_REST).strip().rstrip("/")
        self._data_rest_base = rest or _DEFAULT_DATA_REST
        self.websocket: Any | None = None
        self.subscriptions: dict[str, list[str]] = {"trades": [], "bars": []}
        self.on_message_callback: MessageCallback | None = None
        self._running = False
        self._authenticated = False
        self._rest_client: httpx.AsyncClient | None = None

    async def _ensure_rest_client(self) -> httpx.AsyncClient:
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

    async def connect(self) -> None:
        """Abre la conexión y autentica contra el stream de Alpaca."""
        logger.info("alpaca_ws.connecting url=%s", self.base_url)
        self.websocket = await websockets.connect(self.base_url)
        await self.websocket.recv()  # welcome frame
        auth_msg = {"action": "auth", "key": self.api_key, "secret": self.secret_key}
        await self.websocket.send(json.dumps(auth_msg))
        auth_resp = json.loads(await self.websocket.recv())
        if not self._auth_succeeded(auth_resp):
            self._authenticated = False
            raise RuntimeError(f"Alpaca WS auth failed: {auth_resp}")
        self._authenticated = True
        self._running = True
        logger.info("alpaca_ws.authenticated")
        await self._send_subscriptions()

    @staticmethod
    def _auth_succeeded(auth_resp: Any) -> bool:
        return (
            isinstance(auth_resp, list)
            and bool(auth_resp)
            and auth_resp[0].get("msg") == "authenticated"
        )

    async def subscribe(
        self, symbols: list[str], channels: Sequence[str] = ("bars",)
    ) -> None:
        """Registra símbolos en los canales indicados y reenvía si hay conexión."""
        for channel in channels:
            self.subscriptions.setdefault(channel, [])
            for symbol in symbols:
                upper = symbol.upper()
                if upper not in self.subscriptions[channel]:
                    self.subscriptions[channel].append(upper)
        if self.websocket is not None and self._authenticated:
            await self._send_subscriptions()

    async def _send_subscriptions(self) -> None:
        sub_msg: dict[str, Any] = {"action": "subscribe"}
        for channel, symbols in self.subscriptions.items():
            if symbols:
                sub_msg[channel] = symbols
        if len(sub_msg) > 1 and self.websocket is not None:
            await self.websocket.send(json.dumps(sub_msg))
            logger.info("alpaca_ws.subscribed channels=%s", list(sub_msg.keys())[1:])

    @staticmethod
    def map_message(item: dict[str, Any]) -> dict[str, Any] | None:
        """Mapea un mensaje Alpaca a formato interno (con símbolo)."""
        msg_type = item.get("T")
        if msg_type == "b":  # minute bar
            return {
                "ev": "AM",
                "sym": item.get("S"),
                "s": _rfc3339_to_ms(item.get("t")),
                "o": item.get("o"),
                "h": item.get("h"),
                "l": item.get("l"),
                "c": item.get("c"),
                "v": item.get("v"),
            }
        if msg_type == "t":  # trade
            return {
                "ev": "T",
                "sym": item.get("S"),
                "price": item.get("p"),
                "size": item.get("s"),
                "ts": item.get("t"),
            }
        if msg_type == "error":
            logger.error("alpaca_ws.server_error msg=%s", item.get("msg"))
        return None

    async def start(self, callback: MessageCallback) -> None:
        """Bucle principal con reconexión automática."""
        self.on_message_callback = callback
        while self._running or self.websocket is None:
            try:
                if self.websocket is None or not self._authenticated:
                    await self.connect()
                ws = self.websocket
                if ws is None:
                    continue
                async for raw in ws:
                    await self._dispatch(json.loads(raw))
            except Exception as exc:
                logger.error("alpaca_ws.connection_error error=%s reconnecting", exc)
                self.websocket = None
                self._authenticated = False
                await asyncio.sleep(_RECONNECT_DELAY_S)

    async def _dispatch(self, payload: Any) -> None:
        if not isinstance(payload, list) or self.on_message_callback is None:
            return
        for item in payload:
            mapped = self.map_message(item)
            if mapped is not None:
                await self.on_message_callback(mapped)

    async def stop(self) -> None:
        self._running = False
        await self.aclose_rest()
        if self.websocket is not None:
            await self.websocket.close()
            self.websocket = None
        logger.info("alpaca_ws.stopped")

    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        *,
        max_bars: int = 10_000,
        lookback_days: int = 1825,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Backfill REST de barras históricas (sort=desc, paginado)."""
        if limit is not None:
            max_bars = min(limit, 10_000)
        max_bars = max(1, min(int(max_bars), _MAX_BARS_CAP))
        lookback_days = max(1, min(int(lookback_days), 365 * 20))
        params_base = self._historical_params(symbol, timeframe, lookback_days)
        return await self._paginate_bars(symbol.upper(), params_base, max_bars)

    def _historical_params(
        self, symbol: str, timeframe: str, lookback_days: int
    ) -> dict[str, Any]:
        end = datetime.now(UTC)
        start = end - timedelta(days=lookback_days)
        return {
            "symbols": symbol.upper(),
            "timeframe": timeframe,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "adjustment": "raw",
            "feed": self._bars_feed,
            "sort": "desc",
        }

    async def _paginate_bars(
        self, sym: str, params_base: dict[str, Any], max_bars: int
    ) -> list[dict[str, Any]]:
        url = f"{self._data_rest_base}/v2/stocks/bars"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }
        by_ts: dict[int, dict[str, Any]] = {}
        page_token: str | None = None
        http = await self._ensure_rest_client()
        for _ in range(_MAX_PAGES):
            slots = max_bars - len(by_ts)
            if slots <= 0:
                break
            params = {**params_base, "limit": min(10_000, slots)}
            if page_token:
                params["page_token"] = page_token
            response = await http.get(url, headers=headers, params=params)
            if response.status_code != 200:
                logger.error("alpaca_ws.rest_error status=%s", response.status_code)
                break
            data = response.json()
            raw = data.get("bars", {}).get(sym) or []
            for bar in raw:
                row = self._rest_bar_to_internal(bar)
                by_ts[row["s"]] = row
            page_token = data.get("next_page_token")
            if not page_token or not raw:
                break
        ordered = sorted(by_ts.keys())
        return [by_ts[k] for k in ordered[-max_bars:]]

    @staticmethod
    def _rest_bar_to_internal(bar: dict[str, Any]) -> dict[str, Any]:
        return {
            "ev": "AM",
            "s": _rfc3339_to_ms(bar.get("t")),
            "o": bar.get("o"),
            "h": bar.get("h"),
            "l": bar.get("l"),
            "c": bar.get("c"),
            "v": bar.get("v"),
        }


def _rfc3339_to_ms(timestamp: Any) -> int:
    """Convierte un timestamp RFC3339 de Alpaca a epoch ms."""
    text = str(timestamp or "").replace("Z", "+00:00")
    if not text:
        return 0
    return int(datetime.fromisoformat(text).timestamp() * 1000)
