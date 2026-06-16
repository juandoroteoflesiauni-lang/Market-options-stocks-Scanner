from __future__ import annotations
from typing import Any
"""
backend/domain/argentina_models.py
════════════════════════════════════════════════════════════════════════════════
Domain contracts for Argentine macro-economic data (Sector: DATA).
════════════════════════════════════════════════════════════════════════════════
"""


import datetime as _dt
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Argentine Market Constants ───────────────────────────────────────────────

ARGENTINA_DOLAR_TIPOS: Final[frozenset[str]] = frozenset(
    {
        "oficial",
        "blue",
        "bolsa",
        "contadoconliqui",
        "cripto",
        "mayorista",
        "solidario",
        "tarjeta",
    }
)


class BCRAExchangeRate(BaseModel):
    """
    Official exchange rate from the Banco Central de la República Argentina (BCRA).
    Logical PK : (fecha, codigo_moneda)
    Source     : BCRA public API.
    """

    model_config = ConfigDict(frozen=True)

    fecha: _dt.date = Field(..., description="Fecha de la cotización")
    codigo_moneda: str = Field(..., description="Código ISO/BCRA de la moneda (ej: 'USD', '002')")
    descripcion: str | None = Field(default=None, description="Nombre descriptivo de la moneda")
    tipo_pase: str | None = Field(default=None, description="Tipo de pase (campo BCRA)")
    tipo_cotizacion: str | None = Field(default=None, description="Tipo de cotización (campo BCRA)")
    compra: float | None = Field(default=None, description="Precio de compra en ARS")
    venta: float | None = Field(default=None, description="Precio de venta en ARS")
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(_dt.UTC),
        description="UTC timestamp de la última consulta",
    )

    @field_validator("fecha", mode="before")
    @classmethod
    def coerce_fecha(cls, v: Any) -> _dt.date:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, _dt.date):
            return v
        if isinstance(v, str):
            return _dt.date.fromisoformat(v.split("T")[0])
        raise ValueError(f"Cannot parse BCRAExchangeRate.fecha: {v!r}")

    @field_validator("fetched_at", mode="before")
    @classmethod
    def enforce_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=_dt.UTC)
            return v.astimezone(_dt.UTC)
        raise ValueError(f"Cannot parse BCRAExchangeRate.fetched_at: {v!r}")


class ArgentinaDolarSnapshot(BaseModel):
    """
    Dollar exchange rate snapshot by market type (official, blue, MEP, CCL).
    Logical PK : (fecha, casa)
    Source     : ArgentinaDatos API.
    """

    model_config = ConfigDict(frozen=True)

    fecha: _dt.date = Field(..., description="Fecha de la cotización")
    casa: str = Field(
        ..., description="Tipo de mercado (blue, oficial, bolsa, contadoconliqui, etc.)"
    )
    compra: float | None = Field(default=None, description="Precio de compra en ARS")
    venta: float | None = Field(default=None, description="Precio de venta en ARS")

    @field_validator("fecha", mode="before")
    @classmethod
    def coerce_fecha(cls, v: Any) -> _dt.date:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, _dt.date):
            return v
        if isinstance(v, str):
            return _dt.date.fromisoformat(v.split("T")[0])
        raise ValueError(f"Cannot parse ArgentinaDolarSnapshot.fecha: {v!r}")

    @field_validator("casa", mode="before")
    @classmethod
    def normalise_casa(cls, v: str) -> str:
        return str(v).strip().lower()


class RiesgoPaisPoint(BaseModel):
    """
    Country Risk (EMBI+ Argentina) time series point.
    Logical PK : fecha
    Source     : ArgentinaDatos API.
    """

    model_config = ConfigDict(frozen=True)

    fecha: _dt.date = Field(..., description="Fecha de la observación")
    valor: float = Field(..., description="Riesgo País en puntos básicos (bps)")
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(_dt.UTC),
        description="UTC timestamp de la última consulta",
    )

    @field_validator("fecha", mode="before")
    @classmethod
    def coerce_fecha(cls, v: Any) -> _dt.date:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, _dt.date):
            return v
        if isinstance(v, str):
            return _dt.date.fromisoformat(v.split("T")[0])
        raise ValueError(f"Cannot parse RiesgoPaisPoint.fecha: {v!r}")

    @field_validator("fetched_at", mode="before")
    @classmethod
    def enforce_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=_dt.UTC)
            return v.astimezone(_dt.UTC)
        raise ValueError(f"Cannot parse RiesgoPaisPoint.fetched_at: {v!r}")


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : argentina_models.py
# Sub-capa         : Modelo (Domain)
# Enfoque          : Contratos para datos macroeconómicos argentinos.
# Eliminado        : Comentarios de QB V1, inconsistencias de Pydantic V1/V2.
# Preservado       : Validadores de fecha y normalización de 'casa'.
# ─────────────────────────────────────────────────────────────────────
