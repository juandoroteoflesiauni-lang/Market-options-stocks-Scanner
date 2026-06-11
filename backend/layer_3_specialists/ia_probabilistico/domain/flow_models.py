"""
backend/layer_3_specialists/ia_probabilistico/domain/flow_models.py
════════════════════════════════════════════════════════════════════════════════
Domain models for Backtesting and Execution Flow.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BacktestResult(BaseModel):
    """Result envelope for a single backtest trial."""

    model_config = ConfigDict(frozen=True)

    ok: bool = True
    error: str | None = None

    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    total_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    trades_count: int | None = None
