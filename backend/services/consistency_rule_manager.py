from decimal import Decimal
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from backend.config.funding_thresholds import FundingThresholds
from backend.models.trade_record import TradeRecord


class ConsistencyDecision(BaseModel):
    """Result of the consistency rule evaluation."""

    model_config = ConfigDict(frozen=True)
    is_allowed: bool
    best_day_profit: Decimal
    total_profit: Decimal
    best_day_ratio: Decimal
    reason: str


class ConsistencyRuleManager:
    """Ensures compliance with prop firm consistency rules (e.g., 50% cap)."""

    def __init__(self, thresholds: FundingThresholds | None = None) -> None:
        self.thresholds = thresholds or FundingThresholds()

    def evaluate(self, trades: Sequence[TradeRecord]) -> ConsistencyDecision:
        """Check if any single day exceeds the max allowed percentage of total profit."""
        daily_pnl: dict[str, Decimal] = {}
        for t in trades:
            if t.pnl is not None:
                date_key = (
                    t.closed_at.strftime("%Y-%m-%d")
                    if t.closed_at
                    else t.opened_at.strftime("%Y-%m-%d")
                )
                daily_pnl[date_key] = daily_pnl.get(date_key, Decimal("0.0")) + Decimal(str(t.pnl))

        positives = [pnl for pnl in daily_pnl.values() if pnl > Decimal("0.0")]
        total_profit = sum(positives) if positives else Decimal("0.0")
        best_day = max(positives) if positives else Decimal("0.0")

        ratio = best_day / total_profit if total_profit > Decimal("0.0") else Decimal("0.0")
        cap = Decimal("0.50")  # 50% cap standard rule

        if total_profit > Decimal("0.0") and ratio >= cap:
            return ConsistencyDecision(
                is_allowed=False,
                best_day_profit=best_day,
                total_profit=total_profit,
                best_day_ratio=ratio,
                reason=f"Consistency cap breached: {ratio * 100:.1f}% >= {cap * 100:.1f}%",
            )

        return ConsistencyDecision(
            is_allowed=True,
            best_day_profit=best_day,
            total_profit=total_profit,
            best_day_ratio=ratio,
            reason="Consistency rules passed",
        )
