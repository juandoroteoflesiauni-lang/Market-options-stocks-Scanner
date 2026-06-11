"""Contratos de Dominio para el Motor FVG (Fair Value Gap) — Sector Técnico.

Define los tipos, estados y estructuras de datos para la detección y mitigación
de Fair Value Gaps de forma determinista y JSON-safe.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FVGType(StrEnum):
    """Directional type of a Fair Value Gap."""

    BULLISH = "Bullish"
    BEARISH = "Bearish"


class FVGStatus(StrEnum):
    """Lifecycle state of an FVG zone."""

    ACTIVE = "Active"
    PARTIALLY_MITIGATED = "PartiallyMitigated"
    FULLY_MITIGATED = "FullyMitigated"
    INVALIDATED = "Invalidated"


class Candle(BaseModel):
    """OHLCV candle consumed by the FVG engine."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class FVGZone(BaseModel):
    """Fair Value Gap zone and its current mitigation cursor."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    creation_timestamp: str
    type: FVGType
    top_price: float
    bottom_price: float
    original_gap_size: float
    current_mitigation_level: float
    status: FVGStatus
    mitigation_pct: float = 0.0
    is_consequent_encroachment: bool = False
    is_iofed: bool = False
    mitigated_timestamp: str | None = None
    mitigated_at_index: int | None = None


class FVGConfig(BaseModel):
    """Runtime knobs for FVG detection."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    min_gap_size: float | None = Field(default=None, ge=0)
    max_active_fvgs: int = Field(default=100, ge=1)
    tick_size: float | None = Field(default=None, gt=0)
    mitigated_ttl_candles: int = Field(default=0, ge=0)


class FVGEvent(BaseModel):
    """Lifecycle event emitted by the engine."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    type: str
    zone: FVGZone
    candle: Candle


class FVGAnalysisOutput(BaseModel):
    """Compact JSON-safe output for the technical terminal."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = True
    ok: bool = True
    error: str | None = None
    active_count: int = 0
    history_count: int = 0
    bullish_active_count: int = 0
    bearish_active_count: int = 0
    partial_count: int = 0
    consequent_encroachment_count: int = 0
    iofed_count: int = 0
    tick_size: float | None = None
    min_gap_size: float | None = None
    active_zones: tuple[FVGZone, ...] = ()
    recent_events: tuple[FVGEvent, ...] = ()
