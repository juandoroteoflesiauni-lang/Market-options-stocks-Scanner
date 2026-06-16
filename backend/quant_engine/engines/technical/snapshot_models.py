from __future__ import annotations
"""Modelos de dominio para Snapshots de Mercado (Sector Técnico)."""


from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SnapshotBar(BaseModel):
    """Estructura de barra OHLCV con soporte para snapshots de mercado."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str = Field(..., description="Ticker del activo")
    timestamp: datetime = Field(..., description="Fecha y hora de la barra")
    open: float = Field(..., description="Precio de apertura")
    high: float = Field(..., description="Precio máximo")
    low: float = Field(..., description="Precio mínimo")
    close: float = Field(..., description="Precio de cierre")
    volume: float = Field(..., description="Volumen operado")

    # Metadatos del snapshot
    snapshot_id: str | None = Field(None, description="ID único del snapshot del sistema de datos")
    is_restored: bool = Field(
        False, description="Indica si esta barra proviene de una restauración de estado"
    )


class MarketState(BaseModel):
    """Representa el estado capturado de un flujo de datos en un punto temporal."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str
    last_timestamp: datetime
    iterations_processed: int
    rng_state: bytes | None = None
    applied_signals: list[str] = Field(default_factory=list)
