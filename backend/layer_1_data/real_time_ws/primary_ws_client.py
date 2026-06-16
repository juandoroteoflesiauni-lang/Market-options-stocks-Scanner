from typing import Any
"""
backend/layer_1_data/real_time_ws/primary_ws_client.py
════════════════════════════════════════════════════════════════════════════════
Primary (Matba Rofex) WebSocket Client — Using official pyRofex SDK.
════════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pyRofex

from backend.config.settings import load_settings
from backend.domain.primary_models import PrimaryMarketData

logger = logging.getLogger("backend.layer_1_data.real_time_ws.primary")


class PrimaryWSClient:
    """
    WebSocket client for Primary API (Matba Rofex) using pyRofex SDK.
    Provides real-time Tick data (L1) and Pydantic-based normalization.
    """

    def __init__(self) -> None:
        self.settings = load_settings()
        self._running = False
        self._initialized = False
        self.on_message_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self.subscriptions: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def _ensure_initialized(self):
        """Native pyRofex initialization (Environment: REMARKETS)."""
        if not self._initialized:
            try:
                pyRofex.initialize(
                    user=self.settings.primary_user,
                    password=self.settings.primary_password,
                    account=self.settings.primary_account,
                    environment=pyRofex.Environment.REMARKET,
                )
                self._initialized = True
                logger.info("pyRofex SDK initialized (REMARKETS) for WebSocket.")
            except Exception as e:
                logger.error(f"Failed to initialize pyRofex for WS: {e}")
                raise

    def market_data_handler(self, message: dict[str, Any]):
        """
        Handler para mensajes de Market Data de pyRofex.
        Crucial: Pasa el diccionario en bruto por los modelos Pydantic V2.
        """
        if not self.on_message_callback or not self._loop:
            return

        try:
            md_raw = message.get("marketData")
            inst_id = message.get("instrumentId")
            if not md_raw or not inst_id:
                return

            # Inyectar identificación de instrumento para validación Pydantic
            md_raw["symbol"] = inst_id.get("symbol")
            md_raw["marketId"] = inst_id.get("marketId")

            # Validación e Inmutabilidad Institucional vía Pydantic V2
            primary_md = PrimaryMarketData(**md_raw)

            # Normalización a formato interno Tick
            normalized = {
                "source": "PRIMARY_SDK",
                "symbol": primary_md.symbol,
                "bid": primary_md.bids[0].price if primary_md.bids else None,
                "bid_size": primary_md.bids[0].size if primary_md.bids else None,
                "ask": primary_md.asks[0].price if primary_md.asks else None,
                "ask_size": primary_md.asks[0].size if primary_md.asks else None,
                "last": primary_md.last.price if primary_md.last else None,
                "last_size": primary_md.last.size if primary_md.last else None,
                "volume": primary_md.volume,
                "timestamp": self._loop.time(),
            }

            # Puente seguro entre el thread del SDK y el loop de asyncio
            asyncio.run_coroutine_threadsafe(self.on_message_callback(normalized), self._loop)
        except Exception as e:
            logger.error(f"Error in pyRofex market_data_handler: {e}")

    def error_handler(self, message: dict[str, Any]):
        """Handler para errores del SDK."""
        logger.error(f"pyRofex WS Error: {message}")

    async def connect(self):
        """Inicia la conexión WebSocket nativa del SDK."""
        self._ensure_initialized()
        self._loop = asyncio.get_event_loop()

        try:
            logger.info("Iniciando conexión WebSocket pyRofex...")
            pyRofex.init_websocket_connection(
                market_data_handler=self.market_data_handler, error_handler=self.error_handler
            )
            self._running = True
            logger.info("pyRofex WebSocket conectado exitosamente.")

            if self.subscriptions:
                await self.subscribe(self.subscriptions)
        except Exception as e:
            logger.error(f"Failed to connect pyRofex WS: {e}")
            raise

    async def subscribe(self, symbols: list[str]):
        """Suscribe instrumentos usando pyRofex."""
        self._ensure_initialized()

        # Filtramos duplicados que ya estén en self.subscriptions
        new_symbols = [s for s in symbols if s not in self.subscriptions]
        if not new_symbols:
            # Si ya estamos suscritos, igual llamamos al SDK por si acaso se perdió el estado
            new_symbols = symbols
        else:
            self.subscriptions.extend(new_symbols)

        try:
            logger.info(f"Suscribiendo pyRofex a: {new_symbols}")
            pyRofex.market_data_subscription(
                tickers=new_symbols,
                entries=[
                    pyRofex.MarketDataEntry.BIDS,
                    pyRofex.MarketDataEntry.OFFERS,
                    pyRofex.MarketDataEntry.LAST,
                    pyRofex.MarketDataEntry.TRADE_VOLUME,
                ],
            )
        except Exception as e:
            logger.error(f"Error subscribing via pyRofex: {e}")

    async def start(self, callback: Callable[[dict[str, Any]], Awaitable[None]]):
        """Inicia el cliente y mantiene el loop."""
        self.on_message_callback = callback
        await self.connect()

        while self._running:
            await asyncio.sleep(1)

    async def stop(self):
        """Cierra la conexión del SDK."""
        self._running = False
        try:
            pyRofex.close_websocket_connection()
            logger.info("pyRofex WebSocket cerrado.")
        except:
            pass


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : primary_ws_client.py
# Sub-capa         : WebSocket (SDK)
# Enfoque          : Implementación nativa usando pyRofex SDK.
# ─────────────────────────────────────────────────────────────────────
