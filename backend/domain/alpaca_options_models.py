"""Modelos de confluencia de opciones para Ruta 1 Alpaca. # [IM][TH]"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

OptionsDirection = Literal["BULL", "BEAR", "NEUTRAL"]
OptionsFamily = Literal["momentum", "volume", "structure"]


class OptionsEngineSignal(BaseModel):
    """Señal normalizada de un motor híbrido de opciones."""

    model_config = ConfigDict(frozen=True)

    engine: str
    family: OptionsFamily
    direction: OptionsDirection
    score: float = Field(ge=0.0, le=1.0)
    detail: dict[str, Any] = Field(default_factory=dict)


class OptionsConfluence(BaseModel):
    """Sub-score agregado de confluencia LONG-only para R1."""

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0.0, le=1.0)
    by_family: dict[str, float] = Field(default_factory=dict)
    by_engine: dict[str, float] = Field(default_factory=dict)
    dominant_direction: OptionsDirection = "NEUTRAL"
    critical: bool = False
    moderate: bool = False
    reason_codes: tuple[str, ...] = ()


class Route1OptionsSnapshotContext(BaseModel):
    """Contexto de snapshot ~5min para replay broadcast en R1."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: str
    available: bool = False
    features: dict[str, Any] = Field(default_factory=dict)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    call_wall: float | None = None
    put_wall: float | None = None
    max_pain: float | None = None


__all__ = [
    "OptionsConfluence",
    "OptionsDirection",
    "OptionsEngineSignal",
    "OptionsFamily",
    "Route1OptionsSnapshotContext",
]
