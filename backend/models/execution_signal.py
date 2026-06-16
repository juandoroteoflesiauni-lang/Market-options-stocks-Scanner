from __future__ import annotations
"""Modelo de señales de ejecución para Phase D.

Define el esquema Pydantic v2 frozen para ExecutionSignal,
la señal que se emite al frontend cuando se detecta una oportunidad de ejecución.
"""


from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.market_snapshot import DataLineage


class SignalType(str, Enum):
    """Tipos de señales de ejecución."""

    ENTRY_LONG = "ENTRY_LONG"
    ENTRY_SHORT = "ENTRY_SHORT"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    SCALP_LONG = "SCALP_LONG"
    SCALP_SHORT = "SCALP_SHORT"


class SignalStrength(str, Enum):
    """Fuerza de la señal."""

    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    CRITICAL = "CRITICAL"


class ExecutionSignal(BaseModel):
    """Señal de ejecución inmutable generada por Phase D.

    Representa una acción recomendada (entrada, salida, stop loss, take profit)
    basada en el análisis tick-by-tick de los 5 contratos monitoreados.
    """

    model_config = ConfigDict(frozen=True)

    # Identificación
    signal_id: str
    contract_symbol: str
    underlying_ticker: str

    # Señal
    signal_type: SignalType
    strength: SignalStrength
    direction: Literal["LONG", "SHORT", "NEUTRAL"]

    # Precios
    entry_price: Decimal = Field(gt=Decimal("0"))
    current_price: Decimal = Field(gt=Decimal("0"))
    stop_loss_price: Decimal | None = None
    take_profit_price: Decimal | None = None

    # Métricas de la señal
    confidence: float = Field(ge=0.0, le=1.0)
    expected_move_pct: float = Field(default=0.0)
    risk_reward_ratio: float = Field(default=0.0, ge=0.0)

    # Contexto del motor
    trigger_reason: str
    engine_scores: dict[str, float] = Field(default_factory=dict)

    # Metadatos
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data_lineage: DataLineage

    @field_validator("underlying_ticker")
    @classmethod
    def ticker_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("contract_symbol")
    @classmethod
    def symbol_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    @property
    def is_entry(self) -> bool:
        return self.signal_type in (
            SignalType.ENTRY_LONG,
            SignalType.ENTRY_SHORT,
            SignalType.SCALP_LONG,
            SignalType.SCALP_SHORT,
        )

    @property
    def is_exit(self) -> bool:
        return self.signal_type in (
            SignalType.EXIT_LONG,
            SignalType.EXIT_SHORT,
            SignalType.STOP_LOSS,
            SignalType.TAKE_PROFIT,
        )

    def to_websocket_payload(self) -> dict[str, object]:
        """Convierte la señál a formato dict para broadcast WebSocket."""
        return {
            "signal_id": self.signal_id,
            "type": "execution_signal",
            "contract_symbol": self.contract_symbol,
            "underlying": self.underlying_ticker,
            "signal_type": self.signal_type.value,
            "strength": self.strength.value,
            "direction": self.direction,
            "entry_price": str(self.entry_price),
            "current_price": str(self.current_price),
            "stop_loss": str(self.stop_loss_price) if self.stop_loss_price else None,
            "take_profit": str(self.take_profit_price) if self.take_profit_price else None,
            "confidence": round(self.confidence, 4),
            "expected_move_pct": round(self.expected_move_pct, 2),
            "risk_reward_ratio": round(self.risk_reward_ratio, 2),
            "trigger_reason": self.trigger_reason,
            "timestamp": self.timestamp.isoformat(),
        }


class TickAnalysis(BaseModel):
    """Resultado del análisis de un tick individual por el SignalEmitter."""

    model_config = ConfigDict(frozen=True)

    contract_symbol: str
    price: Decimal
    volume: int
    vwap: float
    price_change_pct: float
    momentum_score: float
    volatility_score: float
    signal_generated: bool = False
    signal: ExecutionSignal | None = None
