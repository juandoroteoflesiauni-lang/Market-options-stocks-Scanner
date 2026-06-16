from __future__ import annotations
from typing import Any
"""Modelos de respuesta para la API de Polygon (Sector Técnico)."""



from pydantic import BaseModel, ConfigDict, Field


class PolygonExchangeStatus(BaseModel):
    """Estado de las principales bolsas."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    nyse: str | None = None
    nasdaq: str | None = None
    otc: str | None = None


class PolygonCurrencyStatus(BaseModel):
    """Estado de los mercados de divisas y cripto."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    crypto: str | None = None
    fx: str | None = None


class PolygonMarketStatus(BaseModel):
    """Estado general del mercado según Polygon."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    market: str = Field(
        ..., description="Overall market status (e.g. open, closed, extended-hours)"
    )
    serverTime: str | None = None
    exchanges: PolygonExchangeStatus | None = None
    currencies: PolygonCurrencyStatus | None = None
    earlyHours: bool | None = None
    afterHours: bool | None = None


class PolygonSnapshotTicker(BaseModel):
    """Detalle de snapshot para un ticker específico."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    todaysChangePerc: float | None = None
    todaysChange: float | None = None
    updated: int | None = None
    day: dict[str, Any] | None = None
    min: dict[str, Any] | None = None
    prevDay: dict[str, Any] | None = None


class PolygonSnapshotResponse(BaseModel):
    """Respuesta completa de snapshot de ticker."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    status: str | None = None
    ticker: PolygonSnapshotTicker | None = None


class PolygonQuote(BaseModel):
    """Formato estandarizado de cotización interna."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str
    price: float
    change_pct: float | None = None
    volume: int | None = None
    timestamp: int | None = None


# ─────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: TÉCNICO
# Archivo      : polygon_models.py
# Sub-capa     : Modelo
# Eliminado    : Comentarios redundantes de implementación.
# Preservado   : Estructuras de respuesta Snapshot y MarketStatus.
# Pendientes   : Ninguno — migración limpia completada.
# ─────────────────────────────────────────────────────────
