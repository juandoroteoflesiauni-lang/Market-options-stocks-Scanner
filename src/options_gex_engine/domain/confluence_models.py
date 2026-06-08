"""Contratos de Confluencia de Microestructura — Sector Técnico.

Define las acciones, niveles de convicción y modelos de orquestación para el
motor de confluencia triple (VSA x SMC x GEX).
"""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class ConfluenceAction(str, Enum):
    """Acciones del sistema basadas en el análisis de confluencia."""
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    SQUEEZE_BUY = "SQUEEZE_BUY"


class ConfluenceConviction(str, Enum):
    """Nivel de convicción / fiabilidad del setup analizado."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SignalDirection(str, Enum):
    """Dirección cualitativa del sesgo del mercado."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class SpotVsZGL(str, Enum):
    """Relación de precio entre el Spot actual y el Zero Gamma Level."""
    ABOVE = "ABOVE"
    BELOW = "BELOW"
    AT_ZGL = "AT_ZGL"


class VSAVannaSignal(str, Enum):
    """Tipo de soporte de la presión de Vanna al análisis de volumen (VSA)."""
    SUPPORTIVE = "SUPPORTIVE"
    CONFLICTING = "CONFLICTING"
    NEUTRAL = "NEUTRAL"


class WyckoffFase(str, Enum):
    """Fases del ciclo de mercado bajo la metodología de Wyckoff."""
    ACCUMULATION = "ACCUMULATION"
    MARKUP = "MARKUP"
    DISTRIBUTION = "DISTRIBUTION"
    MARKDOWN = "MARKDOWN"


# ─────────────────────────────────────────────────────────────────────────────
# §2  CONFIGURATION & THRESHOLDS (Canonical)
# ─────────────────────────────────────────────────────────────────────────────

CONFLUENCE_BUY_THRESHOLD:  float = 0.25
CONFLUENCE_SELL_THRESHOLD: float = -0.25
CONFLUENCE_SQUEEZE_OVERRIDE: float = 0.85

CONFLUENCE_CONVICTION_HIGH:   float = 0.70
CONFLUENCE_CONVICTION_MEDIUM: float = 0.40

# Pesos del Score Total (Tabla 31-32)
CONFLUENCE_WEIGHT_GEX:    float = 0.35
CONFLUENCE_WEIGHT_SMC:    float = 0.25
CONFLUENCE_WEIGHT_IV:     float = 0.20
CONFLUENCE_WEIGHT_STRAT:  float = 0.10
CONFLUENCE_WEIGHT_WY_VSA: float = 0.10

GEX_STOP_ZGL_TOLERANCE: float = 0.005  # 0.5%


# ─────────────────────────────────────────────────────────────────────────────
# §3  DOMAIN MODELS (Pydantic V2)
# ─────────────────────────────────────────────────────────────────────────────

class SMCGEXZone(BaseModel):
    """Zona de confluencia entre estructuras SMC (OB/FVG) y niveles GEX."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    level:        float
    zone_type:    str
    distance_pct: float
    conviction:   ConfluenceConviction
    description:  str


class VSAVannaGEXResult(BaseModel):
    """Resultado de la matriz de confluencia triple (Tabla 20)."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    action:         ConfluenceAction
    conviction:     ConfluenceConviction
    vsa_label:      str
    vanna_pressure: VSAVannaSignal
    gex_regime:     str
    explanation:    str


class WyckoffGEXDecision(BaseModel):
    """Decisión de timing basada en Wyckoff y régimen GEX (Tabla 23)."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    action:          ConfluenceAction
    wyckoff_fase:    WyckoffFase
    gex_regime:      str
    spot_vs_zgl:     SpotVsZGL
    squeeze_risk:    float
    stop_anchor:    float | None = None
    stop_logic:     str = ""
    is_golden_setup: bool = False


class MicrostructureConfluenceResult(BaseModel):
    """Resultado consolidado de todos los especialistas de microestructura."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker:           str
    timestamp:        str
    score:           float = 0.0
    signal:          ConfluenceAction = ConfluenceAction.WAIT
    confidence:      float = 0.0
    conviction:      ConfluenceConviction = ConfluenceConviction.LOW

    gex_sub_score:   float = 0.0
    smc_sub_score:   float = 0.0
    iv_sub_score:    float = 0.0
    strat_sub_score: float = 0.0
    wyckoff_vsa_sub: float = 0.0

    confluence_zones: list[SMCGEXZone] = Field(default_factory=list)
    squeeze_override: bool = False
    vsa_vanna_gex:   VSAVannaGEXResult | None = None
    wyckoff_gex:     WyckoffGEXDecision | None = None

    ok:              bool = True
    error:           str | None = None


class GEXLevels(BaseModel):
    """Niveles mecánicos de GEX de la cadena de opciones."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    put_wall: float
    zero_gamma_level: float
    volatility_magnet: float | None = None
    max_pain: float


class OptionsSMCConfluenceResult(BaseModel):
    """Resultado del mapeo de confluencia entre los motores técnico (SMC) y de derivados."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    is_ob_validated: bool = False
    is_sweep_confirmed: bool = False
    is_magnet_active: bool = False
    confluence_score: float = 0.0
    summary: str = "NO CONFLUENCE"

