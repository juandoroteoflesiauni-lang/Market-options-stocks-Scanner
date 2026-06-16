"""Contrato de señal canónica unificada. # [PD-2][PD-4][TH][IM]"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CanonicalLegSpec(BaseModel):
    """Especificación de una pata individual para instrumentos estructurados/multi-pata."""

    model_config = ConfigDict(frozen=True)

    contract_symbol: str = Field(description="Símbolo del contrato de opción u otro derivado")
    side: Literal["buy", "sell"]
    ratio: int = Field(default=1, ge=1, description="Ratio/Multiplicador de tamaño de la pata")

    @field_validator("contract_symbol")
    @classmethod
    def clean_contract_symbol(cls, value: str) -> str:
        return value.upper().strip()


class CanonicalSignalPayload(BaseModel):
    """Contrato unificado y agnóstico de señal para la Funding Decision Layer."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="Símbolo del activo subyacente")
    asset_type: Literal["equity", "option", "future", "crypto", "cash", "other"]
    direction: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    entry_price: Decimal = Field(gt=Decimal("0"), description="Precio de entrada proyectado del subyacente")
    stop_loss_price: Optional[Decimal] = Field(default=None, gt=Decimal("0"))
    max_loss_usd: Optional[Decimal] = Field(default=None, gt=Decimal("0"), description="Pérdida máxima estimada de la estructura")
    structure: str = Field(description="Nombre de la estructura o playbook (ej. 'long_call', 'call_debit_spread', 'linear')")
    legs: tuple[CanonicalLegSpec, ...] = Field(default_factory=tuple)
    source_engine: str = Field(default="omni_engine", description="Identificador del motor origen")
    timestamp: datetime
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, value: str) -> str:
        return value.upper().strip()

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value
