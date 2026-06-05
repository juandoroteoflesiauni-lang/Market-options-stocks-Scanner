import numpy as np
import pytest

from backend.engine.metrics.parametric_optimizer import (
    ParametricOptimizerEngine,
    ParameterRange,
    ParameterSpace,
    BacktestResult,
    OptimizationResult,
)
from backend.models.result import Result


def dummy_backtest(data: np.ndarray, parameters: dict[str, float]) -> BacktestResult:
    # A simple backtest that returns metrics based on parameter values
    param1 = parameters.get("param1", 0.0)
    param2 = parameters.get("param2", 0.0)
    
    # We'll make param1 = 1.5 and param2 = 0.5 the "optimal" parameters
    if abs(param1 - 1.5) < 1e-9 and abs(param2 - 0.5) < 1e-9:
        return BacktestResult(
            ok=True,
            trades_count=60,
            sharpe_ratio=2.5,
            sortino_ratio=3.0,
            calmar_ratio=1.5,
            total_return_pct=15.0,
            max_drawdown_pct=10.0
        )
    return BacktestResult(
        ok=True,
        trades_count=40,  # Below min_trades limit of 50
        sharpe_ratio=1.0,
        sortino_ratio=1.2,
        calmar_ratio=0.5,
        total_return_pct=5.0,
        max_drawdown_pct=10.0
    )


def test_parametric_optimizer_engine():
    # Setup parameter space
    param_space = ParameterSpace(
        parameters={
            "param1": ParameterRange(start=1.0, stop=2.0, step=0.5),  # 1.0, 1.5, 2.0
            "param2": ParameterRange(start=0.0, stop=1.0, step=0.5),  # 0.0, 0.5, 1.0
        }
    )
    
    # Mock data array
    data = np.random.rand(100, 5)

    # 1. Test optimize with valid configuration
    # Set min_trades = 30 so non-optimal combination is not penalized out completely
    res = ParametricOptimizerEngine.optimize(
        data=data,
        param_space=param_space,
        backtest_func=dummy_backtest,
        objective_metric="sharpe_ratio",
        min_trades=30,
        n_workers=1
    )
    assert isinstance(res, Result)
    assert res.is_success
    opt_result = res.unwrap()
    assert isinstance(opt_result, OptimizationResult)
    assert opt_result.optimal_parameters["param1"] == 1.5
    assert opt_result.optimal_parameters["param2"] == 0.5
    assert opt_result.best_objective_score == 2.5
    assert opt_result.total_scenarios_run == 9  # 3 * 3
    assert opt_result.successful_scenarios == 9
    assert len(opt_result.top_10_scenarios) == 9

    # 2. Test optimize with empty data (should fail)
    res_empty_data = ParametricOptimizerEngine.optimize(
        data=np.empty((0, 5)),
        param_space=param_space,
        backtest_func=dummy_backtest,
        min_trades=30,
        n_workers=1
    )
    assert isinstance(res_empty_data, Result)
    assert res_empty_data.is_failure
    assert "Data array is empty" in res_empty_data.reason

    # 3. Test optimize with empty parameter space (should fail)
    res_empty_space = ParametricOptimizerEngine.optimize(
        data=data,
        param_space=ParameterSpace(parameters={}),
        backtest_func=dummy_backtest,
        min_trades=30,
        n_workers=1
    )
    assert isinstance(res_empty_space, Result)
    assert res_empty_space.is_failure
    assert "Invalid parameter space" in res_empty_space.reason
