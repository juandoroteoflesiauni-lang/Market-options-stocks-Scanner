from __future__ import annotations
from typing import Literal, Any
"""
Domain contracts for structured financial event extraction.
"""


import re as _re
from enum import Enum as _Enum

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ConfigDict as _ConfigDict
from pydantic import field_validator as _field_validator


class EarningsReportData(BaseModel):
    """Structured data from an earnings announcement."""

    model_config = ConfigDict(frozen=True)

    eps_estimado: float | None = Field(default=None, description="EPS expected by analysts")
    eps_reportado: float | None = Field(default=None, description="Actual EPS reported")
    revenue_estimado: float | None = Field(default=None, description="Revenue expected")
    revenue_reportado: float | None = Field(default=None, description="Actual revenue reported")
    guidance: Literal["positivo", "negativo", "neutral"] | None = Field(
        default=None,
        description="Company forward-looking guidance tone",
    )


class MacroEventData(BaseModel):
    """Structured data from a macro-economic event (central banks, etc)."""

    model_config = ConfigDict(frozen=True)

    tasa_previa: float | None = Field(default=None, description="Previous interest rate")
    tasa_nueva: float | None = Field(default=None, description="Newly announced interest rate")
    tono_fed: Literal["hawkish", "dovish", "neutral"] | None = Field(
        default=None,
        description="Sentiment/Tone of the central bank statement",
    )


class StructuredEventResult(BaseModel):
    """Container for the extracted event and its metadata."""

    model_config = ConfigDict(frozen=True)

    event_type: Literal["earnings", "macro", "other"]
    data: EarningsReportData | MacroEventData | dict[str, Any]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_summary: str = Field(default="", description="Concise summary from the LLM")
    error: str | None = None


class GuidanceDirection(str, _Enum):
    """Dirección del guidance corporativo."""

    POSITIVO = "positivo"
    NEGATIVO = "negativo"
    NEUTRAL = "neutral"


class FedTone(str, _Enum):
    """Tono de política monetaria de la Reserva Federal."""

    HAWKISH = "hawkish"
    DOVISH = "dovish"
    NEUTRAL = "neutral"


class FinancialEventType(str, _Enum):
    """Tipos de eventos financieros soportados por el motor extendido."""

    EARNINGS_REPORT = "earnings_report"
    MACRO_EVENT = "macro_event"


def _coerce_to_float(v: float | str | int | None) -> float | None:
    """
    Intenta convertir un valor a float de forma segura.

    Maneja casos de alucinación del LLM:
        - "1.5 billones" → extrae 1.5
        - "$2.3M"        → extrae 2.3
        - "N/A"          → None
        - ""             → None
    """

    if v is None:
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        v_stripped = v.strip()
        if not v_stripped or v_stripped.lower() in ("n/a", "null", "none", "-", "n.d."):
            return None
        match = _re.search(r"[-+]?\d+(?:\.\d+)?", v_stripped)
        if match:
            return float(match.group())
        return None
    return None


def _coerce_enum(v: str | _Enum | None, enum_class: type[_Enum]) -> _Enum | None:
    """Coerce seguro a un Enum. Devuelve None si el valor es inválido."""

    if v is None:
        return None
    if isinstance(v, enum_class):
        return v
    if isinstance(v, str):
        v_lower = v.strip().lower()
        for member in enum_class:
            if member.value == v_lower:
                return member
        return None
    return None


class EarningsReportDataExtended(BaseModel):
    """
    Extensión de EarningsReportData con propiedades derivadas y validators
    anti-alucinación.

    Diferencia respecto a EarningsReportData:
      - eps_surprise_pct: sorpresa EPS % calculada (property).
      - revenue_surprise_pct: sorpresa Revenue % calculada (property).
      - guidance usa GuidanceDirection (enum tipado vs Literal).
      - Validators _coerce_float_fields y _coerce_guidance protegen
        ante strings con unidades ("1.5B", "$2.3M") o valores inválidos.
    """

    model_config = _ConfigDict(frozen=True)

    eps_estimado: float | None = Field(
        default=None,
        description="EPS estimado por el consenso de analistas (USD por acción).",
    )
    eps_reportado: float | None = Field(
        default=None,
        description="EPS efectivamente reportado por la compañía (USD por acción).",
    )
    revenue_estimado: float | None = Field(
        default=None,
        description="Revenue estimado por consenso (en la unidad reportada).",
    )
    revenue_reportado: float | None = Field(
        default=None,
        description="Revenue efectivamente reportado (en la unidad reportada).",
    )
    guidance: GuidanceDirection | None = Field(
        default=None,
        description="Dirección del forward guidance: positivo, negativo o neutral.",
    )

    @property
    def eps_surprise_pct(self: EarningsReportDataExtended) -> float | None:
        """Porcentaje de sorpresa EPS = (reportado - estimado) / |estimado| * 100."""

        if self.eps_reportado is None or self.eps_estimado is None:
            return None
        if self.eps_estimado == 0.0:
            return None
        return ((self.eps_reportado - self.eps_estimado) / abs(self.eps_estimado)) * 100.0

    @property
    def revenue_surprise_pct(self: EarningsReportDataExtended) -> float | None:
        """Porcentaje de sorpresa Revenue."""

        if self.revenue_reportado is None or self.revenue_estimado is None:
            return None
        if self.revenue_estimado == 0.0:
            return None
        return (
            (self.revenue_reportado - self.revenue_estimado) / abs(self.revenue_estimado)
        ) * 100.0

    @_field_validator(
        "eps_estimado",
        "eps_reportado",
        "revenue_estimado",
        "revenue_reportado",
        mode="before",
    )
    @classmethod
    def _coerce_float_fields(
        cls: type[EarningsReportDataExtended],
        v: float | str | int | None,
    ) -> float | None:
        return _coerce_to_float(v)

    @_field_validator("guidance", mode="before")
    @classmethod
    def _coerce_guidance(
        cls: type[EarningsReportDataExtended],
        v: str | GuidanceDirection | None,
    ) -> GuidanceDirection | None:
        return _coerce_enum(v, GuidanceDirection)


class MacroEventDataExtended(BaseModel):
    """
    Extensión de MacroEventData con propiedad derivada delta_bps y
    validators anti-alucinación.

    Diferencia respecto a MacroEventData:
      - delta_bps: cambio en puntos base (tasa_nueva - tasa_previa) * 100.
      - tono_fed usa FedTone (enum tipado vs Literal).
      - Validators _coerce_rate_fields y _coerce_tone protegen
        ante strings con "%" o valores inválidos del LLM.
    """

    model_config = _ConfigDict(frozen=True)

    tasa_previa: float | None = Field(
        default=None,
        description="Tasa de interés de referencia ANTES de la decisión (en %).",
    )
    tasa_nueva: float | None = Field(
        default=None,
        description="Tasa de interés de referencia DESPUÉS de la decisión (en %).",
    )
    tono_fed: FedTone | None = Field(
        default=None,
        description="Tono predominante del comunicado: hawkish, dovish o neutral.",
    )

    @property
    def delta_bps(self: MacroEventDataExtended) -> float | None:
        """Cambio en puntos base = (tasa_nueva - tasa_previa) * 100."""

        if self.tasa_nueva is None or self.tasa_previa is None:
            return None
        return round((self.tasa_nueva - self.tasa_previa) * 100.0, 1)

    @_field_validator("tasa_previa", "tasa_nueva", mode="before")
    @classmethod
    def _coerce_rate_fields(
        cls: type[MacroEventDataExtended],
        v: float | str | int | None,
    ) -> float | None:
        return _coerce_to_float(v)

    @_field_validator("tono_fed", mode="before")
    @classmethod
    def _coerce_tone(
        cls: type[MacroEventDataExtended],
        v: str | FedTone | None,
    ) -> FedTone | None:
        return _coerce_enum(v, FedTone)


class BuilderPayload(BaseModel):
    """Payload inmutable listo para que el Orquestador lo envíe al LLM."""

    model_config = _ConfigDict(frozen=True)

    system_prompt: str
    user_message: str
    response_format: dict[str, str] = Field(
        default_factory=lambda: {"type": "json_object"},
    )


class TypedParseResult(BaseModel):
    """
    Resultado del parsing tipado — siempre se devuelve, nunca se lanza excepción.

    Si success=True → data contiene el StructuredEventResult validado.
    Si success=False → errors contiene la causa del fallo.
    """

    model_config = _ConfigDict(frozen=True)

    success: bool
    data: StructuredEventResult | None = None
    errors: list[str] = Field(default_factory=list)
    raw_input: str = Field(default="", repr=False)


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: event_models.py
# Eliminado: bloques de procedencia del sistema anterior
# Preservado: contratos de eventos, enums, helpers y validators tipados
# Pendientes: ninguno
# ─────────────────────────────────────────────────
