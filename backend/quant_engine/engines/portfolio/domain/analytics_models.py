from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PerformancePeriod(str, Enum):
    """Canonical performance measurement periods."""

    MTD = "MTD"
    QTD = "QTD"
    YTD = "YTD"
    M1 = "1M"
    M3 = "3M"
    M6 = "6M"
    Y1 = "1Y"
    Y3 = "3Y"
    Y5 = "5Y"
    ITD = "ITD"


class AttributionEffect(str, Enum):
    """Brinson-Fachler effect labels."""

    ALLOCATION = "allocation"
    SELECTION = "selection"
    INTERACTION = "interaction"
    TOTAL = "total"


class PerformanceMetrics(BaseModel):
    """Institutional portfolio performance metrics."""

    model_config = ConfigDict(frozen=True)

    twr: float
    mwr: float | None = None
    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float | None = None
    max_drawdown: float
    max_drawdown_duration: int | None = None
    current_drawdown: float = 0.0
    win_rate: float | None = None
    profit_factor: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    period: PerformancePeriod
    start_date: date
    end_date: date
    trading_days: int
    benchmark_return: float | None = None
    alpha: float | None = None
    beta: float | None = None
    tracking_error: float | None = None
    information_ratio: float | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> PerformanceMetrics:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        return self


class AttributionResult(BaseModel):
    """Single-asset simplified attribution contract."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    trade_return: float
    sector_return: float
    market_return: float
    selection_alpha: float
    allocation_beta: float
    total_active_return: float
    is_skillful: bool
    sector: str = "Unknown"
    period_days: int = 0

    @model_validator(mode="after")
    def validate_total(self) -> AttributionResult:
        expected = round(self.selection_alpha + self.allocation_beta, 6)
        if abs(self.total_active_return - expected) > 1e-4:
            raise ValueError("total_active_return must equal selection_alpha + allocation_beta")
        return self


class CorrelationMatrix(BaseModel):
    """Correlation matrix and per-ticker summary stats."""

    model_config = ConfigDict(frozen=True)

    tickers: list[str]
    matrix: dict[str, dict[str, float]]
    returns: dict[str, float]
    volatilities: dict[str, float]
    period: str
    trading_days: int
    calculated_at: datetime
    error: str | None = None


class DrawdownAnalysis(BaseModel):
    """Complete drawdown analysis for a portfolio or asset."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    current_drawdown: float
    current_drawdown_start: date | None = None
    current_drawdown_days: int = 0
    max_drawdown: float
    max_drawdown_start: date | None = None
    max_drawdown_end: date | None = None
    max_drawdown_duration: int = 0
    recovery_date: date | None = None
    avg_drawdown: float = 0.0
    drawdown_count: int = 0
    avg_recovery_days: float = 0.0
    drawdowns_over_5pct: int = 0
    drawdowns_over_10pct: int = 0
    drawdowns_over_20pct: int = 0
    error: str | None = None


class ActiveReturnDecomposition(BaseModel):
    """MSCI MAC Active Base Return internal tree."""

    model_config = ConfigDict(frozen=True)

    model_base_return: float = 0.0
    trading_impact: float = 0.0
    pricing_impact: float = 0.0
    look_through_impact: float = 0.0
    benchmark_residual: float = 0.0
    active_base_return: float = 0.0


class CurrencyAttribution(BaseModel):
    """MSCI MAC Currency effect decomposition."""

    model_config = ConfigDict(frozen=True)

    currency_excess: float = 0.0
    cross_product: float = 0.0
    total_currency: float = 0.0


class FactorContribution(BaseModel):
    """MSCI MAC Common Factor vs Specific Return decomposition."""

    model_config = ConfigDict(frozen=True)

    common_factors: dict[str, float] = {}
    specific_return: float = 0.0
    total_local_excess: float = 0.0


class MACAttributionSummary(BaseModel):
    """Consolidated MSCI MAC Performance Attribution result."""

    model_config = ConfigDict(frozen=True)

    active_return: ActiveReturnDecomposition
    currency_return: CurrencyAttribution
    local_excess: FactorContribution
    total_active_return: float = 0.0
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TradeDirection(str, Enum):
    """Trade direction labels."""

    LONG = "long"
    SHORT = "short"


class TradePerformanceTrade(BaseModel):
    """Open/Closed trade payload for analytics."""

    model_config = ConfigDict(frozen=True)

    trade_id: int | str
    symbol: str
    side: TradeDirection
    return_pct: float
    pnl_fiat: float
    duration_minutes: float


class TradePerformanceReport(BaseModel):
    """Final KPI report for trades."""

    model_config = ConfigDict(frozen=True)

    is_statistically_significant: bool = False
    n_trades: int = 0
    net_pnl_fiat: float = float("nan")
    net_return_pct: float = float("nan")
    win_rate: float = float("nan")
    profit_factor: float = float("nan")
    expectancy_pct: float = float("nan")
    sharpe_ratio: float = float("nan")
    sortino_ratio: float = float("nan")
    calmar_ratio: float = float("nan")
    max_drawdown_pct: float = float("nan")
    long_win_rate: float = float("nan")
    short_win_rate: float = float("nan")
    avg_win_pct: float = float("nan")
    avg_loss_pct: float = float("nan")
    avg_duration_minutes: float = float("nan")
    extra: dict[str, str | float | int | bool] = Field(default_factory=dict)


class PortfolioAttributionReport(BaseModel):
    """Contract for BHB and Frongello portfolio attribution."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    sector_level: pd.DataFrame
    period_level: pd.DataFrame
    is_valid_attribution: bool
    validation_notes: list[str] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: PORTAFOLIO
# Archivo         : analytics_models.py
# Sub-capa        : Domain
# Solver/Optimizer: N/A
# Eliminado       : Referencias legacy de QuantumBeta V1.
# Preservado      : PerformanceMetrics, Attribution (Brinson/MAC), DrawdownAnalysis, TradePerformance.
# Pendientes      : Integración con Engine de Atribución (Phase 2).
# ────────────────────────────────────────────────────────────────────
