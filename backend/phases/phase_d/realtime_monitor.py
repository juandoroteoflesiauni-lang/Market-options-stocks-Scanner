from __future__ import annotations
from typing import Any
"""Monitor en tiempo real para Phase D.

Consume ticks de Massive WebSocket para los 5 contratos seleccionados
por Phase C, los pasa al SignalEmitter para generar señales de ejecución,
y publica las señales al EventBus y al frontend via broadcast.
"""


import asyncio
import contextlib
import json
import logging
from decimal import Decimal

import websockets

from backend.bus.event_bus import EventBus
from backend.models.execution_signal import ExecutionSignal
from backend.models.option_contract import TopOptionSelection
from backend.phases.phase_d.signal_emitter import SignalEmitter

logger = logging.getLogger(__name__)

# Configuración por defecto
DEFAULT_MONITOR_CONFIG: dict[str, Any] = {
    "reconnect_delay": 3.0,
    "max_reconnect_delay": 30.0,
    "heartbeat_interval": 15.0,
    "tick_timeout": 60.0,
}


class RealtimeMonitor:
    """Monitor en tiempo real de Phase D.

    Se suscribe a los ticks de los contratos seleccionados por Phase C
    usando Massive WebSocket, procesa los ticks con el SignalEmitter,
    y publica las señales al EventBus.
    """

    def __init__(
        self,
        massive_ws_url: str,
        massive_api_key: str,
        selections: list[TopOptionSelection],
        event_bus: EventBus,
        broadcast_fn: Any = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._ws_url = massive_ws_url
        self._api_key = massive_api_key
        self._selections = selections
        self._bus = event_bus
        self._broadcast_fn = broadcast_fn
        self._config = {**DEFAULT_MONITOR_CONFIG, **(config or {})}

        self._emitter = SignalEmitter(selections=selections)
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._reconnect_delay = self._config["reconnect_delay"]

        self._symbols = self._extract_symbols()
        self._signals_buffer: list[ExecutionSignal] = []
        self._max_buffer_size = 100

        logger.info(
            "RealtimeMonitor initialized for %d contracts",
            len(self._symbols),
        )

    def _extract_symbols(self) -> list[str]:
        """Extrae los símbolos de contratos de las selecciones."""
        symbols: list[str] = []
        for sel in self._selections:
            for contract in sel.selected_contracts:
                symbols.append(contract.contract_symbol)
        return symbols

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def signals_buffer(self) -> list[ExecutionSignal]:
        return list(self._signals_buffer)

    async def start(self) -> None:
        """Inicia el monitor en background con auto-reconnect."""
        self._running = True
        logger.info("RealtimeMonitor starting for %d symbols", len(self._symbols))

        while self._running:
            try:
                await self._connect_and_consume()
            except asyncio.CancelledError:
                self._running = False
                logger.info("RealtimeMonitor cancelled")
                raise
            except Exception as e:
                logger.error(
                    "RealtimeMonitor error: %s. Reconnecting in %.1fs...",
                    e,
                    self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    self._config["max_reconnect_delay"],
                )

    async def stop(self) -> None:
        """Detiene el monitor gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("RealtimeMonitor stopped. %d signals generated", len(self._signals_buffer))

    async def _connect_and_consume(self) -> None:
        """Conecta a Massive WebSocket y consume ticks."""
        async with websockets.connect(
            self._ws_url,
            additional_headers={"Authorization": f"Bearer {self._api_key}"},
        ) as ws:
            logger.info("Connected to Massive WebSocket")

            self._reconnect_delay = self._config["reconnect_delay"]

            await self._subscribe(ws)

            while self._running:
                try:
                    data = await asyncio.wait_for(
                        ws.recv(),
                        timeout=self._config["tick_timeout"],
                    )
                    await self._handle_message(data)
                except TimeoutError:
                    logger.warning("Tick timeout — sending heartbeat")
                    await ws.ping()
                except websockets.ConnectionClosed:
                    logger.warning("Massive WebSocket connection closed")
                    break

    async def _subscribe(self, ws: Any) -> None:
        """Suscribe a los símbolos de los contratos."""
        if not self._symbols:
            return

        sub_payload = {
            "action": "subscribe",
            "options": self._symbols,
        }
        await ws.send(json.dumps(sub_payload))
        logger.info("Subscribed to %d option contracts", len(self._symbols))

    async def _handle_message(self, raw_data: str) -> None:
        """Procesa un mensaje del WebSocket."""
        try:
            messages = json.loads(raw_data)
            if not isinstance(messages, list):
                messages = [messages]

            for msg in messages:
                await self._process_tick_message(msg)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from Massive WebSocket")

    async def _process_tick_message(self, msg: dict[str, Any]) -> None:
        """Procesa un tick individual."""
        msg_type = msg.get("T", "")

        if msg_type not in ("t", "trade", "tick"):
            return

        symbol = msg.get("S") or msg.get("symbol") or msg.get("sym", "")
        price = msg.get("p") or msg.get("price")
        volume = msg.get("v") or msg.get("volume", 0)
        timestamp = msg.get("t") or msg.get("timestamp")

        if not symbol or price is None:
            return

        price_decimal = Decimal(str(price))
        volume_int = int(volume)
        ts_float = float(timestamp) if timestamp else None

        analysis = self._emitter.process_tick(
            contract_symbol=symbol,
            price=price_decimal,
            volume=volume_int,
            timestamp=ts_float,
        )

        if analysis and analysis.signal_generated and analysis.signal:
            await self._emit_signal(analysis.signal)

    async def _emit_signal(self, signal: ExecutionSignal) -> None:
        """Publica una señal al EventBus y al frontend."""
        logger.info(
            "SIGNAL EMITTED: %s %s %s @ %s (confidence: %.2f) — %s",
            signal.signal_type.value,
            signal.direction,
            signal.contract_symbol,
            signal.entry_price,
            signal.confidence,
            signal.trigger_reason,
        )

        self._signals_buffer.append(signal)
        if len(self._signals_buffer) > self._max_buffer_size:
            self._signals_buffer.pop(0)

        await self._bus.publish_priority(signal)

        if self._broadcast_fn:
            try:
                await self._broadcast_fn(signal.to_websocket_payload())
            except Exception as e:
                logger.warning("Broadcast failed: %s", e)

    def get_stats(self) -> dict[str, Any]:
        """Retorna estadísticas del monitor."""
        return {
            "running": self._running,
            "symbols_monitored": len(self._symbols),
            "signals_generated": len(self._signals_buffer),
            "last_signal": (
                self._signals_buffer[-1].to_websocket_payload() if self._signals_buffer else None
            ),
            "emitter_buffers": {
                sym: self._emitter.get_buffer_stats(sym) for sym in self._symbols[:5]
            },
        }
