from __future__ import annotations
from typing import Any
"""Persistent BingX market WebSocket hub.

Layer 1 owns venue I/O only. Consumers can subscribe to public channels and
receive decoded payloads or 1-second trade micro-bars without handling socket
lifecycles directly.
"""


import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass

try:  # pragma: no cover - import shim for optional runtime dependency.
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]
    ConnectionClosed = Exception  # type: ignore[assignment,misc]

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import (
    BINGX_WS_MARKET_URL,
    _decode_ws_frame,
    _parse_depth_levels,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class BingXMicroBar:
    symbol: str
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TickAggregator:
    """Aggregate trade ticks into bounded 1-second OHLCV bars."""

    def __init__(self, symbol: str) -> None:
        self._symbol = symbol
        self._bucket_ms: int | None = None
        self._open: float | None = None
        self._high: float | None = None
        self._low: float | None = None
        self._close: float | None = None
        self._volume = 0.0
        self._count = 0

    def add_trade(self, payload: dict[str, Any]) -> list[BingXMicroBar]:
        price = _as_float(payload.get("p") or payload.get("price") or payload.get("lastPrice"))
        qty = _as_float(payload.get("q") or payload.get("qty") or payload.get("quantity"))
        event_time = _as_int(payload.get("T") or payload.get("time") or payload.get("ts"))
        if price is None or qty is None or event_time is None:
            return []
        bucket = (event_time // 1000) * 1000
        emitted: list[BingXMicroBar] = []
        if self._bucket_ms is not None and bucket != self._bucket_ms:
            bar = self._current_bar()
            if bar is not None:
                emitted.append(bar)
            self._reset()
        self._bucket_ms = bucket
        if self._open is None:
            self._open = price
            self._high = price
            self._low = price
        self._high = max(self._high or price, price)
        self._low = min(self._low or price, price)
        self._close = price
        self._volume += max(qty, 0.0)
        self._count += 1
        return emitted

    def flush(self) -> list[BingXMicroBar]:
        bar = self._current_bar()
        self._reset()
        return [bar] if bar is not None else []

    def _current_bar(self) -> BingXMicroBar | None:
        if self._bucket_ms is None or self._open is None or self._close is None:
            return None
        return BingXMicroBar(
            symbol=self._symbol,
            open_time_ms=self._bucket_ms,
            close_time_ms=self._bucket_ms + 999,
            open=self._open,
            high=self._high if self._high is not None else self._open,
            low=self._low if self._low is not None else self._open,
            close=self._close,
            volume=self._volume,
            trade_count=self._count,
        )

    def _reset(self) -> None:
        self._bucket_ms = None
        self._open = None
        self._high = None
        self._low = None
        self._close = None
        self._volume = 0.0
        self._count = 0


class BingXWebSocketHub:
    """Small multi-channel public WebSocket hub for BingX market streams."""

    def __init__(self, url: str = BINGX_WS_MARKET_URL) -> None:
        self._url = url

    async def stream_channel(
        self,
        symbol: str,
        channel_suffix: str,
        *,
        max_messages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if websockets is None:  # pragma: no cover
            raise RuntimeError("websockets package not available; install requirements.txt")
        channel = f"{symbol.strip()}@{channel_suffix}"
        sub_payload = {"id": f"qa-{uuid.uuid4().hex[:12]}", "reqType": "sub", "dataType": channel}
        async with websockets.connect(self._url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps(sub_payload))
            logger.info("bingx_ws_hub.subscribed channel=%s", channel)
            count = 0
            try:
                async for raw in ws:
                    decoded = _decode_ws_frame(raw)
                    if decoded is None:
                        continue
                    if decoded.get("ping"):
                        await ws.send(json.dumps({"pong": decoded["ping"]}))
                        continue
                    yield decoded
                    count += 1
                    if max_messages is not None and count >= max_messages:
                        break
            except ConnectionClosed as exc:  # pragma: no cover - network-dependent
                logger.warning("bingx_ws_hub.connection_closed channel=%s error=%s", channel, exc)

    async def stream_micro_bars(
        self,
        symbol: str,
        *,
        max_messages: int | None = None,
    ) -> AsyncIterator[BingXMicroBar]:
        aggregator = TickAggregator(symbol)
        async for payload in self.stream_channel(symbol, "trade", max_messages=max_messages):
            for raw_trade in _extract_trades(payload):
                for bar in aggregator.add_trade(raw_trade):
                    yield bar
        for bar in aggregator.flush():
            yield bar

    async def stream_depth(
        self,
        symbol: str,
        *,
        depth_level: int = 20,
        max_messages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized order-book dicts from ``{symbol}@depth{N}`` frames."""
        suffix = f"depth{max(5, min(int(depth_level), 100))}"
        async for payload in self.stream_channel(symbol, suffix, max_messages=max_messages):
            book = parse_depth_ws_frame(payload)
            if book is not None:
                yield book

    async def stream_trades(
        self,
        symbol: str,
        *,
        max_messages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for payload in self.stream_channel(symbol, "trade", max_messages=max_messages):
            for trade in _extract_trades(payload):
                yield trade

    async def stream_book_ticker(
        self,
        symbol: str,
        *,
        max_messages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for payload in self.stream_channel(symbol, "bookTicker", max_messages=max_messages):
            row = _extract_book_ticker(payload)
            if row is not None:
                yield row


async def probe_ws_channel(
    hub: BingXWebSocketHub,
    symbol: str,
    channel_suffix: str,
) -> bool:
    """Return True if BingX streams usable payloads on the public channel."""
    try:
        async for payload in hub.stream_channel(symbol, channel_suffix, max_messages=3):
            code = payload.get("code")
            if code not in (None, 0, "0"):
                return False
            if channel_suffix.startswith("depth") and parse_depth_ws_frame(payload) is not None:
                return True
            if channel_suffix == "trade" and _extract_trades(payload):
                return True
            if channel_suffix == "bookTicker" and _extract_book_ticker(payload) is not None:
                return True
    except Exception as exc:
        logger.debug(
            "bingx_ws_hub.probe_failed symbol=%s channel=%s error=%s",
            symbol,
            channel_suffix,
            str(exc)[:120],
        )
    return False


def parse_depth_ws_frame(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a BingX ``@depth`` WebSocket frame to REST-compatible book dict."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    parsed_bids = _parse_depth_levels(data.get("bids"))
    parsed_asks = _parse_depth_levels(data.get("asks"))
    if not parsed_bids or not parsed_asks:
        return None
    data_type = str(payload.get("dataType") or "")
    symbol = data_type.split("@", 1)[0] if "@" in data_type else ""
    ts = int(payload.get("timestamp") or time.time() * 1000)
    return {
        "symbol": symbol,
        "bids": data.get("bids", []),
        "asks": data.get("asks", []),
        "parsed_bids": parsed_bids,
        "parsed_asks": parsed_asks,
        "timestamp_ms": ts,
        "source": "bingx_ws_depth",
        "last_update_id": data.get("lastUpdateId"),
    }


def _extract_book_ticker(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    symbol = str(data.get("s") or data.get("symbol") or "").strip()
    bid_px = _as_float(data.get("b") or data.get("bidPrice"))
    bid_qty = _as_float(data.get("B") or data.get("bidQty"))
    ask_px = _as_float(data.get("a") or data.get("askPrice"))
    ask_qty = _as_float(data.get("A") or data.get("askQty"))
    if not symbol or bid_px is None or ask_px is None:
        return None
    ts = int(data.get("T") or data.get("time") or payload.get("timestamp") or time.time() * 1000)
    return {
        "symbol": symbol,
        "best_bid_price": bid_px,
        "best_bid_size": bid_qty or 0.0,
        "best_ask_price": ask_px,
        "best_ask_size": ask_qty or 0.0,
        "timestamp_ms": ts,
        "source": "bingx_ws_book_ticker",
    }


def _extract_trades(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", payload)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
