"""Builder payout eligibility engine (buffer, consistency, qualified days, cap)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.builder_models import (
    BUILDER_BUFFER_NOT_REACHED,
    BUILDER_PAYOUT_CAP_REACHED,
    BUILDER_PAYOUT_CONSISTENCY_RISK,
    BUILDER_QUALIFYING_DAYS_MISSING,
    BuilderAccountState,
    BuilderConsistencyGuidance,
    BuilderDailyPnl,
    BuilderPayoutCycleRecord,
    BuilderPayoutPlan,
    BuilderPhase,
    BuilderProfile,
    PayoutEvaluation,
)
from backend.services.builder_state_machine import effective_equity

_SIM_PHASES: frozenset[BuilderPhase] = frozenset(
    {
        "SIM_ACTIVE",
        "SIM_BUFFER_BUILDING",
        "SIM_PAYOUT_ELIGIBLE",
    }
)


class BuilderPayoutEngine:
    """Evaluate sim-funded payout readiness for the Builder plan."""

    def __init__(self, thresholds: FundingThresholds | None = None) -> None:
        self._thresholds = thresholds or FundingThresholds()

    def evaluate(
        self,
        state: BuilderAccountState,
        profile: BuilderProfile,
        cycle: BuilderPayoutCycleRecord,
        daily_pnls: Sequence[BuilderDailyPnl] | None = None,
    ) -> PayoutEvaluation:
        """Compute payout eligibility and suggested sim phase."""
        net_profit = _net_cycle_profit(state)
        buffer_progress = max(Decimal("0"), net_profit)
        buffer_target = cycle.buffer_target or profile.payout_buffer
        buffer_pct = _progress_pct(buffer_progress, buffer_target)
        consistency_ratio = _consistency_ratio_live(daily_pnls or ())
        qualified_days = _qualified_days_count(daily_pnls or ())
        reason_codes = _eligibility_reasons(
            profile=profile,
            net_profit=net_profit,
            buffer_progress=buffer_progress,
            buffer_target=buffer_target,
            consistency_ratio=consistency_ratio,
            qualified_days=qualified_days,
            min_qualified_days=self._thresholds.builder_min_qualified_days_payout,
        )
        eligible = len(reason_codes) == 0
        withdrawable = _withdrawable_amount(
            profile=profile,
            net_profit=net_profit,
            buffer_target=buffer_target,
            eligible=eligible,
        )
        if eligible and withdrawable <= Decimal("0"):
            eligible = False
            reason_codes = _dedupe((*reason_codes, BUILDER_PAYOUT_CAP_REACHED))
        suggested_phase = _suggest_phase(
            state.phase,
            eligible=eligible,
            buffer_pct=buffer_pct,
        )
        return PayoutEvaluation(
            eligible=eligible,
            withdrawable_amount=withdrawable,
            buffer_progress_pct=buffer_pct,
            consistency_ratio_live=consistency_ratio,
            qualified_days_count=qualified_days,
            suggested_phase=suggested_phase,
            reason_codes=tuple(reason_codes),
        )

    def consistency_guidance(
        self,
        profile: BuilderProfile,
        daily_pnls: Sequence[BuilderDailyPnl] | None = None,
    ) -> BuilderConsistencyGuidance:
        """Compute the max profit today that keeps the best day under the cap.

        The MFFU consistency rule invalidates a payout when a single day exceeds
        ``consistency_cap`` (50%) of the total profit. Given prior positive days,
        the additional profit allowed today before today becomes the disqualifying
        "hero day" is ``cap / (1 - cap) * prior_positive_profit``.
        """
        entries = tuple(daily_pnls or ())
        positives = [entry.pnl for entry in entries if entry.pnl > Decimal("0")]
        total_profit = sum(positives, start=Decimal("0"))
        best_day = max(positives) if positives else Decimal("0")
        prior_positive = total_profit
        cap = profile.consistency_cap
        if cap >= Decimal("1") or cap <= Decimal("0") or prior_positive <= Decimal("0"):
            max_today = Decimal("0")
        else:
            max_today = (cap / (Decimal("1") - cap) * prior_positive).quantize(Decimal("0.01"))
        ratio = (
            (best_day / total_profit).quantize(Decimal("0.0001"))
            if total_profit > Decimal("0")
            else Decimal("0")
        )
        return BuilderConsistencyGuidance(
            consistency_cap=cap,
            consistency_ratio_live=ratio,
            total_profit=total_profit,
            best_day_profit=best_day,
            prior_positive_profit=prior_positive,
            max_profit_today_usd=max_today,
            is_consistency_at_risk=ratio >= cap * Decimal("0.70"),
            needs_more_days=len(positives) < 2,
        )

    def payout_plan(
        self,
        state: BuilderAccountState,
        profile: BuilderProfile,
        cycle: BuilderPayoutCycleRecord,
        daily_pnls: Sequence[BuilderDailyPnl] | None = None,
    ) -> BuilderPayoutPlan:
        """Project remaining buffer, qualified days and an ETA to first payout."""
        payout = self.evaluate(state, profile, cycle, daily_pnls)
        entries = tuple(daily_pnls or ())
        positives = [entry.pnl for entry in entries if entry.pnl > Decimal("0")]
        buffer_target = cycle.buffer_target or profile.payout_buffer
        net_profit = _net_cycle_profit(state)
        buffer_remaining = max(Decimal("0"), buffer_target - net_profit)
        required_days = self._thresholds.builder_min_qualified_days_payout
        days_remaining = max(0, required_days - payout.qualified_days_count)
        avg_daily = (
            (sum(positives, start=Decimal("0")) / Decimal(len(positives))).quantize(
                Decimal("0.01")
            )
            if positives
            else Decimal("0")
        )
        eta_buffer_days = (
            int((buffer_remaining / avg_daily).to_integral_value(rounding="ROUND_CEILING"))
            if avg_daily > Decimal("0") and buffer_remaining > Decimal("0")
            else 0
        )
        eta = max(days_remaining, eta_buffer_days)
        estimated = None if payout.eligible else (eta if eta > 0 else None)
        return BuilderPayoutPlan(
            buffer_target=buffer_target,
            buffer_progress=net_profit,
            buffer_remaining=buffer_remaining,
            qualified_days_count=payout.qualified_days_count,
            qualified_days_required=required_days,
            qualified_days_remaining=days_remaining,
            min_profit_payout=profile.min_profit_payout,
            projected_withdrawable=payout.withdrawable_amount,
            avg_daily_profit=avg_daily,
            estimated_days_to_payout=estimated,
            is_eligible=payout.eligible,
            blocking_reason_codes=payout.reason_codes,
        )

    def sync_cycle(
        self,
        state: BuilderAccountState,
        cycle: BuilderPayoutCycleRecord,
        daily_pnls: Sequence[BuilderDailyPnl] | None = None,
    ) -> BuilderPayoutCycleRecord:
        """Refresh cycle progress fields from the live account snapshot."""
        net_profit = _net_cycle_profit(state)
        return cycle.model_copy(
            update={
                "buffer_progress": max(Decimal("0"), net_profit),
                "qualified_days_count": _qualified_days_count(daily_pnls or ()),
            }
        )


def _net_cycle_profit(state: BuilderAccountState) -> Decimal:
    return max(Decimal("0"), effective_equity(state) - state.initial_capital)


def _progress_pct(progress: Decimal, target: Decimal) -> Decimal:
    if target <= Decimal("0"):
        return Decimal("0")
    return (progress / target * Decimal("100")).quantize(Decimal("0.01"))


def _consistency_ratio_live(daily_pnls: Sequence[BuilderDailyPnl]) -> Decimal:
    positives = [entry.pnl for entry in daily_pnls if entry.pnl > Decimal("0")]
    if not positives:
        return Decimal("0")
    total_profit = sum(positives, start=Decimal("0"))
    best_day = max(positives)
    return (best_day / total_profit).quantize(Decimal("0.0001"))


def _qualified_days_count(daily_pnls: Sequence[BuilderDailyPnl]) -> int:
    return sum(1 for entry in daily_pnls if entry.pnl > Decimal("0"))


def _eligibility_reasons(
    *,
    profile: BuilderProfile,
    net_profit: Decimal,
    buffer_progress: Decimal,
    buffer_target: Decimal,
    consistency_ratio: Decimal,
    qualified_days: int,
    min_qualified_days: int,
) -> list[str]:
    reasons: list[str] = []
    if net_profit < profile.min_profit_payout:
        reasons.append(BUILDER_BUFFER_NOT_REACHED)
    if buffer_progress < buffer_target:
        reasons.append(BUILDER_BUFFER_NOT_REACHED)
    if qualified_days < min_qualified_days:
        reasons.append(BUILDER_QUALIFYING_DAYS_MISSING)
    if consistency_ratio > profile.consistency_cap:
        reasons.append(BUILDER_PAYOUT_CONSISTENCY_RISK)
    return reasons


def _withdrawable_amount(
    *,
    profile: BuilderProfile,
    net_profit: Decimal,
    buffer_target: Decimal,
    eligible: bool,
) -> Decimal:
    if not eligible:
        return Decimal("0")
    excess = net_profit - buffer_target
    if excess <= Decimal("0"):
        return Decimal("0")
    return min(profile.payout_cap, excess).quantize(Decimal("0.01"))


def _suggest_phase(
    current_phase: BuilderPhase,
    *,
    eligible: bool,
    buffer_pct: Decimal,
) -> BuilderPhase | None:
    if current_phase not in _SIM_PHASES:
        return None
    if eligible:
        return "SIM_PAYOUT_ELIGIBLE"
    if buffer_pct < Decimal("100"):
        return "SIM_BUFFER_BUILDING"
    return "SIM_ACTIVE"


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
