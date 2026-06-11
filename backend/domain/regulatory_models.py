"""
Domain contracts for regulatory kill-switch scanning.
Immutable Pydantic V2 models used by the regulatory scanner engine.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

REGULATORY_SCANNER_SEVERITY_EXISTENTIAL: Final[str] = "EXISTENTIAL"
REGULATORY_SCANNER_SEVERITY_HIGH: Final[str] = "HIGH"
REGULATORY_SCANNER_SEVERITY_LOW: Final[str] = "LOW"
REGULATORY_SCANNER_SEVERITY_NONE: Final[str] = "NONE"

REGULATORY_SCANNER_ACTION_LIQUIDATE: Final[str] = "LIQUIDATE"
REGULATORY_SCANNER_ACTION_REDUCE_EXPOSURE: Final[str] = "REDUCE_EXPOSURE"
REGULATORY_SCANNER_ACTION_CLEAR: Final[str] = "CLEAR"


class RegulatorySeverityLevel(str, Enum):
    """Severity levels emitted by the regulatory scanner."""

    EXISTENTIAL = REGULATORY_SCANNER_SEVERITY_EXISTENTIAL
    HIGH = REGULATORY_SCANNER_SEVERITY_HIGH
    LOW = REGULATORY_SCANNER_SEVERITY_LOW
    NONE = REGULATORY_SCANNER_SEVERITY_NONE


class RegulatoryActionDirective(str, Enum):
    """Action directives consumed by orchestration logic."""

    LIQUIDATE = REGULATORY_SCANNER_ACTION_LIQUIDATE
    REDUCE_EXPOSURE = REGULATORY_SCANNER_ACTION_REDUCE_EXPOSURE
    CLEAR = REGULATORY_SCANNER_ACTION_CLEAR


class RegulatoryVetoResult(BaseModel):
    """Immutable result envelope for regulatory risk scanning."""

    model_config = ConfigDict(frozen=True)

    absolute_veto: bool = Field(
        default=False,
        description="If True, position must be liquidated immediately.",
    )
    severity_level: RegulatorySeverityLevel = Field(
        default=RegulatorySeverityLevel.NONE,
        description="Regulatory severity level.",
    )
    action_directive: RegulatoryActionDirective = Field(
        default=RegulatoryActionDirective.CLEAR,
        description="Action directive for orchestration.",
    )
    matched_keywords: list[str] = Field(
        default_factory=list,
        description="Matched regulatory terms for audit traceability.",
    )
    source: str = Field(
        default="UNKNOWN",
        description="Document source identifier.",
    )
    scan_timestamp: float = Field(
        default=0.0,
        description="UTC epoch time in seconds when scan completed.",
    )
    parse_error: bool = Field(
        default=False,
        description="True when input text was invalid or empty.",
    )

    @model_validator(mode="after")
    def _validate_consistency(self: RegulatoryVetoResult) -> RegulatoryVetoResult:
        """Guarantee internal consistency for absolute veto outcomes."""

        if self.absolute_veto:
            if self.action_directive != RegulatoryActionDirective.LIQUIDATE:
                raise ValueError("absolute_veto=True requires action_directive=LIQUIDATE")
            if self.severity_level != RegulatorySeverityLevel.EXISTENTIAL:
                raise ValueError("absolute_veto=True requires severity_level=EXISTENTIAL")
        return self


__all__ = [
    "RegulatoryActionDirective",
    "RegulatorySeverityLevel",
    "RegulatoryVetoResult",
]


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo: regulatory_models.py
# Sub-capa: Modelo (Domain)
# Eliminado: dependencia a constants del sistema anterior
# Preservado: enums/directivas y validator de consistencia sin cambios de contrato
# Pendientes: ninguno
# ─────────────────────────────────────────────────
