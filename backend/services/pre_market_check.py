from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


class PreMarketDecision(BaseModel):
    """Result of pre-market validation checks."""

    model_config = ConfigDict(frozen=True)
    is_allowed: bool
    reason: str


class PreMarketCheck:
    """Validates pre-market conditions (trading hours, holidays, weekends)."""

    def evaluate(self, check_time: datetime | None = None) -> PreMarketDecision:
        """Evaluate if the current time is a valid trading period."""
        now = check_time or datetime.now(timezone.utc)
        
        # Simplified logic for Sprint 1: block weekends
        if now.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
            return PreMarketDecision(
                is_allowed=False, 
                reason="Weekend trading blocked"
            )

        return PreMarketDecision(
            is_allowed=True, 
            reason="Pre-market checks passed"
        )
