from decimal import Decimal

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.portfolio_risk_models import AccountState


class TrailingMLLSimulator:
    """Simulates maximum loss limit (Topstep/MFF trailing or FTMO static style)."""

    def __init__(self, thresholds: FundingThresholds | None = None) -> None:
        self.thresholds = thresholds or FundingThresholds()

    def get_remaining_max_risk_pct(self, account: AccountState) -> Decimal:
        """Compute remaining max risk percent based on high watermark or static."""
        limit_pct = Decimal(str(self.thresholds.ftmo_max_loss_limit_pct))
        max_amount = Decimal(str(account.initial_capital)) * (limit_pct / Decimal("100.0"))

        eq = Decimal(str(account.current_equity))

        # Assuming static limit (FTMO rules)
        limit_equity = Decimal(str(account.initial_capital)) - max_amount

        remaining_amt = max(Decimal("0.0"), eq - limit_equity)
        remaining_pct = (remaining_amt / Decimal(str(account.initial_capital))) * Decimal("100.0")
        return remaining_pct
