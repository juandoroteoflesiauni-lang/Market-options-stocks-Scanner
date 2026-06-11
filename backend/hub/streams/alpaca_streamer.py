import asyncio
import json
import logging
from typing import Any

import websockets

from backend.api.routes.signals import broadcast_signal

logger = logging.getLogger(__name__)


# [PD-3][TH] Alpaca Real-time Streamer
class AlpacaStreamer:
    """Consumes real-time trades from Alpaca IEX stream and broadcasts them."""

    def __init__(self, api_key: str, api_secret: str, universe: list[str], hub: Any = None) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.universe = universe
        self.hub = hub
        self.running = False
        self._task: asyncio.Task[Any] | None = None
        self.ws_url = "wss://stream.data.alpaca.markets/v2/iex"

        # State to track changes for the UI
        self.base_prices: dict[str, float] = {}
        self.prev_prices: dict[str, float] = {}
        self.am_prices: dict[str, float] = {}
        self.am_changes: dict[str, float] = {}
        self.candles: dict[str, list[dict[str, Any]]] = {}

    async def start(self) -> None:
        """Starts the streamer background loop with auto-reconnect."""
        self.running = True
        logger.info("AlpacaStreamer started for %d symbols.", len(self.universe))

        # Pre-seed prices from REST API so UI updates immediately (especially after hours)
        if self.hub:
            after_market_data = {}
            try:
                res_am = await self.hub.get_after_market_quotes(self.universe)
                if res_am.is_success:
                    after_market_data = res_am.unwrap()
            except Exception as e:
                logger.warning("Failed to fetch after-market data: %s", e)

            for sym in self.universe:
                try:
                    res = await self.hub.get_raw_quote(sym)
                    if res.is_success:
                        quote = res.unwrap()
                        price = float(quote.get("price", 0.0))
                        prev_close = float(quote.get("previousClose", price))

                        self.base_prices[sym] = prev_close
                        self.prev_prices[sym] = price

                        price_change = price - prev_close
                        price_change_pct = (
                            ((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0
                        )

                        payload = {
                            "symbol": sym,
                            "price": f"{price:.2f}",
                            "priceChange": f"{price_change:.2f}",
                            "priceChangePct": f"{price_change_pct:.2f}",
                        }

                        am_quote = after_market_data.get(sym)
                        if am_quote and "price" in am_quote:
                            am_price = float(am_quote["price"])
                            am_change_pct = ((am_price - price) / price) * 100 if price > 0 else 0.0
                            self.am_prices[sym] = am_price
                            self.am_changes[sym] = am_change_pct
                            payload["afterMarketPrice"] = f"{am_price:.4f}"
                            payload["afterMarketChangePct"] = f"{am_change_pct:.2f}"

                        await broadcast_signal(payload)
                except Exception as e:
                    logger.warning("Failed to pre-seed %s: %s", sym, e)

            # Fetch intraday candles concurrently
            try:
                tasks = [self.hub.get_intraday_candles(sym, limit=60) for sym in self.universe]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for sym, res in zip(self.universe, results, strict=False):
                    if not isinstance(res, Exception) and res.is_success:
                        candles = res.unwrap()
                        if candles:
                            self.candles[sym] = candles
                            await broadcast_signal({"symbol": sym, "candles": candles})
            except Exception as e:
                logger.warning("Failed to pre-seed candles: %s", e)

        # Start after-market polling
        self._am_task = asyncio.create_task(self._poll_after_market())

        while self.running:
            try:
                await self._connect_and_consume()
            except asyncio.CancelledError:
                self.running = False
                logger.info("AlpacaStreamer cancelled.")
                raise
            except Exception as e:
                logger.error("AlpacaStreamer error: %s. Reconnecting in 3s...", e)
                await asyncio.sleep(3.0)

    async def _poll_after_market(self) -> None:
        """Polls FMP for extended hours ticks since Alpaca websocket is quiet."""
        from datetime import datetime, time
        from zoneinfo import ZoneInfo

        ny_tz = ZoneInfo("America/New_York")

        while self.running:
            try:
                now = datetime.now(ny_tz).time()
                # If outside regular hours (9:30 - 16:00 EST)
                if (now < time(9, 30) or now >= time(16, 0)) and self.hub:
                    res = await self.hub.get_after_market_quotes(self.universe)
                    if res.is_success:
                        am_data = res.unwrap()
                        for sym in self.universe:
                            quote = am_data.get(sym)
                            if quote and "price" in quote:
                                am_price = float(quote["price"])

                                # Compare against base price (regular close)
                                base_price = self.base_prices.get(sym, am_price)
                                am_change_pct = (
                                    ((am_price - base_price) / base_price) * 100
                                    if base_price > 0
                                    else 0.0
                                )

                                # Broadcast if price changed to avoid sending duplicate ticks
                                if self.am_prices.get(sym) != am_price:
                                    self.am_prices[sym] = am_price
                                    self.am_changes[sym] = am_change_pct

                                    payload = {
                                        "symbol": sym,
                                        "afterMarketPrice": f"{am_price:.4f}",
                                        "afterMarketChangePct": f"{am_change_pct:.2f}",
                                    }
                                    await broadcast_signal(payload)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("After-market poll failed: %s", e)

            await asyncio.sleep(5.0)

    async def stop(self) -> None:
        """Gracefully stops the streamer."""
        self.running = False
        if hasattr(self, "_am_task"):
            self._am_task.cancel()
        logger.info("AlpacaStreamer stopped.")

    async def _connect_and_consume(self) -> None:
        async with websockets.connect(self.ws_url) as ws:
            # 1. Wait for connected message
            _ = await ws.recv()

            # 2. Authenticate
            auth_payload = {
                "action": "auth",
                "key": self.api_key,
                "secret": self.api_secret,
            }
            await ws.send(json.dumps(auth_payload))
            auth_resp = await ws.recv()
            auth_data = json.loads(auth_resp)

            if (
                not isinstance(auth_data, list)
                or auth_data[0].get("T") != "success"
                or auth_data[0].get("msg") != "authenticated"
            ):
                raise RuntimeError(f"Alpaca auth failed: {auth_data}")

            logger.info("Alpaca authenticated successfully.")

            # 3. Subscribe to trades
            sub_payload = {"action": "subscribe", "trades": self.universe}
            await ws.send(json.dumps(sub_payload))
            sub_resp = await ws.recv()
            logger.info("Alpaca subscription response: %s", sub_resp)

            # 4. Consume stream
            while self.running:
                data = await ws.recv()
                events = json.loads(data)
                for ev in events:
                    if ev.get("T") == "t":  # Trade event
                        await self._handle_trade(ev)

    async def _handle_trade(self, ev: dict[str, Any]) -> None:
        sym = ev.get("S")
        price = ev.get("p")
        if not sym or not price:
            return

        new_price = float(price)

        if sym not in self.base_prices:
            self.base_prices[sym] = new_price
            self.prev_prices[sym] = new_price

        base_price = self.base_prices[sym]
        old_price = self.prev_prices[sym]

        price_change = new_price - old_price
        # Calculate % change vs base price for the session
        price_change_pct = ((new_price - base_price) / base_price) * 100 if base_price > 0 else 0.0

        self.prev_prices[sym] = new_price

        payload = {
            "symbol": sym,
            "price": f"{new_price:.2f}",
            "priceChange": f"{price_change:.2f}",
            "priceChangePct": f"{price_change_pct:.2f}",
        }

        await broadcast_signal(payload)
