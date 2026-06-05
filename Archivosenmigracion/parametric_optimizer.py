"""
backend/layer_3_specialists/ia_probabilistico/engines/parametric_optimizer.py
════════════════════════════════════════════════════════════════════════════════
Parametric Optimizer Engine — High-Fidelity Parallel Grid Search.
Stateless engine for exhaustive parameter sweeps across backtest scenarios.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import time
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, Final, List, Optional, Protocol, Tuple

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ..domain.flow_models import BacktestResult
from ..domain.optimization_models import (
    ObjectiveMetric,
    OptimizationResult,
    ParameterRange,
    ParameterSpace,
    ScenarioConfig,
    ScenarioResult,
)

logger = logging.getLogger("quantumbeta.engines.parametric_optimizer")

ParameterDict = dict[str, float]
BacktestOutcome = BacktestResult | tuple[BacktestResult, pd.Series]

_CHUNKS_PER_WORKER: Final[int] = 4
_MIN_STD: Final[float] = 1e-12

def _numeric_or_nan(value: float | None) -> float:
    return float(value) if value is not None else float("nan")

class BacktestCallable(Protocol):
    def __call__(self, data: pd.DataFrame, parameters: ParameterDict) -> BacktestOutcome: ...

class ParameterSpaceExpander:
    """Expand a parameter space into concrete scenario configurations."""
    @staticmethod
    def expand(param_space: ParameterSpace) -> Iterator[ScenarioConfig]:
        param_names = list(param_space.parameters.keys())
        param_values = [p.to_values() for p in param_space.parameters.values()]
        for scenario_id, combination in enumerate(itertools.product(*param_values)):
            yield ScenarioConfig(scenario_id=scenario_id, parameters=dict(zip(param_names, combination)))

class OptimizationAnalyzer:
    """Utility methods for post-optimization analysis."""
    @staticmethod
    def surface_stability_score(top_10: list[ScenarioResult]) -> float:
        valid = [s for s in top_10 if s.objective_score != float("-inf")]
        if len(valid) < 2: return 0.0
        scores = [s.objective_score for s in valid]
        mean_score = sum(scores) / len(scores)
        if abs(mean_score) < _MIN_STD: return 0.0
        std_score = (sum((s - mean_score) ** 2 for s in scores) / len(scores)) ** 0.5
        return float(round(max(0.0, 1.0 - (std_score / abs(mean_score))), 4))

def _execute_chunk(
    chunk: list[ScenarioConfig],
    df_data: pd.DataFrame,
    backtest_func: BacktestCallable,
    objective_metric: ObjectiveMetric,
    min_trades: int,
    penalty_decay: float,
) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for scenario in chunk:
        try:
            output = backtest_func(data=df_data, parameters=scenario.parameters)
            res = output[0] if isinstance(output, tuple) else output

            if not res.ok:
                results.append(ScenarioResult(
                    scenario_id=scenario.scenario_id, parameters=scenario.parameters,
                    objective_score=float("-inf"), raw_score=float("-inf"), n_trades=0, error=res.error or "Error",
                ))
                continue

            # Score extraction
            score_map = {"sharpe_ratio": res.sharpe_ratio, "sortino_ratio": res.sortino_ratio, "calmar_ratio": res.calmar_ratio, "total_return": res.total_return_pct}
            raw = float(score_map.get(objective_metric) or float("-inf"))

            # Penalization
            n_t = int(res.trades_count or 0)
            final_score, was_penalized = (float("-inf"), True) if n_t < min_trades else (raw, False)
            if not was_penalized and penalty_decay > 0 and n_t < min_trades * 2:
                 final_score *= (1.0 - math.exp(-penalty_decay * (n_t - min_trades)))

            results.append(ScenarioResult(
                scenario_id=scenario.scenario_id, parameters=scenario.parameters,
                objective_score=final_score, raw_score=raw, n_trades=n_t,
                sharpe_ratio=_numeric_or_nan(res.sharpe_ratio),
                sortino_ratio=_numeric_or_nan(res.sortino_ratio),
                calmar_ratio=_numeric_or_nan(res.calmar_ratio),
                total_return=_numeric_or_nan(res.total_return_pct),
                max_drawdown=_numeric_or_nan(res.max_drawdown_pct),
                was_penalized=was_penalized
            ))
        except Exception as e:
            results.append(ScenarioResult(
                scenario_id=scenario.scenario_id, parameters=scenario.parameters,
                objective_score=float("-inf"), raw_score=float("-inf"), n_trades=0, error=str(e)
            ))
    return results

class ParametricOptimizerEngine:
    """Exhaustive parameter optimizer with multiprocessing support."""
    @staticmethod
    def optimize(
        data: pd.DataFrame,
        param_space: ParameterSpace,
        backtest_func: BacktestCallable,
        objective_metric: ObjectiveMetric = "sharpe_ratio",
        min_trades: int = 50,
        n_workers: int | None = None,
        penalty_decay: float = 0.0
    ) -> OptimizationResult | None:
        try:
            start_t = time.perf_counter()
            if data.empty or not param_space.parameters: return None

            scenarios = list(ParameterSpaceExpander.expand(param_space))
            workers = max(1, n_workers or os.cpu_count() or 1)

            # Parallel execution setup
            chunk_size = max(1, math.ceil(len(scenarios) / (workers * _CHUNKS_PER_WORKER)))
            chunks = [scenarios[i:i + chunk_size] for i in range(0, len(scenarios), chunk_size)]

            all_results: list[ScenarioResult] = []
            if workers == 1:
                for c in chunks: all_results.extend(_execute_chunk(c, data, backtest_func, objective_metric, min_trades, penalty_decay))
            else:
                with ProcessPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(_execute_chunk, c, data, backtest_func, objective_metric, min_trades, penalty_decay) for c in chunks]
                    for f in as_completed(futures): all_results.extend(f.result())

            if not all_results: return None
            sorted_res = sorted(all_results, key=lambda r: r.objective_score, reverse=True)
            if sorted_res[0].objective_score == float("-inf"): return None

            return OptimizationResult(
                optimal_parameters=sorted_res[0].parameters,
                best_objective_score=sorted_res[0].objective_score,
                total_scenarios_run=len(all_results),
                successful_scenarios=sum(1 for r in all_results if r.error is None),
                failed_scenarios=sum(1 for r in all_results if r.error is not None),
                penalized_scenarios=sum(1 for r in all_results if r.was_penalized and r.error is None),
                top_10_scenarios=tuple(sorted_res[:10]),
                execution_time_sec=time.perf_counter() - start_t,
                parameter_space_size=len(scenarios),
                n_workers_used=workers,
                objective_metric=objective_metric,
                min_trades_threshold=min_trades
            )
        except Exception as e:
            logger.error(f"Optimization failed: {e}"); return None

# ────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: IA / PROBABILÍSTICO
# Archivo        : parametric_optimizer.py
# Sub-capa       : Engine (Parallel Parameter Optimizer)
# framework      : concurrent.futures | pandas | numpy
# Descripcion    : Integración institutional de Grid Search paralelo.
# ────────────────────────────────────────────────────────────────
