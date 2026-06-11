"""Modelos de dominio para la Superficie de Volatilidad Implicada (IV Surface).

Implementa los contratos de datos para el pipeline de análisis de volatilidad,
incluyendo regímenes de IV Rank, VRP, Skew y estructura temporal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─────────────────────────────────────────────────────────────────────────────
# ENUMERATIONS
# ─────────────────────────────────────────────────────────────────────────────


class IVRegime(str, Enum):
    """Clasificación de régimen basada en IV Rank."""

    HIGH = "HIGH"  # > 0.60
    NORMAL = "NORMAL"  # 0.30 - 0.60
    LOW = "LOW"  # < 0.30


class VRPSignal(str, Enum):
    """Clasificación de Volatility Risk Premium."""

    RICH = "RICH"  # > +2%
    FAIR = "FAIR"  # [-2%, +2%]
    CHEAP = "CHEAP"  # < -2%


class PutSkewRegime(str, Enum):
    """Régimen de sesgo de puts (Risk Reversal proxy)."""

    ELEVATED = "ELEVATED"
    NORMAL = "NORMAL"
    INVERTED = "INVERTED"


class TermStructureRegime(str, Enum):
    """Régimen de estructura temporal de la IV."""

    CONTANGO = "CONTANGO"
    BACKWARDATION = "BACKWARDATION"
    FLAT = "FLAT"


class PDFTailRisk(str, Enum):
    """Clasificación de riesgo de cola de la PDF implícita."""

    LEFT_TAIL = "LEFT_TAIL"
    RIGHT_TAIL = "RIGHT_TAIL"
    SYMMETRIC = "SYMMETRIC"


class IVSurfaceSignal(str, Enum):
    """Señal compuesta de la superficie de IV."""

    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    CASH = "CASH"


# ─────────────────────────────────────────────────────────────────────────────
# REGIME THRESHOLDS (Constantes canónicas)
# ─────────────────────────────────────────────────────────────────────────────

HIGH_IV_RANK_THRESHOLD: float = 0.60
LOW_IV_RANK_THRESHOLD: float = 0.30
RICH_VRP_THRESHOLD: float = 0.02
CHEAP_VRP_THRESHOLD: float = -0.02
ELEVATED_SKEW_THRESHOLD: float = 0.03
INVERTED_SKEW_THRESHOLD: float = -0.03
CONTANGO_MIN_SLOPE: float = 1e-5
PDF_SKEW_LEFT_THRESHOLD: float = -0.5
PDF_SKEW_RIGHT_THRESHOLD: float = 0.5
OTM_WINDOW: float = 0.08
TARGET_DTE: float = 30.0
MIN_DTE: float = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# INPUT MODELS
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OptionEntry:
    """Entrada individual de un contrato en la cadena de opciones."""

    strike: float
    expiry: str  # "YYYY-MM-DD"
    option_type: str  # "call" | "put"
    iv: float
    open_interest: int = 0
    volume: int = 0
    bid: float | None = None
    ask: float | None = None


@dataclass(frozen=True)
class IVSurfaceInput:
    """Contrato de entrada para el cálculo de Superficie IV."""

    spot: float
    options_chain: list[OptionEntry]
    hist_close: np.ndarray
    expiries: list[str]
    risk_free_rate: float = 0.05
    ticker: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK OUTPUT MODELS
# ─────────────────────────────────────────────────────────────────────────────


class SkewProfile(BaseModel):
    """Métricas de sesgo por expiración."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    expiry: str
    dte: float = Field(..., ge=0.0)
    atm_iv: float = Field(..., ge=0.0)
    put_wing_iv: float | None = None
    call_wing_iv: float | None = None
    put_skew_pct: float | None = None
    call_skew_pct: float | None = None
    skew_25d: float | None = None
    butterfly: float | None = None
    skew_slope: float | None = None
    skew_regime: PutSkewRegime = PutSkewRegime.NORMAL

    @field_validator("atm_iv", mode="before")
    @classmethod
    def _clamp_atm_iv(cls, v: float) -> float:
        return max(0.0, float(v))


class TermStructurePoint(BaseModel):
    """Punto individual en la curva de estructura temporal."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    expiry: str
    dte: float = Field(..., ge=0.0)
    iv_atm: float = Field(..., ge=0.0)
    iv_rank_expiry: float | None = Field(default=None, ge=0.0, le=1.0)


class HistoricalVolatilityBlock(BaseModel):
    """Métricas de volatilidad histórica."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    hv_10d: float | None = Field(default=None, ge=0.0)
    hv_20d: float | None = Field(default=None, ge=0.0)
    hv_30d: float | None = Field(default=None, ge=0.0)
    rolling_hv_min: float | None = Field(default=None, ge=0.0)
    rolling_hv_max: float | None = Field(default=None, ge=0.0)


class VRPBlock(BaseModel):
    """Análisis de Volatility Risk Premium."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    vrp_10d: float | None = None
    vrp_20d: float | None = None
    vrp_30d: float | None = None
    vrp_log: float | None = None
    vrp_signal: VRPSignal = VRPSignal.FAIR


class TermStructureBlock(BaseModel):
    """Análisis completo de la estructura temporal de IV."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    regime: TermStructureRegime = TermStructureRegime.FLAT
    slope: float | None = None
    intercept: float | None = None
    r_squared: float | None = Field(default=None, ge=0.0, le=1.0)
    front_iv: float | None = Field(default=None, ge=0.0)
    back_iv: float | None = Field(default=None, ge=0.0)
    kink_dte: float | None = Field(default=None, ge=0.0)
    ts_signal: str = "NEUTRAL"
    points: list[TermStructurePoint] = Field(default_factory=list)


class ImpliedPDFBlock(BaseModel):
    """PDF implícita capturada via Breeden-Litzenberger."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    pdf_values: list[float] = Field(default_factory=list)
    strike_grid: list[float] = Field(default_factory=list)
    expected_strike: float | None = None
    pdf_std: float | None = Field(default=None, ge=0.0)
    skewness: float | None = None
    excess_kurtosis: float | None = None
    tail_risk: PDFTailRisk = PDFTailRisk.SYMMETRIC


class CompositeScoringBlock(BaseModel):
    """Resultado del scoring compuesto de superficie IV."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    score: float = Field(default=0.0, ge=-1.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    signal: IVSurfaceSignal = IVSurfaceSignal.HOLD
    n_sources: int = Field(default=0, ge=0, le=5)

    contrib_vrp: float | None = None
    contrib_rank: float | None = None
    contrib_ts: float | None = None
    contrib_skew: float | None = None
    contrib_pdf: float | None = None


# ─────────────────────────────────────────────────────────────────────────────
# TOP-LEVEL OUTPUT
# ─────────────────────────────────────────────────────────────────────────────


class IVSurfaceOutput(BaseModel):
    """Salida consolidada del análisis de superficie de IV."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    # Identidad
    ticker: str = ""
    spot: float = Field(default=0.0, ge=0.0)

    # Bloque A: ATM IV + contexto
    atm_iv: float | None = Field(default=None, ge=0.0)
    atm_iv_30d: float | None = Field(default=None, ge=0.0)
    atm_iv_60d: float | None = Field(default=None, ge=0.0)

    iv_rank: float = Field(default=0.5, ge=0.0, le=1.0)
    iv_percentile: float = Field(default=0.5, ge=0.0, le=1.0)
    iv_regime: IVRegime = IVRegime.NORMAL

    # Bloque B: VRP
    vrp_signal: VRPSignal = VRPSignal.FAIR
    vrp_details: VRPBlock | None = None

    # Bloque C: Skew profiles
    skew_profiles: list[SkewProfile] = Field(default_factory=list)
    skew_regime: PutSkewRegime = PutSkewRegime.NORMAL
    put_skew_25d: float | None = None
    call_skew_25d: float | None = None

    # Bloque D: Term structure
    term_structure: TermStructureRegime = TermStructureRegime.FLAT
    term_structure_full: TermStructureBlock | None = None

    # Bloque E: Implied PDF
    implied_pdf: list[float] = Field(default_factory=list)
    strike_grid: list[float] = Field(default_factory=list)
    pdf_skewness: float | None = None
    pdf_kurtosis: float | None = None
    pdf_tail_risk: PDFTailRisk = PDFTailRisk.SYMMETRIC
    pdf_details: ImpliedPDFBlock | None = None

    # Bloque F: Composite signal
    composite_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    composite_signal: IVSurfaceSignal = IVSurfaceSignal.HOLD
    composite_details: CompositeScoringBlock | None = None

    # Historical vol companion
    hv_20d: float | None = Field(default=None, ge=0.0)
    hv_30d: float | None = Field(default=None, ge=0.0)
    hv_details: HistoricalVolatilityBlock | None = None

    # Expected moves
    sigma_daily: float | None = None
    expected_move_1d: float | None = None
    expected_move_5d: float | None = None

    # Diagnostics
    expirations_used: list[str] = Field(default_factory=list)
    contracts_analyzed: int = 0
    error: str | None = None
    ok: bool = True

    @field_validator("iv_rank", "iv_percentile", mode="before")
    @classmethod
    def _clamp_rank(cls, v: float) -> float:
        return float(np.clip(float(v), 0.0, 1.0))

    @field_validator("composite_score", mode="before")
    @classmethod
    def _clamp_score(cls, v: float) -> float:
        return float(np.clip(float(v), -1.0, 1.0))


# ─────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: TÉCNICO
# Archivo      : iv_surface_models.py
# Sub-capa     : Modelo
# Eliminado    : Referencias a QuantumBeta V1, Alpha V5 y rutas legacy.
# Preservado   : Enums de régimen, umbrales canónicos y estructura de 6 bloques.
# Pendientes   : Ninguno.
# ─────────────────────────────────────────────────────────
