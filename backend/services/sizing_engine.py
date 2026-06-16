from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from backend.config.funding_thresholds import FundingThresholds


class MultiFactorInputs(BaseModel):
    """Factors influencing position sizing."""

    model_config = ConfigDict(frozen=True)
    f_vol: Decimal = Decimal("1.0")
    f_dd: Decimal = Decimal("1.0")
    f_signal: Decimal = Decimal("1.0")
    f_regime: Decimal = Decimal("1.0")
    f_conviction: Decimal = Decimal("1.0")


class SizingRequest(BaseModel):
    """Inputs to compute final position sizing."""

    model_config = ConfigDict(frozen=True)
    kelly_base: Decimal
    global_factor: Decimal
    multi_factors: MultiFactorInputs
    survival_recommended_risk_pct: Decimal
    remaining_daily_risk_pct: Decimal
    remaining_max_risk_pct: Decimal
    equity: Decimal
    stop_distance_pct: Decimal


class SizingDecision(BaseModel):
    """Final output of the Multi-Factor Sizing Engine."""

    model_config = ConfigDict(frozen=True)
    allowed_risk_pct: Decimal
    position_notional: Decimal
    base_risk_pct: Decimal
    capped_by: str


class SizingEngine:
    """Multi-factor sizing engine taking Kelly, global context, and survival rules."""

    def __init__(self, thresholds: FundingThresholds | None = None) -> None:
        self.thresholds = thresholds or FundingThresholds()

    def compute_position_size(self, request: SizingRequest) -> SizingDecision:
        """Compute the final allowed risk and position notional."""
        if request.stop_distance_pct <= Decimal("0.0"):
            return SizingDecision(
                allowed_risk_pct=Decimal("0.0"),
                position_notional=Decimal("0.0"),
                base_risk_pct=Decimal("0.0"),
                capped_by="invalid_stop",
            )

        f_total = (
            request.multi_factors.f_vol
            * request.multi_factors.f_dd
            * request.multi_factors.f_signal
            * request.multi_factors.f_regime
            * request.multi_factors.f_conviction
            * request.global_factor
        )

        base_risk = request.kelly_base * f_total

        cap_ftmo = Decimal(str(self.thresholds.ftmo_base_risk_per_trade_pct))

        candidates = {
            "kelly_multi_factor": base_risk,
            "survival": request.survival_recommended_risk_pct,
            "remaining_daily": request.remaining_daily_risk_pct,
            "remaining_max": request.remaining_max_risk_pct,
            "ftmo_hardcap": cap_ftmo,
        }

        # Find the minimum cap
        capped_by = min(candidates, key=candidates.get)  # type: ignore[arg-type]
        allowed_risk_pct = candidates[capped_by]

        if allowed_risk_pct <= Decimal("0.0"):
            return SizingDecision(
                allowed_risk_pct=Decimal("0.0"),
                position_notional=Decimal("0.0"),
                base_risk_pct=base_risk,
                capped_by=capped_by,
            )

        # Formula: Equity * (Risk_PCT / Stop_PCT)
        notional = request.equity * (allowed_risk_pct / request.stop_distance_pct)

        return SizingDecision(
            allowed_risk_pct=allowed_risk_pct,
            position_notional=notional,
            base_risk_pct=base_risk,
            capped_by=capped_by,
        )
