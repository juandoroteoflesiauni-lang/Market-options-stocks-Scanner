from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from backend.models.global_context_snapshot import GlobalContextSnapshot


class ConvergenceDecision(BaseModel):
    """Result of convergence validation."""

    model_config = ConfigDict(frozen=True)
    is_allowed: bool
    conviction_multiplier: Decimal
    reason: str


class ConvergenceGate:
    """Validates coherence between trade signals and global context."""

    def evaluate(self, direction: str, context: GlobalContextSnapshot) -> ConvergenceDecision:
        """Evaluate signal direction against market context."""
        if not context.is_valid:
            return ConvergenceDecision(
                is_allowed=True,
                conviction_multiplier=Decimal("1.0"),
                reason="Context invalid, neutral impact",
            )

        if context.market_regime == "MELTDOWN":
            if direction.upper() == "LONG":
                return ConvergenceDecision(
                    is_allowed=False,
                    conviction_multiplier=Decimal("0.0"),
                    reason="Long blocked in MELTDOWN",
                )
            else:
                return ConvergenceDecision(
                    is_allowed=True,
                    conviction_multiplier=Decimal("1.0"),
                    reason="Short allowed in MELTDOWN",
                )

        if context.market_regime == "BEAR" and direction.upper() == "LONG":
            return ConvergenceDecision(
                is_allowed=True,
                conviction_multiplier=Decimal("0.5"),
                reason="Long size reduced in BEAR regime",
            )

        if context.market_regime == "BULL" and direction.upper() == "SHORT":
            return ConvergenceDecision(
                is_allowed=True,
                conviction_multiplier=Decimal("0.5"),
                reason="Short size reduced in BULL regime",
            )

        return ConvergenceDecision(
            is_allowed=True,
            conviction_multiplier=Decimal("1.0"),
            reason="Signal aligned with context",
        )
