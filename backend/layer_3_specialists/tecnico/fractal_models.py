"""Modelos de Dominio para el Motor Fractal — Sector Técnico.

Define las estructuras de datos para señales de confluencia Fractal FVG
y mediciones de Entropía de Shannon para el filtrado de regímenes de mercado.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FractalSignal(BaseModel):
    """Señal de confluencia Fractal FVG."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    timestamp: datetime
    bias: str  # LONG | SHORT | CASH (bidireccional)
    fvg_size: float
    entropy_score: float
    is_fvg_active: bool


class EntropyScore(BaseModel):
    """Medición de complejidad informacional (Entropía)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    timestamp: datetime
    value: float
    z_score: float
    is_ordered: bool
