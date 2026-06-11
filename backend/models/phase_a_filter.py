"""Modelo de resultado del filtro global de Phase A.

Cada ticker evaluado por Phase A produce un FilterResult con:
- acceptance: si pasa el gate y continúa a Phase B
- quality_score: score compuesto 0-100
- breakdown: scores individuales por cada filtro
- rejection_reason: causa de rechazo si corresponde
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FilterScore(BaseModel):
    """Score individual de un filtro."""

    model_config = ConfigDict(frozen=True)

    name: str
    score: float = Field(ge=0.0, le=100.0)
    weight: float = Field(ge=0.0, le=1.0)
    passed: bool
    reason: str = ""


class PhaseAFilterResult(BaseModel):
    """Resultado completo del filtro global de Phase A."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    accepted: bool
    quality_score: float = Field(ge=0.0, le=100.0)
    breakdown: tuple[FilterScore, ...]
    rejection_reason: str = ""
