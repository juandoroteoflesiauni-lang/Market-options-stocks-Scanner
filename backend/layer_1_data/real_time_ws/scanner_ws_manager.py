"""
Global WebSocket manager for Market Scanner live prices using Alpaca WS.
"""

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.settings import load_settings
from backend.layer_1_data.real_time_ws.alpaca_ws_client import AlpacaWSClient

logger = get_logger(__name__)


@dataclass(frozen=True)
class ScannerLivePrice:
    """Fresh display price cached from the scanner WebSocket stream."""

    price: float
    change_pct: float | None = None
    source: str = "alpaca_ws"
    timestamp_ms: int | None = None


class ScannerWSManager:
    _instance: "ScannerWSManager | None" = None

    def __init__(self) -> None:
        self._client: AlpacaWSClient | None = None
        self._live_prices: dict[str, ScannerLivePrice] = {}
        self._running_task: asyncio.Task | None = None
        self._subscribed_symbols: set[str] = set()
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "ScannerWSManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self) -> None:
        settings = load_settings()
        if not settings.alpaca_api_key or not settings.alpaca_secret_key:
            logger.warning("scanner_ws_manager: Missing Alpaca keys. WebSockets disabled.")
            return

        self._client = AlpacaWSClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            bars_feed=settings.alpaca_bars_feed,
            data_rest_base=settings.alpaca_data_base_url,
        )

        async def _on_message(msg: dict[str, Any]) -> None:
            sym = msg.get("sym")
            if not sym or not isinstance(sym, str):
                return
            sym = sym.upper()

            ev = msg.get("ev")
            price: float | None = None
            timestamp_ms: int = int(time.time() * 1000)

            if ev == "T":
                p = msg.get("price")
                if p is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        price = float(p)
            elif ev == "AM":
                c = msg.get("c")
                s = msg.get("s")
                if c is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        price = float(c)
                        if s is not None:
                            timestamp_ms = int(s)

            if price is not None and price > 0:
                # Actualizar precio vivo
                existing = self._live_prices.get(sym)
                # Mantener change_pct existente si existe, ya que el WS no nos da close previo
                change_pct = existing.change_pct if existing else None

                self._live_prices[sym] = ScannerLivePrice(
                    price=price,
                    change_pct=change_pct,
                    source="alpaca_ws",
                    timestamp_ms=timestamp_ms,
                )

        self._running_task = asyncio.create_task(self._client.start(_on_message))
        logger.info("scanner_ws_manager: Started Alpaca WS Client in background.")

    async def stop(self) -> None:
        if self._client:
            await self._client.stop()
        if self._running_task:
            self._running_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._running_task
        self._subscribed_symbols.clear()
        logger.info("scanner_ws_manager: Stopped.")

    async def subscribe_symbols(self, symbols: list[str]) -> None:
        if not self._client:
            return

        to_subscribe = []
        async with self._lock:
            for s in symbols:
                sym = s.upper().strip()
                if sym and sym not in self._subscribed_symbols:
                    self._subscribed_symbols.add(sym)
                    to_subscribe.append(sym)

        if to_subscribe:
            # Nos suscribimos a trades ('trades') y barras por minuto ('bars' = AM)
            # para máxima reactividad
            await self._client.subscribe(to_subscribe, channels=["trades", "bars"])
            logger.debug(
                f"scanner_ws_manager: Subscribed to {len(to_subscribe)} new symbols: {to_subscribe}"
            )

    def get_price(self, symbol: str) -> ScannerLivePrice | None:
        return self._live_prices.get(symbol.upper().strip())

    def update_change_pct(self, symbol: str, change_pct: float) -> None:
        sym = symbol.upper().strip()
        existing = self._live_prices.get(sym)
        if existing:
            self._live_prices[sym] = ScannerLivePrice(
                price=existing.price,
                change_pct=change_pct,
                source=existing.source,
                timestamp_ms=existing.timestamp_ms,
            )


# Instancia global exportada
scanner_ws_manager = ScannerWSManager.get_instance()
