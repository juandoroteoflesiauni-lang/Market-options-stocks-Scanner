"""
backend/layer_1_data/real_time_ws/data912_rest_poller.py
════════════════════════════════════════════════════════════════════════════════
Data912 REST Poller — Asynchronous polling engine for market data.
════════════════════════════════════════════════════════════════════════════════
Simulates a real-time stream by periodically fetching REST endpoints.
Includes robust rate-limiting and exponential backoff.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from backend.domain.data912_models import Data912LiveQuote
from backend.layer_1_data.fetchers.data912_fetcher import Data912Fetcher

logger = logging.getLogger("backend.layer_1_data.real_time_ws.data912_poller")


class Data912RestPoller:
    """
    Simulates a WebSocket stream by polling Data912 REST endpoints.

    Design:
    - Interval: 20s (aligned with provider's refresh rate).
    - Rate Limiting: Strict 120 req/min limit management.
    - Backoff: Exponential wait on 429 (Too Many Requests).
    """

    def __init__(self, interval: float = 20.0) -> None:
        self._fetcher = Data912Fetcher()
        self._interval = interval
        self._running = False
        self._on_message_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._subscriptions: list[str] = []
        self._backoff_time = 0.0
        self._max_backoff = 60.0

    async def start(self, callback: Callable[[dict[str, Any]], Awaitable[None]]):
        """Start the polling loop."""
        self._on_message_callback = callback
        self._running = True
        logger.info(f"Data912 Poller started. Interval: {self._interval}s")

        while self._running:
            try:
                if self._backoff_time > 0:
                    logger.warning(f"Data912 Backoff active. Waiting {self._backoff_time}s...")
                    await asyncio.sleep(self._backoff_time)
                    self._backoff_time = 0.0

                start_time = time.time()

                # Polling cycles (fetching panels we need)
                # Note: We fetch the whole panel and filter by subscriptions to save requests
                await self._poll_cycle()

                # Calculate sleep time to maintain interval
                elapsed = time.time() - start_time
                sleep_time = max(0, self._interval - elapsed)
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error in Data912 Poller loop: {e}")
                await asyncio.sleep(self._interval)

    async def _poll_cycle(self):
        """Execute a single polling cycle across active panels."""
        if not self._subscriptions:
            return

        # Fetch CEDEARs as primary source (requested by user)
        quotes = await self._fetcher.get_live_cedears()
        if quotes is None:
            # Handle possible 429 in fetcher (if it returns None on HTTP error)
            self._backoff_time = min(self._max_backoff, (self._backoff_time + 5.0) * 1.5)
            return

        for quote in quotes:
            if quote.symbol in self._subscriptions:
                normalized = self._normalize_quote(quote)
                if normalized and self._on_message_callback:
                    await self._on_message_callback(normalized)

    def _normalize_quote(self, quote: Data912LiveQuote) -> dict[str, Any]:
        """Normalize Data912 model to internal Tick format (consistent with Primary)."""
        return {
            "source": "DATA912_POLLER",
            "symbol": quote.symbol,
            "price": quote.price,
            "size": quote.size,
            "side": "TRADE",  # Data912 REST is mostly last trade price
            "type": "T",
            "timestamp": time.time(),
        }

    async def subscribe(self, symbols: list[str]):
        """Update subscription list."""
        self._subscriptions = list(set(self._subscriptions + symbols))
        logger.info(f"Data912 Poller subscribed to: {self._subscriptions}")

    async def stop(self):
        """Stop the poller."""
        self._running = False
        logger.info("Data912 Poller stopped.")


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : data912_rest_poller.py
# Sub-capa         : WebSocket Simulator (Polling)
# Enfoque          : Motor de polling asíncrono para Data912.
# ─────────────────────────────────────────────────────────────────────
