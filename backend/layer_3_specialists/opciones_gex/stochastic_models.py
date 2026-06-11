"""Contratos de Dominio Estocásticos — Sector Opciones/GEX.

Define los modelos de datos para los resultados de proyecciones probabilísticas
y los datos del gráfico de abanico (Fan Chart).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FanChart(BaseModel):
    """Estructura de percentiles para visualización de proyecciones."""

    model_config = ConfigDict(frozen=True)

    p10: list[float] = Field(..., description="Percentil 10 (Soporte extremo)")
    p25: list[float] = Field(..., description="Percentil 25 (Soporte moderado)")
    p50: list[float] = Field(..., description="Percentil 50 (Mediana / EV)")
    p75: list[float] = Field(..., description="Percentil 75 (Resistencia moderada)")
    p90: list[float] = Field(..., description="Percentil 90 (Resistencia extrema)")
    timestamps: list[str] = Field(..., description="Fechas proyectadas en formato ISO")


class StochasticPredictiveResult(BaseModel):
    """Resultado final del motor predictivo estocástico."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    jump_intensity: float = 0.0
    vol_of_vol: float = 0.0
    fan_chart: FanChart | None = None
    drift_bias: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
    expected_value_horizon: float = 0.0

    ok: bool = True
    error: str | None = None


# ─────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: OPCIONES
# Archivo      : stochastic_models.py
# Sub-capa     : Domain (Models)
# Eliminado    : Dependencias externas legacy.
# Preservado   : Definición de FanChart y Drifts institucionales.
# ─────────────────────────────────────────────────────────────
