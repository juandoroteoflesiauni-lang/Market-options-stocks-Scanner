from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class OptimizationStrategy(str, Enum):
    MVO = "MARKOWITZ_MEAN_VARIANCE"
    HRP = "HIERARCHICAL_RISK_PARITY"
    RISK_PARITY = "EQUAL_RISK_CONTRIBUTION"
    BLACK_LITTERMAN = "BLACK_LITTERMAN_VIEWS"


class PortfolioWeights(BaseModel):
    """Ticker to weight mapping."""

    model_config = ConfigDict(frozen=True)
    weights: dict[str, float] = Field(default_factory=dict)


class AssetStats(BaseModel):
    model_config = ConfigDict(frozen=True)
    expected_return: float
    volatility: float
    beta: float
    mic_score: float


class QuantumPortfolioResult(BaseModel):
    """Consolidated Portfolio Optimization Result."""

    model_config = ConfigDict(frozen=True)

    timestamp: str
    strategy: OptimizationStrategy
    weights: dict[str, float]
    expected_return: float
    volatility: float
    sharpe_ratio: float

    # Diagnostics
    diversification_ratio: float = 0.0
    hhi_index: float = 0.0
    is_valid: bool = True
    warnings: list[str] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: PORTAFOLIO
# Archivo         : portfolio_models.py
# Sub-capa        : Modelo
# Solver/Optimizer: N/A
# Eliminado       : Referencias legacy, Pydantic v1 patterns
# Preservado      : OptimizationStrategy, PortfolioWeights, AssetStats, QuantumPortfolioResult
# Pendientes      : Integración con Portfolio Engine
# ────────────────────────────────────────────────────────────────────
