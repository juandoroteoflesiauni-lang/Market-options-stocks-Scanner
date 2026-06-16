from __future__ import annotations
from typing import Any
"""Private BingX account WebSocket manager.

This module stays in Layer 1 because it owns exchange I/O. It exposes decoded
account/order events through an async iterator and keeps the listen key alive.
"""


import asyncio
import json
from collections.abc import AsyncIterator

try:  # pragma: no cover - optional runtime dependency.
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]
    ConnectionClosed = Exception  # type: ignore[assignment,misc]

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import (
    BINGX_WS_MARKET_URL,
    BingXClient,
    _decode_ws_frame,
)

logger = get_logger(__name__)


class BingXAccountWebSocket:
    """Consume private account/order update events using a BingX listen key."""

    def __init__(
        self,
        client: BingXClient,
        *,
        url: str = BINGX_WS_MARKET_URL,
        refresh_seconds: float = 30 * 60,
    ) -> None:
        self._client = client
        self._url = url
        self._refresh_seconds = refresh_seconds
        self._listen_key: str | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    async def stream_events(
        self, *, max_messages: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        if websockets is None:  # pragma: no cover
            raise RuntimeError("websockets package not available; install requirements.txt")
        listen_key = await self._ensure_listen_key()
        ws_url = f"{self._url}?listenKey={listen_key}"
        count = 0
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
            logger.info("bingx_account_ws.connected listen_key_present=%s", bool(listen_key))
            self._refresh_task = asyncio.create_task(self._refresh_loop())
            try:
                async for raw in ws:
                    decoded = _decode_ws_frame(raw)
                    if decoded is None:
                        continue
                    if decoded.get("ping"):
                        await ws.send(json.dumps({"pong": decoded["ping"]}))
                        continue
                    normalized = _normalize_private_event(decoded)
                    if normalized is None:
                        continue
                    yield normalized
                    count += 1
                    if max_messages is not None and count >= max_messages:
                        break
            except ConnectionClosed as exc:  # pragma: no cover
                logger.warning("bingx_account_ws.connection_closed error=%s", exc)
            finally:
                if self._refresh_task is not None:
                    self._refresh_task.cancel()
                    self._refresh_task = None

    async def _ensure_listen_key(self) -> str:
        if self._listen_key is None:
            listen_key = await self._client.create_listen_key()
            if not listen_key:
                raise RuntimeError("BingX private stream did not return a listenKey")
            self._listen_key = listen_key
        return self._listen_key

    async def _refresh_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._refresh_seconds)
                if self._listen_key:
                    await self._client.refresh_listen_key(self._listen_key)
                    logger.debug("bingx_account_ws.listen_key_refreshed")
        except asyncio.CancelledError:
            raise


def _normalize_private_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    event_type = str(data.get("e") or data.get("eventType") or data.get("type") or "")
    if event_type in {"ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE"}:
        return data
    if "a" in data or "o" in data:
        return data
    return data if event_type else None
