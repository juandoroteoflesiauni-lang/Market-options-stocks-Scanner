from __future__ import annotations
"""Fundamental factor backtester (placeholder using composite quality score)."""


from collections.abc import Sequence

from backend.backtesting.base import (
    BacktestConfig,
    BacktestResult,
    run_long_only_threshold_backtest,
)


def run_fundamentals_backtest(
    returns_pct: Sequence[float],
    quality_score: Sequence[float],
    *,
    symbol: str,
    threshold: float = 55.0,
    cost_config: BacktestConfig | None = None,
) -> BacktestResult:
    return run_long_only_threshold_backtest(
        returns_pct,
        quality_score,
        symbol=symbol,
        module="fundamentals",
        threshold=threshold,
        cost_config=cost_config,
    )
