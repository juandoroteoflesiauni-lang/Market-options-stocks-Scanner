"""
backend/layer_3_specialists/ia_probabilistico/domain/optimization_models.py
════════════════════════════════════════════════════════════════════════════════
Domain models for Parametric Optimization and Scenario Analysis.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

ObjectiveMetric = Literal["sharpe_ratio", "sortino_ratio", "calmar_ratio", "total_return"]


class ParameterRange(BaseModel):
    """Range of values for a single parameter."""

    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    step: float

    def to_values(self) -> list[float]:
        if self.step <= 0:
            return [self.start]
        return list(np.arange(self.start, self.end + self.step, self.step))


class ParameterSpace(BaseModel):
    """Collection of parameter ranges to explore."""

    model_config = ConfigDict(frozen=True)
    parameters: dict[str, ParameterRange]


class ScenarioConfig(BaseModel):
    """Specific configuration for a single backtest scenario."""

    model_config = ConfigDict(frozen=True)
    scenario_id: int
    parameters: dict[str, float]


class ScenarioResult(BaseModel):
    """Result of a single backtest scenario execution."""

    model_config = ConfigDict(frozen=True)

    scenario_id: int
    parameters: dict[str, float]
    objective_score: float
    raw_score: float
    n_trades: int

    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0

    was_penalized: bool = False
    error: str | None = None


class OptimizationResult(BaseModel):
    """Summary result of an optimization run."""

    model_config = ConfigDict(frozen=True)

    optimal_parameters: dict[str, float]
    best_objective_score: float
    total_scenarios_run: int
    successful_scenarios: int
    failed_scenarios: int
    penalized_scenarios: int
    top_10_scenarios: tuple[ScenarioResult, ...]

    execution_time_sec: float
    parameter_space_size: int
    n_workers_used: int
    objective_metric: ObjectiveMetric
    min_trades_threshold: int


class OptionsFlowToxicityMultiplier(BaseModel):
    """Pydantic model for Options Flow Toxicity multiplier output."""

    multiplier: float = Field(..., ge=0.0, le=1.0)
    toxicity_score: float = Field(..., ge=0.0, le=1.0)
    reason: str


class ShadowDeltaPositionMultiplier(BaseModel):
    """Pydantic model for Shadow Delta position multiplier output."""

    multiplier: float = Field(..., ge=1.0, le=1.4)
    edge_signal: float = Field(..., ge=0.0, le=1.0)
    reason: str
    delta_divergence: float = Field(0.0)
