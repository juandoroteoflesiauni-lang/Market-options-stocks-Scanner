from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class RiskMetricsSnapshot(BaseModel):
    """
    Snapshot of aggregate performance and risk metrics.
    Frozen for immutability.
    """

    model_config = ConfigDict(frozen=True)

    sample_size: int

    # Core expectancy
    expectancy_r: Decimal
    expectancy_by_setup: dict[str, Decimal]
    profit_factor: float

    # Risk-adjusted returns
    sharpe: float
    sortino: float
    calmar: float

    # Drawdown and tail risk
    bur: float
    buffer_zone: str
    ulcer: float
    var95: Decimal
    cvar95: Decimal
    cvar99: Decimal

    # Probabilistic and sizing
    kelly_applied: float
    risk_of_ruin_pct: float
