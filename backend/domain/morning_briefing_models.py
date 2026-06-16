from __future__ import annotations
"""
backend/domain/morning_briefing_models.py
════════════════════════════════════════════════════════════════════════════════
Domain models for Morning Briefing and Macro Sentiment.
════════════════════════════════════════════════════════════════════════════════
"""


from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class RiskRegime(str, Enum):
    """Global risk appetite regimes for the session."""

    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"
    SHOCK = "SHOCK"


class SectorTilt(str, Enum):
    """Canonical sectors for daily overweighting."""

    TECNOLOGIA = "Tecnología"
    DEFENSIVOS = "Defensivos"
    BANCOS = "Bancos"
    ENERGIA = "Energía"
    SALUD = "Salud"
    CONSUMO = "Consumo Discrecional"
    MATERIALES = "Materiales"
    INMOBILIARIO = "Inmobiliario"
    UTILIDADES = "Utilidades"
    NEUTRO = "Neutro"


class MacroSnapshot(BaseModel):
    """Macroeconomic state at the time of briefing."""

    model_config = ConfigDict(frozen=True)

    vix_level: float = Field(ge=0, le=150)
    vix_1d_change: float = 0.0
    us_10y_yield: float = Field(ge=0, le=25)
    us_2y_yield: float = Field(default=4.0, ge=0, le=25)
    us_10y_1d_change: float = 0.0
    dxy_level: float = Field(default=100.0, ge=50, le=200)
    dxy_1d_change: float = 0.0
    crude_wti: float = Field(default=70.0, ge=0)
    gold_spot: float = Field(default=2000.0, ge=0)
    spx_futures_pct: float = 0.0
    ndx_futures_pct: float = 0.0
    ig_spread_bps: float = 120.0
    hy_spread_bps: float = 400.0
    snapshot_utc: datetime


class NewsEvent(BaseModel):
    """Event news item processed by the NLP pipeline."""

    model_config = ConfigDict(frozen=True)

    headline: str = Field(min_length=5, max_length=300)
    source: str = Field(min_length=2, max_length=60)
    region: str = "US"
    sentiment: float = Field(default=0.0, ge=-1.0, le=1.0)
    impact_score: float = Field(default=0.5, ge=0.0, le=1.0)
    published_utc: datetime


class MorningBriefResult(BaseModel):
    """Result from the MorningBriefingEngine."""

    model_config = ConfigDict(frozen=True)

    risk_regime: RiskRegime
    conviction_score: Annotated[float, Field(ge=0.0, le=1.0)]
    key_drivers: Annotated[list[str], Field(min_length=3, max_length=3)]
    sector_tilt: SectorTilt
    generated_at: datetime
    llm_raw_response: str | None = Field(default=None, exclude=True)
    is_fallback: bool = Field(default=False)


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : morning_briefing_models.py
# Sub-capa         : Domain / Contracts
# Enfoque          : Contratos para el Morning Briefing y Macro Sentiment.
# ─────────────────────────────────────────────────────────────────────
