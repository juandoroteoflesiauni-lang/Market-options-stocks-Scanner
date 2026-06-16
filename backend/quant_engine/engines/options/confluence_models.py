"""Modelos de Confluencia de Microestructura — Sector Opciones/GEX.

Define los contratos de datos para la integración de señales multi-especialista,
incluyendo el Master Confluence Scorer y el Triple Filtro de Earnings.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

# Shared enums — single source of truth in backend/domain/confluence_action.py.
# Re-exported here for backward compatibility with opciones_gex consumers.
from backend.domain.confluence_action import (
    ConfluenceAction,
    ConfluenceConviction,
    SpotVsZGL,
    VSAVannaSignal,
    WyckoffFase,
)

# ══════════════════════════════════════════════════════════════════════════════
# §1  ENUMERATIONS (sector-specific)
# ══════════════════════════════════════════════════════════════════════════════


class EarningsStructure(str, Enum):
    """Estructura de opciones sugerida para eventos de ganancias."""

    LONG_CALENDAR = "LONG_CALENDAR"  # Preferido (Kelly conservador)
    SHORT_STRADDLE = "SHORT_STRADDLE"  # Agresivo (Kelly dinámico)
    VOID = "VOID"  # Filtro no superado


# ══════════════════════════════════════════════════════════════════════════════
# §2  MODELOS DE CAPA DE CONFLUENCIA
# ══════════════════════════════════════════════════════════════════════════════


class SMCGEXZone(BaseModel):
    """Zona de confluencia entre niveles SMC (OB/FVG) y GEX (Walls/ZGL)."""

    model_config = ConfigDict(frozen=True)

    level: float
    zone_type: str  # Ej: "OB_BULL_AT_PUT_WALL"
    distance_pct: float
    conviction: ConfluenceConviction
    description: str = ""


class VSAVannaGEXResult(BaseModel):
    """Resultado del cruce VSA x Vanna x GEX."""

    model_config = ConfigDict(frozen=True)

    action: ConfluenceAction
    conviction: ConfluenceConviction
    vsa_label: str
    vanna_pressure: VSAVannaSignal
    gex_regime: str
    explanation: str = ""


class WyckoffGEXDecision(BaseModel):
    """Resultado del cruce Wyckoff x Régimen GEX."""

    model_config = ConfigDict(frozen=True)

    action: ConfluenceAction
    wyckoff_fase: WyckoffFase
    gex_regime: str
    spot_vs_zgl: SpotVsZGL
    squeeze_risk: float
    stop_anchor: float | None = None
    stop_logic: str = ""
    is_golden_setup: bool = False


class MicrostructureConfluenceResult(BaseModel):
    """
    Contrato Maestro de Salida del Scorer de Confluencia Total (MIC).
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    timestamp: str

    score: float = Field(default=0.0, ge=-1.0, le=1.0)
    signal: ConfluenceAction = ConfluenceAction.WAIT
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    conviction: ConfluenceConviction = ConfluenceConviction.LOW

    # Desglose de sub-scores (normalizados [-1, +1])
    gex_sub_score: float = 0.0
    smc_sub_score: float = 0.0
    iv_sub_score: float = 0.0
    strat_sub_score: float = 0.0
    wyckoff_vsa_sub: float = 0.0

    confluence_zones: list[SMCGEXZone] = Field(default_factory=list)
    squeeze_override: bool = False  # True → Fuerza CASH

    vsa_vanna_gex: VSAVannaGEXResult | None = None
    wyckoff_gex: WyckoffGEXDecision | None = None

    ok: bool = True
    error: str | None = None


class GEXLevels(BaseModel):
    """Niveles clave de exposición gamma (GEX)."""

    model_config = ConfigDict(frozen=True)

    call_wall: float = Field(..., description="Strike con máximo GEX positivo")
    put_wall: float = Field(..., description="Strike con máximo GEX negativo")
    zero_gamma_level: float = Field(
        ..., description="Nivel de interpolación de cambio de signo GEX"
    )
    max_pain: float = Field(..., description="Strike que minimiza la pérdida agregada de OI")
    volatility_magnet: float | None = None  # Alias para Max Pain en contextos de atracción


class OptionsSMCConfluenceResult(BaseModel):
    """Resultado de la validación mecánica de POIs estructurales."""

    model_config = ConfigDict(frozen=True)

    is_ob_validated: bool = False
    is_sweep_confirmed: bool = False
    is_magnet_active: bool = False
    confluence_score: float = 0.0  # Bonus de 0.0 a 1.0 para el MIC Score
    summary: str = "NO CONFLUENCE"


class EarningsSetup(BaseModel):
    """Resultado del Triple Filtro de Earnings e integracion Macro."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    passes: bool
    f1_backwardation: bool
    f2_volume_ok: bool
    f3_vrp_rich: bool

    double_event_risk: bool = False
    recommended_structure: EarningsStructure = EarningsStructure.VOID
    kelly_fraction: float = 0.0

    ts_regime: str = "UNKNOWN"
    vol_30d_avg: float = 0.0
    vrp_log: float = 0.0
    notes: list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# §3  CONSTANTES INSTITUCIONALES
# ══════════════════════════════════════════════════════════════════════════════

# Pesos del Scorer (Expediente Técnico §3)
CONFLUENCE_WEIGHT_GEX: Final[float] = 0.35
CONFLUENCE_WEIGHT_SMC: Final[float] = 0.25
CONFLUENCE_WEIGHT_IV: Final[float] = 0.20
CONFLUENCE_WEIGHT_STRAT: Final[float] = 0.10
CONFLUENCE_WEIGHT_WY_VSA: Final[float] = 0.10

# Umbrales direccionales
CONFLUENCE_BUY_THRESHOLD: Final[float] = 0.25
CONFLUENCE_SELL_THRESHOLD: Final[float] = -0.25
CONFLUENCE_SQUEEZE_OVERRIDE: Final[float] = 0.85

# Umbrales de convicción
CONFLUENCE_CONVICTION_HIGH: Final[float] = 0.65
CONFLUENCE_CONVICTION_MEDIUM: Final[float] = 0.40

# Triple Filtro Earnings
EARNINGS_VRP_LOG_THRESHOLD: Final[float] = math.log(1.15)
EARNINGS_MIN_OPTION_VOLUME: Final[float] = 2500.0
EARNINGS_DOUBLE_EVENT_WINDOW_DAYS: Final[int] = 5


# ─────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: OPCIONES
# Archivo      : confluence_models.py
# Sub-capa     : Contracts (Integration)
# Eliminado    : Branding QuantumBeta V1.
# Preservado   : Ponderaciones del MIC, Regla Long-Only, Triple Filtro.
# Dependencias : Requiere Pydantic V2.
# ─────────────────────────────────────────────────────────────
