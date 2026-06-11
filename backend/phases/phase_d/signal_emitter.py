"""Emisor de señales de ejecución para Phase D.

Analiza ticks en tiempo real y genera ExecutionSignals basándose en:
- Momentum (cambio de precio acumulado)
- Volatilidad (rangos y desviaciones)
- Volume spike (picos de volumen)
- VWAP (Volume Weighted Average Price)
- Confluencia con scores de Phase C
"""

from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal
from typing import Any

from backend.models.execution_signal import (
    ExecutionSignal,
    SignalStrength,
    SignalType,
    TickAnalysis,
)
from backend.models.market_snapshot import DataLineage
from backend.models.option_contract import TopOptionSelection
from backend.phases.phase_d.tick_buffer import TickBuffer

logger = logging.getLogger(__name__)


# Configuración por defecto del emisor
DEFAULT_EMITTER_CONFIG: dict[str, Any] = {
    "momentum_window": 20,
    "volatility_window": 30,
    "volume_spike_threshold": 2.5,
    "entry_momentum_threshold": 0.003,
    "exit_momentum_threshold": -0.002,
    "stop_loss_pct": 0.02,
    "take_profit_pct": 0.04,
    "min_confidence": 0.60,
    "cooldown_seconds": 30,
    "min_ticks_for_signal": 10,
}


class SignalEmitter:
    """Motor de generación de señales de ejecución.

    Analiza ticks en tiempo real y produce ExecutionSignals cuando
    se cumplen los criterios de entrada/salida.
    """

    def __init__(
        self,
        selections: list[TopOptionSelection],
        config: dict[str, Any] | None = None,
    ) -> None:
        self._config = {**DEFAULT_EMITTER_CONFIG, **(config or {})}
        self._selections = {s.ticker: s for s in selections}
        self._buffers: dict[str, TickBuffer] = {}
        self._last_signal_time: dict[str, float] = {}
        self._contract_map: dict[str, str] = {}

        for sel in selections:
            for contract in sel.selected_contracts:
                self._contract_map[contract.contract_symbol] = sel.ticker
                self._buffers[contract.contract_symbol] = TickBuffer()

        logger.info(
            "SignalEmitter initialized for %d contracts across %d tickers",
            len(self._contract_map),
            len(selections),
        )

    def process_tick(
        self,
        contract_symbol: str,
        price: float,
        volume: int,
        timestamp: float | None = None,
    ) -> TickAnalysis | None:
        """Procesa un tick individual y retorna análisis + posible señal.

        Args:
            contract_symbol: Símbolo del contrato.
            price: Precio del tick.
            volume: Volumen del tick.
            timestamp: Timestamp Unix del tick.

        Returns:
            TickAnalysis con resultado o None si no aplica.
        """
        if contract_symbol not in self._buffers:
            return None

        ts = timestamp or time.time()
        buffer = self._buffers[contract_symbol]
        buffer.add(price, volume, ts)

        if buffer.count < self._config["min_ticks_for_signal"]:
            return None

        momentum = buffer.price_change_pct(self._config["momentum_window"])
        volatility = buffer.volatility(self._config["volatility_window"])
        vwap = buffer.vwap()
        vol_spike = buffer.volume_spike(self._config["volume_spike_threshold"])

        underlying = self._contract_map.get(contract_symbol, "")
        selection = self._selections.get(underlying)

        signal = None
        signal_generated = False

        if self._should_emit_signal(contract_symbol, momentum, volatility, vol_spike, selection):
            signal = self._generate_signal(
                contract_symbol=contract_symbol,
                price=price,
                momentum=momentum,
                volatility=volatility,
                vwap=vwap,
                vol_spike=vol_spike,
                selection=selection,
            )
            if signal:
                signal_generated = True
                self._last_signal_time[contract_symbol] = ts

        return TickAnalysis(
            contract_symbol=contract_symbol,
            price=Decimal(str(price)),
            volume=volume,
            vwap=vwap,
            price_change_pct=momentum,
            momentum_score=min(abs(momentum) * 1000, 100.0),
            volatility_score=min(volatility * 10000, 100.0),
            signal_generated=signal_generated,
            signal=signal,
        )

    def _should_emit_signal(
        self,
        contract_symbol: str,
        momentum: float,
        volatility: float,
        vol_spike: bool,
        selection: TopOptionSelection | None,
    ) -> bool:
        """Determina si se debe emitir una señal."""
        last_signal = self._last_signal_time.get(contract_symbol, 0)
        if time.time() - last_signal < self._config["cooldown_seconds"]:
            return False

        entry_threshold = self._config["entry_momentum_threshold"]
        if abs(momentum) >= entry_threshold:
            return True

        if vol_spike and abs(momentum) >= entry_threshold * 0.5:
            return True

        return bool(
            selection
            and selection.confidence >= self._config["min_confidence"]
            and abs(momentum) >= entry_threshold * 0.7
        )

    def _generate_signal(
        self,
        contract_symbol: str,
        price: float,
        momentum: float,
        volatility: float,
        vwap: float,
        vol_spike: bool,
        selection: TopOptionSelection | None,
    ) -> ExecutionSignal | None:
        """Genera una señal de ejecución."""
        underlying = self._contract_map.get(contract_symbol, "")
        entry_threshold = self._config["entry_momentum_threshold"]

        if momentum >= entry_threshold:
            signal_type = SignalType.ENTRY_LONG
            direction = "LONG"
        elif momentum <= -entry_threshold:
            signal_type = SignalType.ENTRY_SHORT
            direction = "SHORT"
        elif vol_spike and momentum > 0:
            signal_type = SignalType.SCALP_LONG
            direction = "LONG"
        elif vol_spike and momentum < 0:
            signal_type = SignalType.SCALP_SHORT
            direction = "SHORT"
        else:
            return None

        strength = self._classify_strength(momentum, volatility, vol_spike)
        confidence = self._compute_confidence(momentum, volatility, vol_spike, selection)

        stop_loss_pct = self._config["stop_loss_pct"]
        take_profit_pct = self._config["take_profit_pct"]

        if direction == "LONG":
            stop_loss = price * (1 - stop_loss_pct)
            take_profit = price * (1 + take_profit_pct)
        else:
            stop_loss = price * (1 + stop_loss_pct)
            take_profit = price * (1 - take_profit_pct)

        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        rr_ratio = reward / max(risk, 1e-8)

        engine_scores: dict[str, float] = {}
        if selection:
            engine_scores = selection.engine_scores

        trigger_parts = []
        if abs(momentum) >= entry_threshold:
            trigger_parts.append(f"Momentum {momentum:+.4%}")
        if vol_spike:
            trigger_parts.append("Volume spike")
        if vwap > 0 and price > vwap:
            trigger_parts.append("Price > VWAP")
        elif vwap > 0 and price < vwap:
            trigger_parts.append("Price < VWAP")

        return ExecutionSignal(
            signal_id=str(uuid.uuid4())[:8],
            contract_symbol=contract_symbol,
            underlying_ticker=underlying,
            signal_type=signal_type,
            strength=strength,
            direction=direction,
            entry_price=Decimal(str(round(price, 2))),
            current_price=Decimal(str(round(price, 2))),
            stop_loss_price=Decimal(str(round(stop_loss, 2))),
            take_profit_price=Decimal(str(round(take_profit, 2))),
            confidence=confidence,
            expected_move_pct=abs(momentum) * 100,
            risk_reward_ratio=rr_ratio,
            trigger_reason=" | ".join(trigger_parts) if trigger_parts else "Momentum threshold",
            engine_scores=engine_scores,
            data_lineage=DataLineage(
                source="phase_d_emitter",
                ingestion_latency_ms=0,
                raw_field_count=0,
            ),
        )

    def _classify_strength(
        self,
        momentum: float,
        volatility: float,
        vol_spike: bool,
    ) -> SignalStrength:
        """Clasifica la fuerza de la señal."""
        score = abs(momentum) * 1000

        if vol_spike:
            score *= 1.5
        if volatility > 0.005:
            score *= 1.2

        if score >= 8:
            return SignalStrength.CRITICAL
        elif score >= 5:
            return SignalStrength.STRONG
        elif score >= 3:
            return SignalStrength.MODERATE
        else:
            return SignalStrength.WEAK

    def _compute_confidence(
        self,
        momentum: float,
        volatility: float,
        vol_spike: bool,
        selection: TopOptionSelection | None,
    ) -> float:
        """Calcula la confianza de la señal."""
        base = 0.5

        momentum_boost = min(abs(momentum) * 50, 0.25)
        base += momentum_boost

        if vol_spike:
            base += 0.10

        if selection:
            base += selection.confidence * 0.15

        return round(min(max(base, 0.0), 1.0), 4)

    def get_buffer_stats(self, contract_symbol: str) -> dict[str, float] | None:
        """Retorna estadísticas del buffer para un contrato."""
        buffer = self._buffers.get(contract_symbol)
        if not buffer or buffer.count == 0:
            return None

        return {
            "count": buffer.count,
            "last_price": buffer.last_price or 0.0,
            "vwap": buffer.vwap(),
            "price_change_pct": buffer.price_change_pct(),
            "volatility": buffer.volatility(),
        }
