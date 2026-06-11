"""
backend/layer_3_specialists/ia_probabilistico/domain/stochastic_models.py
════════════════════════════════════════════════════════════════════════════════
Domain contracts for stochastic modeling and path simulations.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FanChart(BaseModel):
    """Percentile structure for projection visualization."""

    model_config = ConfigDict(frozen=True)

    p10: list[float] = Field(..., description="Percentile 10 (Extreme Support)")
    p25: list[float] = Field(..., description="Percentile 25 (Moderate Support)")
    p50: list[float] = Field(..., description="Percentile 50 (Median / EV)")
    p75: list[float] = Field(..., description="Percentile 75 (Moderate Resistance)")
    p90: list[float] = Field(..., description="Percentile 90 (Extreme Resistance)")
    timestamps: list[str] = Field(..., description="Projected dates in ISO format")


from typing import Literal


class StochasticPredictiveResult(BaseModel):
    """Final output of the stochastic predictive engine."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    jump_intensity: float = 0.0
    vol_of_vol: float = 0.0
    fan_chart: FanChart | None = None
    predictability_score: float = Field(0.5, ge=0, le=1, description="Entropy-based predictability")
    drift_bias: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"
    expected_value_horizon: float = 0.0

    ok: bool = True
    error: str | None = None


class HestonParams(BaseModel):
    """Parameters for the Heston Stochastic Volatility model."""

    kappa: float = Field(..., description="Mean reversion speed")
    theta: float = Field(..., description="Long-term variance")
    vov: float = Field(..., description="Volatility of volatility")
    rho: float = Field(..., description="Correlation between price and vol")
    v0: float = Field(..., description="Initial variance")


class MJDParams(BaseModel):
    """Parameters for the Merton Jump-Diffusion model."""

    lambd: float = Field(..., description="Jump intensity (Poisson rate)")
    mu_j: float = Field(..., description="Mean jump size (log-scale)")
    sigma_j: float = Field(..., description="Jump size volatility")


class SimulationConfig(BaseModel):
    """Configuration for Monte Carlo simulations."""

    n_paths: int = Field(default=1000, ge=100)
    n_points: int = Field(default=30, ge=1)
    # MIGRATION: seed=42 — confirmar si este seed reproduce el modelo de producción
    seed: int | None = 42


# ────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: IA / PROBABILÍSTICO
# Archivo        : stochastic_models.py
# Sub-capa       : Modelo (Domain Contracts)
# Framework ML   : Pydantic
# Eliminado      : Referencias legacy a Opciones/GEX sector.
# Preservado     : FanChart, StochasticPredictiveResult, Heston/MJD/Simulation Configs.
# Pendientes     : Calibración de seed de producción.
# ────────────────────────────────────────────────────────────────
