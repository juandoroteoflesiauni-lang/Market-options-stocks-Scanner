from __future__ import annotations
"""Módulo del modelo de datos de mercado alineado con FINOS.

Define los esquemas inmutables de Pydantic v2 para MarketSnapshot y DataLineage,
los cuales sirven como el contrato de datos común a lo largo del pipeline del funnel.
"""


from datetime import UTC, datetime
from decimal import Decimal

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator


class UniverseType(str, Enum):
    """Define a qué universo pertenece el activo para enrutarlo en el pipeline."""
    SHARED = "SHARED"
    ALPACA_EXTENDED = "ALPACA_EXTENDED"


class DataLineage(BaseModel):
    """Realiza el seguimiento de la procedencia de cada MarketSnapshot.

    Un snapshot sin linaje es un "huérfano" y será rechazado por cualquier
    motor de procesamiento en las fases posteriores.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    ingestion_latency_ms: int = Field(ge=0)
    raw_field_count: int = Field(ge=0)


class OHLCVBar(BaseModel):
    """Una vela OHLCV individual para el resumen de Phase A."""

    model_config = ConfigDict(frozen=True)

    time: str
    open: Decimal = Field(ge=Decimal("0"))
    high: Decimal = Field(ge=Decimal("0"))
    low: Decimal = Field(ge=Decimal("0"))
    close: Decimal = Field(ge=Decimal("0"))
    volume: Decimal = Field(ge=Decimal("0"))


class MarketSnapshot(BaseModel):
    """Objeto canónico e inmutable de datos de mercado.

    Representa el contrato de datos común entre fases (Common Domain Model),
    alineado con los estándares de FINOS para datos de instrumentos financieros.
    Todos los atributos se validan en el momento de la construcción.
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    exchange: str
    price: Decimal = Field(ge=Decimal("0"))
    volume: int = Field(ge=0)
    exchange_timestamp: datetime
    data_lineage: DataLineage
    ohlcv: tuple[OHLCVBar, ...] = Field(default_factory=tuple)
    daily_change_pct: float = Field(default=0.0)
    avg_volume: int = Field(default=0, ge=0)
    high_priority: bool = Field(default=False)
    universe_type: UniverseType = Field(default=UniverseType.SHARED)

    @field_validator("ticker")
    @classmethod
    def ticker_must_be_uppercase_and_non_empty(cls, value: str) -> str:
        """Valida que el ticker no esté vacío y lo convierte a mayúsculas."""
        if not value or not value.strip():
            raise ValueError("El ticker no puede estar vacío o contener solo espacios en blanco.")
        return value.upper().strip()

    @field_validator("exchange_timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware_utc(cls, value: datetime) -> datetime:
        """Valida que la marca de tiempo tenga información de zona horaria y sea UTC."""
        if value.tzinfo is None:
            raise ValueError(
                f"El exchange_timestamp debe ser consciente de la zona horaria. "
                f"Se recibió un datetime ingenuo (naive): {value}"
            )
        # Convertir a la zona UTC para asegurar consistencia
        return value.astimezone(UTC)
