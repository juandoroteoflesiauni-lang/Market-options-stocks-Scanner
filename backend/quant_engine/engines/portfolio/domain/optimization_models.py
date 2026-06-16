from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

ObjectiveMetric = Literal[
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "total_return",
]


class ParameterRange(BaseModel):
    """Discrete range for a single strategy parameter."""

    model_config = ConfigDict(frozen=True)

    min: float = Field(..., description="Inclusive minimum value.")
    max: float = Field(..., description="Inclusive maximum value.")
    step: float = Field(..., gt=0.0, description="Discretization step.")

    @model_validator(mode="after")
    def validate_range_coherence(self) -> ParameterRange:
        if self.min >= self.max:
            raise ValueError(
                f"ParameterRange inválido: min ({self.min}) debe ser < max ({self.max})."
            )
        if self.step > (self.max - self.min):
            raise ValueError(
                f"ParameterRange inválido: step ({self.step}) excede el rango ({self.max - self.min})."
            )
        return self

    def to_values(self) -> list[float]:
        n_steps = round((self.max - self.min) / self.step)
        return [round(self.min + i * self.step, 10) for i in range(n_steps + 1)]


class ParameterSpace(BaseModel):
    """Map of parameter name to discrete range."""

    model_config = ConfigDict(frozen=True)

    parameters: dict[str, ParameterRange] = Field(
        ...,
        description="Parameter name to ParameterRange mapping.",
        min_length=1,
    )

    @property
    def total_combinations(self) -> int:
        total = 1
        for param_range in self.parameters.values():
            total *= len(param_range.to_values())
        return total


class ScenarioConfig(BaseModel):
    """Concrete parameter combination inside the search space."""

    model_config = ConfigDict(frozen=True)

    scenario_id: int = Field(..., ge=0)
    parameters: dict[str, float] = Field(..., min_length=1)


class ScenarioResult(BaseModel):
    """Result of a single evaluated scenario."""

    model_config = ConfigDict(frozen=True)

    scenario_id: int
    parameters: dict[str, float]
    objective_score: float
    raw_score: float
    n_trades: int
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    total_return: float
    max_drawdown: float
    was_penalized: bool = False
    error: str | None = None


class OptimizationResult(BaseModel):
    """Final output of the parametric optimizer."""

    model_config = ConfigDict(frozen=True)

    optimal_parameters: dict[str, float]
    best_objective_score: float
    total_scenarios_run: int
    successful_scenarios: int
    failed_scenarios: int
    penalized_scenarios: int
    top_10_scenarios: tuple[ScenarioResult, ...] = Field(default_factory=tuple)
    execution_time_sec: float
    parameter_space_size: int
    n_workers_used: int
    objective_metric: ObjectiveMetric
    min_trades_threshold: int


class CapmAssetMetrics(BaseModel):
    """CAPM metrics for one asset."""

    model_config = ConfigDict(frozen=True)

    beta: float
    expected_return_capm: float
    alpha_jensen: float
    r_squared: float


class OptimizedPortfolioStats(BaseModel):
    """Statistics for one optimized portfolio."""

    model_config = ConfigDict(frozen=True)

    weights: dict[str, float]
    expected_return: float
    volatility: float
    sharpe_ratio: float


class PortfolioOptimizationResult(BaseModel):
    """Full result contract for Markowitz and CAPM portfolio optimization."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    covariance_matrix: pd.DataFrame
    correlation_matrix: pd.DataFrame
    min_variance_portfolio: OptimizedPortfolioStats
    tangency_portfolio: OptimizedPortfolioStats
    capm_metrics: dict[str, CapmAssetMetrics]
    efficient_frontier: pd.DataFrame
    is_valid_optimization: bool
    warnings: list[str] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: PORTAFOLIO
# Archivo         : optimization_models.py
# Sub-capa        : Modelo
# Solver/Optimizer: [scipy | cvxpy | cvxopt]
# Eliminado       : Referencias legacy de QuantumBeta V1, extensiones __all__ manuales
# Preservado      : Scenarios (Parametric), CAPM, Markowitz (Portfolio)
# Pendientes      : Integración de engines de optimización de portafolio
# ────────────────────────────────────────────────────────────────────
