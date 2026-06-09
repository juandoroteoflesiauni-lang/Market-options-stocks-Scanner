"""Contratos de Dominio para el Análisis de Opciones — Sector Opciones/GEX.

Define las entidades y estructuras de datos para el análisis de Griegas,
exposiciones institucionales (GEX, VEX, CEX) y analítica probabilística.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ExposureRegime(str, Enum):
    """Regímenes de exposición derivados de flujos de dealers."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    SHOCK   = "SHOCK"


class GreekSurface(BaseModel):
    """Griegas de alta fidelidad a lo largo de toda la cadena de opciones."""
    model_config = ConfigDict(frozen=True)

    speed:  list[float] = Field(default_factory=list)
    zomma:  list[float] = Field(default_factory=list)
    color:  list[float] = Field(default_factory=list)
    ultima: list[float] = Field(default_factory=list)


class PDFAnalytics(BaseModel):
    """Analítica de densidad de probabilidad risk-neutral (Breeden-Litzenberger)."""
    model_config = ConfigDict(frozen=True)

    pop:               float = 0.0    # Probability of Profit
    expected_value:    float = 0.0    # EV en unidades de precio
    skewness:          float = 0.0
    excess_kurtosis:   float = 0.0
    left_tail_prob:    float = 0.0    # Riesgo de cola izquierda [0-1]
    right_tail_prob:   float = 0.0
    tail_regime:       str   = "SYMMETRIC"


class PositioningMetrics(BaseModel):
    """Métricas de posicionamiento institucional y concentración."""
    model_config = ConfigDict(frozen=True)

    daoi_net_delta:    float = 0.0    # Delta-Adjusted Open Interest
    hhi_concentration: float = 0.0    # Herfindahl-Hirschman Index [0-1]
    max_gex_strike:    float | None = None
    gamma_stability:   float = 1.0    # Score de estabilidad basado en Speed


class DealerExposures(BaseModel):
    """Métricas agregadas de exposición de Dealers (VEX, CEX, GEX)."""
    model_config = ConfigDict(frozen=True)

    total_vex: float = 0.0  # Vanna Exposure
    total_cex: float = 0.0  # Charm Exposure
    total_gex: float = 0.0  # Gamma Exposure (recalculada)
    vex_regime: ExposureRegime = ExposureRegime.NEUTRAL
    cex_regime: ExposureRegime = ExposureRegime.NEUTRAL


class OptionsResult(BaseModel):
    """Resultado consolidado del motor de análisis de opciones."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    surface: GreekSurface
    exposures: DealerExposures
    pdf_analytics: PDFAnalytics = Field(default_factory=PDFAnalytics)
    positioning:   PositioningMetrics = Field(default_factory=PositioningMetrics)
    vanna_volatility_sensitivity: float = 0.0
    charm_time_decay_acceleration: float = 0.0
    options_mic_score: float = 0.0
    ok: bool = True
    error: str | None = None


class OptionsSignal(BaseModel):
    """Payload de señal listo para orquestación MIC."""
    model_config = ConfigDict(frozen=True)

    vex_score: float = 0.0
    cex_score: float = 0.0
    skewness_premium: float = 0.0
    regime: ExposureRegime = ExposureRegime.NEUTRAL
