from __future__ import annotations
"""Contratos de Dominio para el Motor TPO (Time Price Opportunity) Skewness — Sector Técnico.

Define las enumeraciones, perfiles, niveles de precio, señales y configuraciones
para la clasificación de la forma del perfil y asimetría de distribución.
"""


from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ProfileShape(StrEnum):
    """Morphological classification of a TPO distribution."""

    NormalDistribution = "NormalDistribution"
    PShape = "PShape"
    BShape = "bShape"
    DDoubleDistribution = "DDoubleDistribution"
    Transitional = "Transitional"


class TPOLevel(BaseModel):
    """Single price level inside a TPO profile."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    price: float
    tpo_count: int
    brackets: tuple[int, ...] = ()


class TPOProfile(BaseModel):
    """Statistical summary of a TPO profile."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    session_id: str
    session_start: str | None = None
    session_end: str | None = None
    highest_price: float | None = None
    lowest_price: float | None = None
    poc_price: float | None = None
    mean_price: float | None = None
    standard_deviation: float | None = None
    skewness: float | None = None
    total_tpos: int = 0
    level_count: int = 0
    levels: tuple[TPOLevel, ...] = ()


class TPOSkewnessSignal(BaseModel):
    """Terminal output emitted by the TPO skewness engine."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ok: bool
    error: str | None = None
    timestamp: str | None = None
    skewness_value: float | None = None
    profile_shape: ProfileShape = ProfileShape.Transitional
    snapshot: TPOProfile | None = None
    tick_size: float | None = None
    bracket_count: int = 0
    is_intraday_input: bool = False


class TPOSkewnessConfig(BaseModel):
    """Runtime configuration for the TPO skewness engine."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    tick_size: float | None = None
    bracket_duration_minutes: int = 30
    session_start_time: str = "09:30"
    skew_threshold: float = 0.50
    symmetry_threshold: float = 0.15
    bimodal_gap_ticks: int = 6
    max_bins_per_bar: int = 500
    max_total_levels: int = 2500
    compact_level_limit: int = 80
