"""Massive API WebSocket Client - Real-time stock aggregates and trades."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.client import ClientConnection
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class MassiveWSClient:
    """
    WebSocket client for Massive API (Polygon-compatible protocol).
    Handles real-time aggregates and trades for multiple symbols.
    """

    def __init__(self, api_key: str, base_url: str = "wss://socket.massive.com/v2/stocks"):
        self.api_key = api_key
        self.base_url = base_url
        self.websocket: ClientConnection | None = None
        self.subscriptions: set[str] = set()
        self.on_message_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._running = False
        self._reconnect_delay = 5.0

    async def connect(self):
        """Establish connection and authenticate."""
        try:
            logger.info(f"Connecting to Massive WS: {self.base_url}")
            self.websocket = await websockets.connect(self.base_url)

            # Initial message from Massive is usually a status message
            greeting = await self.websocket.recv()
            logger.debug(f"Massive WS greeting: {greeting}")

            # Authenticate
            auth_msg = {"action": "auth", "params": self.api_key}
            await self.websocket.send(json.dumps(auth_msg))

            auth_resp = await self.websocket.recv()
            logger.info(f"Massive WS auth response: {auth_resp}")

            try:
                auth_data = json.loads(auth_resp)
                if isinstance(auth_data, list) and len(auth_data) > 0:
                    status = auth_data[0].get("status")
                    if status == "auth_failed":
                        message = auth_data[0].get("message", "Unknown auth error")
                        raise Exception(f"Massive Auth Failed: {message}")
            except json.JSONDecodeError:
                pass

            self._running = True

            # Resubscribe if we had active subscriptions
            if self.subscriptions:
                await self._send_subscriptions()

        except Exception as e:
            logger.error(f"Failed to connect to Massive WS: {e}")
            self._running = False
            raise

    async def subscribe(self, channels: list[str]):
        """
        Subscribe to channels. Formats:
        - T.AAPL (Trades)
        - Q.AAPL (Quotes)
        - AM.AAPL (Aggregates per minute)
        - A.AAPL (Aggregates per second)
        """
        for ch in channels:
            self.subscriptions.add(ch)

        if self.websocket and self._running:
            await self._send_subscriptions()

    async def _send_subscriptions(self):
        if not self.subscriptions:
            return

        sub_msg = {"action": "subscribe", "params": ",".join(self.subscriptions)}
        await self.websocket.send(json.dumps(sub_msg))
        logger.info(f"Subscribed to: {self.subscriptions}")

    async def start(self, callback: Callable[[dict[str, Any]], Awaitable[None]]):
        """Start the message loop."""
        self.on_message_callback = callback

        while self._running:
            try:
                if not self.websocket:
                    await self.connect()

                async for message in self.websocket:
                    data = json.loads(message)
                    if isinstance(data, list):
                        for item in data:
                            if self.on_message_callback:
                                await self.on_message_callback(item)
                    else:
                        if self.on_message_callback:
                            await self.on_message_callback(data)

            except (ConnectionClosed, Exception) as e:
                logger.error(
                    f"Massive WS connection error: {e}. Reconnecting in {self._reconnect_delay}s..."
                )
                self.websocket = None
                await asyncio.sleep(self._reconnect_delay)
                # Exponential backoff could be added here

    async def stop(self):
        """Cleanly stop the client."""
        self._running = False
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        logger.info("Massive WS client stopped.")
