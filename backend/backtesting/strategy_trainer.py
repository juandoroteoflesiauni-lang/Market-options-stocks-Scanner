from __future__ import annotations
"""Train / calibrate per-module weights from simple grid search on toy signals."""


from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.backtesting.base import (
    BacktestConfig,
    BacktestResult,
    WalkForwardSummary,
    run_long_only_threshold_backtest,
    run_walk_forward_threshold_grid,
)
from backend.backtesting.fundamentals_backtester import run_fundamentals_backtest
from backend.backtesting.gex_backtester import run_gex_backtest
from backend.backtesting.predictive_backtester import run_predictive_backtest
from backend.backtesting.technical_backtester import run_technical_backtest


@dataclass
class TrainerConfig:
    symbol: str


class StrategyTrainer:
    """Minimal trainer: runs module engines and returns best threshold by Sharpe."""

    def __init__(self: StrategyTrainer, config: TrainerConfig) -> None:
        self._config = config

    def run_module(
        self: StrategyTrainer,
        module: str,
        returns_pct: Sequence[float],
        signal: Sequence[float],
        *,
        cost_config: BacktestConfig | None = None,
    ) -> BacktestResult:
        if module == "technical":
            return run_technical_backtest(
                returns_pct, signal, symbol=self._config.symbol, cost_config=cost_config
            )
        if module == "options_gex":
            return run_gex_backtest(
                returns_pct, signal, symbol=self._config.symbol, cost_config=cost_config
            )
        if module == "predictive":
            return run_predictive_backtest(
                returns_pct, signal, symbol=self._config.symbol, cost_config=cost_config
            )
        if module == "fundamentals":
            return run_fundamentals_backtest(
                returns_pct, signal, symbol=self._config.symbol, cost_config=cost_config
            )
        raise ValueError(f"unknown module {module}")

    def grid_best(
        self: StrategyTrainer,
        returns_pct: Sequence[float],
        signal: Sequence[float],
        *,
        module: str,
        thresholds: Sequence[float],
        scorer: Callable[[BacktestResult], float],
        cost_config: BacktestConfig | None = None,
    ) -> tuple[float, BacktestResult]:
        best_t = thresholds[0]
        best_res = run_long_only_threshold_backtest(
            returns_pct,
            signal,
            symbol=self._config.symbol,
            module=module,
            threshold=best_t,
            cost_config=cost_config,
        )
        best_score = scorer(best_res)
        for t in thresholds[1:]:
            res = run_long_only_threshold_backtest(
                returns_pct,
                signal,
                symbol=self._config.symbol,
                module=module,
                threshold=t,
                cost_config=cost_config,
            )
            s = scorer(res)
            if s > best_score:
                best_score = s
                best_t = t
                best_res = res
        return best_t, best_res

    def walk_forward_thresholds(
        self: StrategyTrainer,
        returns_pct: Sequence[float],
        signal: Sequence[float],
        *,
        module: str,
        train_window: int,
        test_window: int,
        step: int,
        thresholds: Sequence[float],
        cost_config: BacktestConfig | None = None,
    ) -> WalkForwardSummary:
        return run_walk_forward_threshold_grid(
            returns_pct,
            signal,
            symbol=self._config.symbol,
            module=module,
            train_window=train_window,
            test_window=test_window,
            step=step,
            thresholds=thresholds,
            cost_config=cost_config,
        )
