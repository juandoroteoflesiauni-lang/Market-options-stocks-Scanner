"""Modelos de salida de calibración R1 opciones. # [IM][TH]"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class R1FamilyWeights(BaseModel):
    """Pesos normalizados por familia (suman 1.0)."""

    model_config = ConfigDict(frozen=True)

    momentum: float = Field(ge=0.0, le=1.0)
    volume: float = Field(ge=0.0, le=1.0)
    structure: float = Field(ge=0.0, le=1.0)


class R1OptionsCalibrationMetrics(BaseModel):
    """Métricas del backtest de validación."""

    model_config = ConfigDict(frozen=True)

    n_samples: int = 0
    n_trades: int = 0
    sharpe: float | None = None
    profit_factor: float | None = None
    win_rate: float | None = None
    total_return_pct: float | None = None
    engine: str = "simple"


class R1OptionsCalibrationResult(BaseModel):
    """Artefacto persistido tras calibración C5."""

    model_config = ConfigDict(frozen=True)

    calibrated_at: str
    family_weights: R1FamilyWeights
    classic_weight: float = Field(ge=0.0, le=1.0, default=0.6)
    options_weight: float = Field(ge=0.0, le=1.0, default=0.4)
    entry_threshold: float = Field(ge=0.0, le=1.0, default=0.55)
    calibrator_path: str | None = None
    metrics: R1OptionsCalibrationMetrics = Field(default_factory=R1OptionsCalibrationMetrics)
    symbols: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


__all__ = [
    "R1FamilyWeights",
    "R1OptionsCalibrationMetrics",
    "R1OptionsCalibrationResult",
]
