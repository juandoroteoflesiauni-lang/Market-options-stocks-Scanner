"""
backend/engine/metrics/parametric_optimizer.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Parametric Optimizer Engine — High-Fidelity Parallel Grid Search.
Stateless engine for exhaustive parameter sweeps across backtest scenarios.
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import time
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Final, Literal, Protocol

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.parametric_optimizer")

type FloatArray = npt.NDArray[np.float64]
ParameterDict = dict[str, float]

ObjectiveMetric = Literal["sharpe_ratio", "sortino_ratio", "calmar_ratio", "total_return"]

_CHUNKS_PER_WORKER: Final[int] = 4
_MIN_STD: Final[float] = 1e-12


def _numeric_or_nan(value: float | None) -> float:
    return float(value) if value is not None else float("nan")


# ────────────────────────────────────────────────────────────────
# PYDANTIC DOMAIN MODELS
# ────────────────────────────────────────────────────────────────

class ParameterRange(BaseModel):
    """Range of values for a parameter configuration."""
    model_config = ConfigDict(frozen=True)

    start: float
    stop: float
    step: float

    def to_values(self) -> list[float]:
        """Convert range specs to list of float values."""
        if self.step <= 0.0:
            return [self.start]
        values = []
        curr = self.start
        limit = 1000
        count = 0
        while curr <= self.stop + 1e-9 and count < limit:
            values.append(float(curr))
            curr += self.step
            count += 1
        return values


class ParameterSpace(BaseModel):
    """Collection of parameter ranges defining the sweep search space."""
    model_config = ConfigDict(frozen=True)
    parameters: dict[str, ParameterRange]


class ScenarioConfig(BaseModel):
    """Unique combination of parameter values representing one test case."""
    model_config = ConfigDict(frozen=True)
    scenario_id: int
    parameters: ParameterDict


class BacktestResult(BaseModel):
    """Result details of a single backtest execution (Institutional Alignment)."""
    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    trades_count: int | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    total_return_pct: float | None = None
    max_drawdown_pct: float | None = None


BacktestOutcome = BacktestResult | tuple[BacktestResult, FloatArray]


class BacktestCallable(Protocol):
    """Callback protocol defining backtest simulation function signature."""
    def __call__(self, data: FloatArray, parameters: ParameterDict) -> BacktestOutcome:
        ...


class ScenarioResult(BaseModel):
    """Result metrics of a single parameter configuration backtest run."""
    model_config = ConfigDict(frozen=True)

    scenario_id: int
    parameters: ParameterDict
    objective_score: float
    raw_score: float
    n_trades: int
    sharpe_ratio: float = float("nan")
    sortino_ratio: float = float("nan")
    calmar_ratio: float = float("nan")
    total_return: float = float("nan")
    max_drawdown: float = float("nan")
    was_penalized: bool = False
    error: str | None = None


class OptimizationResult(BaseModel):
    """Consolidated outcome of the optimization grid sweep."""
    model_config = ConfigDict(frozen=True)

    optimal_parameters: ParameterDict
    best_objective_score: float
    total_scenarios_run: int
    successful_scenarios: int
    failed_scenarios: int
    penalized_scenarios: int
    top_10_scenarios: tuple[ScenarioResult, ...]
    execution_time_sec: float
    parameter_space_size: int
    n_workers_used: int
    objective_metric: str
    min_trades_threshold: int


# ────────────────────────────────────────────────────────────────
# OPTIMIZATION ANALYZERS & HELPERS
# ────────────────────────────────────────────────────────────────

class ParameterSpaceExpander:
    """Expand a parameter space into concrete scenario configurations."""

    @staticmethod
    def expand(param_space: ParameterSpace) -> Iterator[ScenarioConfig]:
        param_names = list(param_space.parameters.keys())
        param_values = [p.to_values() for p in param_space.parameters.values()]
        for scenario_id, combination in enumerate(itertools.product(*param_values)):
            yield ScenarioConfig(
                scenario_id=scenario_id,
                parameters=dict(zip(param_names, combination, strict=True)),
            )


class OptimizationAnalyzer:
    """Utility methods for post-optimization analysis."""

    @staticmethod
    def surface_stability_score(top_10: list[ScenarioResult]) -> float:
        valid = [s for s in top_10 if s.objective_score != float("-inf")]
        if len(valid) < 2:
            return 0.0
        scores = [s.objective_score for s in valid]
        mean_score = sum(scores) / len(scores)
        if abs(mean_score) < _MIN_STD:
            return 0.0
        std_score = (sum((s - mean_score) ** 2 for s in scores) / len(scores)) ** 0.5
        return float(round(max(0.0, 1.0 - (std_score / abs(mean_score))), 4))


def _execute_chunk(
    chunk: list[ScenarioConfig],
    data: FloatArray,
    backtest_func: BacktestCallable,
    objective_metric: ObjectiveMetric,
    min_trades: int,
    penalty_decay: float,
) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for scenario in chunk:
        try:
            output = backtest_func(data=data, parameters=scenario.parameters)
            res = output[0] if isinstance(output, tuple) else output

            if not res.ok:
                results.append(
                    ScenarioResult(
                        scenario_id=scenario.scenario_id,
                        parameters=scenario.parameters,
                        objective_score=float("-inf"),
                        raw_score=float("-inf"),
                        n_trades=0,
                        error=res.error or "Error",
                    )
                )
                continue

            # Score extraction
            score_map = {
                "sharpe_ratio": res.sharpe_ratio,
                "sortino_ratio": res.sortino_ratio,
                "calmar_ratio": res.calmar_ratio,
                "total_return": res.total_return_pct,
            }
            raw = float(score_map.get(objective_metric) or float("-inf"))

            # Penalization
            n_t = int(res.trades_count or 0)
            final_score, was_penalized = (
                (float("-inf"), True) if n_t < min_trades else (raw, False)
            )
            if not was_penalized and penalty_decay > 0.0 and n_t < min_trades * 2:
                final_score *= 1.0 - math.exp(-penalty_decay * (n_t - min_trades))

            results.append(
                ScenarioResult(
                    scenario_id=scenario.scenario_id,
                    parameters=scenario.parameters,
                    objective_score=final_score,
                    raw_score=raw,
                    n_trades=n_t,
                    sharpe_ratio=_numeric_or_nan(res.sharpe_ratio),
                    sortino_ratio=_numeric_or_nan(res.sortino_ratio),
                    calmar_ratio=_numeric_or_nan(res.calmar_ratio),
                    total_return=_numeric_or_nan(res.total_return_pct),
                    max_drawdown=_numeric_or_nan(res.max_drawdown_pct),
                    was_penalized=was_penalized,
                )
            )
        except Exception as e:
            results.append(
                ScenarioResult(
                    scenario_id=scenario.scenario_id,
                    parameters=scenario.parameters,
                    objective_score=float("-inf"),
                    raw_score=float("-inf"),
                    n_trades=0,
                    error=str(e),
                )
            )
    return results


class ParametricOptimizerEngine:
    """Exhaustive parameter optimizer with multiprocessing support."""

    @staticmethod
    def optimize(
        data: FloatArray,
        param_space: ParameterSpace,
        backtest_func: BacktestCallable,
        objective_metric: ObjectiveMetric = "sharpe_ratio",
        min_trades: int = 50,
        n_workers: int | None = None,
        penalty_decay: float = 0.0,
    ) -> Result[OptimizationResult]:
        """
        Runs exhaustive parameter sweep over scenarios in parallel.
        """
        try:
            start_t = time.perf_counter()

            if len(data) == 0:
                return Result.failure(reason="Data array is empty")
            if not param_space.parameters:
                return Result.failure(reason="Invalid parameter space: parameters dict is empty")

            scenarios = list(ParameterSpaceExpander.expand(param_space))
            if not scenarios:
                return Result.failure(reason="Parameter range combinations resulted in 0 scenarios")

            workers = max(1, n_workers or os.cpu_count() or 1)

            # Parallel execution setup
            chunk_size = max(1, math.ceil(len(scenarios) / (workers * _CHUNKS_PER_WORKER)))
            chunks = [scenarios[i : i + chunk_size] for i in range(0, len(scenarios), chunk_size)]

            all_results: list[ScenarioResult] = []
            if workers == 1:
                for c in chunks:
                    all_results.extend(
                        _execute_chunk(
                            c,
                            data,
                            backtest_func,
                            objective_metric,
                            min_trades,
                            penalty_decay,
                        )
                    )
            else:
                with ProcessPoolExecutor(max_workers=workers) as executor:
                    futures = [
                        executor.submit(
                            _execute_chunk,
                            c,
                            data,
                            backtest_func,
                            objective_metric,
                            min_trades,
                            penalty_decay,
                        )
                        for c in chunks
                    ]
                    for f in as_completed(futures):
                        all_results.extend(f.result())

            if not all_results:
                return Result.failure(reason="Optimization finished but produced no results")

            sorted_res = sorted(all_results, key=lambda r: r.objective_score, reverse=True)
            if sorted_res[0].objective_score == float("-inf"):
                return Result.failure(
                    reason="All optimization scenarios resulted in infinite penalty (-inf)"
                )

            opt_res = OptimizationResult(
                optimal_parameters=sorted_res[0].parameters,
                best_objective_score=sorted_res[0].objective_score,
                total_scenarios_run=len(all_results),
                successful_scenarios=sum(1 for r in all_results if r.error is None),
                failed_scenarios=sum(1 for r in all_results if r.error is not None),
                penalized_scenarios=sum(
                    1 for r in all_results if r.was_penalized and r.error is None
                ),
                top_10_scenarios=tuple(sorted_res[:10]),
                execution_time_sec=time.perf_counter() - start_t,
                parameter_space_size=len(scenarios),
                n_workers_used=workers,
                objective_metric=objective_metric,
                min_trades_threshold=min_trades,
            )
            return Result.success(opt_res)
        except Exception as e:
            logger.error("Optimization failed: %s", e)
            return Result.failure(reason=f"Optimization failed: {e}")
