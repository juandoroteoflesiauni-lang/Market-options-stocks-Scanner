from __future__ import annotations
"""Technical module backtester (signal vs forward returns)."""


from collections.abc import Sequence

from backend.backtesting.base import (
    BacktestConfig,
    BacktestResult,
    run_long_only_threshold_backtest,
)


def run_technical_backtest(
    returns_pct: Sequence[float],
    momentum_signal: Sequence[float],
    *,
    symbol: str,
    threshold: float = 0.0,
    cost_config: BacktestConfig | None = None,
) -> BacktestResult:
    return run_long_only_threshold_backtest(
        returns_pct,
        momentum_signal,
        symbol=symbol,
        module="technical",
        threshold=threshold,
        cost_config=cost_config,
    )
