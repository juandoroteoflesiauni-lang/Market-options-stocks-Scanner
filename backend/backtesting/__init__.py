"""Specialized backtesting engines (walk-forward friendly, no IO in core loops)."""

from backend.backtesting.base import (
    BacktestConfig,
    BacktestResult,
    SimpleEquityCurve,
    WalkForwardFoldResult,
    WalkForwardSummary,
    run_long_only_threshold_backtest,
    run_walk_forward_threshold_grid,
)
from backend.backtesting.strategy_trainer import StrategyTrainer

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "SimpleEquityCurve",
    "StrategyTrainer",
    "WalkForwardFoldResult",
    "WalkForwardSummary",
    "run_long_only_threshold_backtest",
    "run_walk_forward_threshold_grid",
]
