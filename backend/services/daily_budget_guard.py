from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.portfolio_risk_models import AccountState


class BudgetDecision(BaseModel):
    """Result of the daily budget evaluation."""

    model_config = ConfigDict(frozen=True)
    is_allowed: bool
    remaining_daily_risk_pct: Decimal
    reason: str


class DailyBudgetGuard:
    """Ensures that daily loss limits are respected."""

    def __init__(self, thresholds: FundingThresholds | None = None) -> None:
        self.thresholds = thresholds or FundingThresholds()

    def evaluate(self, account: AccountState, intraday_pnl: float) -> BudgetDecision:
        """Check intraday PnL against funding daily limits."""
        limit_pct = Decimal(str(self.thresholds.ftmo_daily_loss_limit_pct))
        limit_amt = Decimal(str(account.initial_capital)) * (limit_pct / Decimal("100.0"))

        # If intraday_pnl is negative, we used up budget
        used_amt = max(Decimal("0.0"), Decimal(str(-intraday_pnl)))
        remaining_amt = max(Decimal("0.0"), limit_amt - used_amt)

        remaining_pct = (remaining_amt / Decimal(str(account.initial_capital))) * Decimal("100.0")

        if remaining_amt <= Decimal("0.0"):
            return BudgetDecision(
                is_allowed=False,
                remaining_daily_risk_pct=Decimal("0.0"),
                reason="Daily loss limit breached",
            )

        return BudgetDecision(
            is_allowed=True,
            remaining_daily_risk_pct=remaining_pct,
            reason="Within daily budget",
        )
