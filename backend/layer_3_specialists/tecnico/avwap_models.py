"""Contratos de Dominio para el Motor AVWAP (Anchored VWAP) — Sector Técnico.

Define las clasificaciones de anclaje de Shannon, estados de equilibrio de Ortiz
y las estructuras de datos para el análisis estadístico de bandas y stacks multi-temporal.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict

# ─────────────────────────────────────────────────────────────────────────────
# §1  ENUMERATIONS
# ─────────────────────────────────────────────────────────────────────────────


class AVWAPAnchorType(str, Enum):
    """Metodología de anclaje Brian Shannon (Cinco tipos canónicos)."""

    PERIOD_OPEN = "PERIOD_OPEN"  # Apertura de periodo (Año/Mes/Sesión)
    SWING_LOW = "SWING_LOW"  # Swing low significativo (Soporte dinámico)
    SWING_HIGH = "SWING_HIGH"  # Swing high significativo (Resistencia dinámica)
    MOMENTUM_HANDOFF = "MOMENTUM_HANDOFF"  # Punto de cambio de momentum (BOS)
    FUNDAMENTAL_EVENT = "FUNDAMENTAL_EVENT"  # Evento de alto impacto (Earnings/FED)


class VWAPState(str, Enum):
    """Clasificación de equilibrio de mercado basada en desviación estándar (σ)."""

    EQUILIBRIO = "EQUILIBRIO"  # Precio dentro de ±1σ
    DESEQUILIBRIO_ALCISTA = "DESEQUILIBRIO_ALCISTA"  # Precio sobre +1σ con tendencia
    DESEQUILIBRIO_BAJISTA = "DESEQUILIBRIO_BAJISTA"  # Precio bajo -1σ con tendencia
    DESEQUILIBRIO_AGOTADO = "DESEQUILIBRIO_AGOTADO"  # Precio en ±2σ/±3σ (Agotamiento)


class VWAPCrossDirection(str, Enum):
    """Dirección del cruce de VWAP."""

    CROSS_UP = "CROSS_UP"
    CROSS_DOWN = "CROSS_DOWN"
    NONE = "NONE"


class VWAPStackConviction(str, Enum):
    """Nivel de convicción por alineación multi-temporal (Stack Rule)."""

    MAX = "MAX"  # Diario + Semanal + Mensual alineados
    HIGH = "HIGH"  # Dos de tres alineados
    NONE = "NONE"  # Sin alineación


# ─────────────────────────────────────────────────────────────────────────────
# §2  DOMAIN MODELS (Pydantic V2)
# ─────────────────────────────────────────────────────────────────────────────


class AVWAPBands(BaseModel):
    """Niveles estadísticos de bandas AVWAP (σ² volumétrica)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    avwap: float  # Valor central de AVWAP
    upper_1: float  # AVWAP + 1σ
    lower_1: float  # AVWAP - 1σ
    upper_2: float  # AVWAP + 2σ
    lower_2: float  # AVWAP - 2σ
    upper_3: float  # AVWAP + 3σ (Zona de ruptura genuina)
    lower_3: float  # AVWAP - 3σ
    std_dev: float  # Escalar σ en la barra actual


class VWAPCrossEvent(BaseModel):
    """Registro de evento de cruce y validación de aceptación (Regla R-AV-04)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    direction: VWAPCrossDirection = VWAPCrossDirection.NONE
    bar_index: int = 0
    bars_held: int = 0
    accepted: bool = False
    n_confirm: int = 3


class AVWAPResult(BaseModel):
    """Resultado consolidado del motor AVWAP."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    anchor_idx: int
    anchor_type: AVWAPAnchorType = AVWAPAnchorType.PERIOD_OPEN
    bars_since_anchor: int = 0

    avwap_last: float = 0.0
    bands: AVWAPBands | None = None

    vwap_state: VWAPState = VWAPState.EQUILIBRIO
    vwap_mature: bool = False  # Regla R-AV-01: Madurez estadística
    slope_positive: bool = True  # Pendiente de los últimos 5 periodos

    poc_distance: float = 0.0  # |spot - avwap| / spot
    size_factor: float = 1.0  # Multiplicador de posición (Regla R-AV-06)

    last_cross: VWAPCrossEvent | None = None
    ok: bool = True
    error: str | None = None


class VWAPStackResult(BaseModel):
    """Resultado de alineación multi-temporal (Regla VWAP Stack)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    daily_above_vwap: bool = False
    weekly_above_vwap: bool = False
    monthly_above_vwap: bool = False

    all_aligned: bool = False
    direction: str = "CASH"  # "LONG" | "CASH"
    conviction: VWAPStackConviction = VWAPStackConviction.NONE

    daily_vwap: float = 0.0
    weekly_vwap: float = 0.0
    monthly_vwap: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# §3  MATURITY & SIZING CONFIGURATION (Canonical)
# ─────────────────────────────────────────────────────────────────────────────

# Umbrales de madurez (Regla R-AV-01)
AVWAP_MATURITY_DAILY_BARS: Final[int] = 5  # 1 semana operativa
AVWAP_MATURITY_WEEKLY_BARS: Final[int] = 2
AVWAP_MATURITY_MONTHLY_BARS: Final[int] = 10  # 2 semanas operativas
AVWAP_MATURITY_ANNUAL_BARS: Final[int] = 42  # Requerido: 2 meses (Ene + Feb)

# Factores de Sizing (Tabla 5)
AVWAP_SIZING_NEAR_PCT: Final[float] = 0.01  # < 1% -> 100% size
AVWAP_SIZING_MID_PCT: Final[float] = 0.05  # 1%-5% -> 75% size

AVWAP_SIZING_NEAR_FACTOR: Final[float] = 1.00
AVWAP_SIZING_MID_FACTOR: Final[float] = 0.75
AVWAP_SIZING_FAR_FACTOR: Final[float] = 0.50

# Parámetros operativos
AVWAP_SLOPE_WINDOW: Final[int] = 5
AVWAP_ACCEPTANCE_BARS: Final[int] = 3

# Interpretación de Bandas
AVWAP_BAND_EQUILIBRIUM_SIGMA: Final[float] = 1.0
AVWAP_BAND_EXTREME_SIGMA: Final[float] = 2.0


# ─────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: TÉCNICO
# Archivo      : avwap_models.py
# Sub-capa     : Domain (Contratos de Datos)
# Eliminado    : Referencias QuantumBeta.
# Preservado   : Enums de anclaje (Shannon), Estados de Equilibrio (Ortiz).
# Actualizado  : Pydantic V2 ConfigDict(extra="ignore").
# ─────────────────────────────────────────────────────────
