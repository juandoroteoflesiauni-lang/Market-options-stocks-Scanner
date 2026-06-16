from __future__ import annotations
"""Predictive / probabilistic module backtester."""


from collections.abc import Sequence

from backend.backtesting.base import (
    BacktestConfig,
    BacktestResult,
    run_long_only_threshold_backtest,
)


def run_predictive_backtest(
    returns_pct: Sequence[float],
    regime_score: Sequence[float],
    *,
    symbol: str,
    threshold: float = 50.0,
    cost_config: BacktestConfig | None = None,
) -> BacktestResult:
    return run_long_only_threshold_backtest(
        returns_pct,
        regime_score,
        symbol=symbol,
        module="predictive",
        threshold=threshold,
        cost_config=cost_config,
    )
