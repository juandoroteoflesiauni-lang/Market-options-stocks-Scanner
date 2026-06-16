"""Builder-native survival scoring and objective function metrics."""

from __future__ import annotations

from decimal import Decimal

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.builder_models import (
    BUILDER_PAYOUT_CONSISTENCY_RISK,
    BUILDER_TRAILING_DD_CRITICAL,
    BuilderAccountState,
    BuilderProfile,
    BuilderRuleEvaluation,
    BuilderSurvivalSnapshot,
    BuilderSurvivalStatus,
    PayoutEvaluation,
)
from backend.services.builder_state_machine import eval_profit


class BuilderSurvivalEngine:
    """Compute Builder survival score with trailing DD as the dominant input."""

    def __init__(self, thresholds: FundingThresholds | None = None) -> None:
        self._thresholds = thresholds or FundingThresholds()

    def evaluate(
        self,
        state: BuilderAccountState,
        profile: BuilderProfile,
        rule_eval: BuilderRuleEvaluation,
        payout_eval: PayoutEvaluation | None = None,
    ) -> BuilderSurvivalSnapshot:
        """Return a deterministic Builder survival snapshot."""
        reason_codes = _merge_reason_codes(rule_eval, payout_eval)
        eval_progress = _eval_progress_pct(state, profile)
        buffer_progress = payout_eval.buffer_progress_pct if payout_eval else Decimal("0")
        payout_state = _payout_state_label(state, payout_eval)

        if rule_eval.is_breached:
            return BuilderSurvivalSnapshot(
                status="WOULD_BREACH",
                score=Decimal("0"),
                recommended_risk_pct=Decimal("0"),
                eval_progress_pct=eval_progress,
                buffer_progress_pct=buffer_progress,
                distance_to_trailing_dd=rule_eval.distance_to_trailing_dd,
                distance_to_dll_soft_pause=rule_eval.distance_to_dll_soft_pause,
                payout_eligibility_state=payout_state,
                reason_codes=reason_codes,
            )

        components = _score_components(
            profile=profile,
            rule_eval=rule_eval,
            payout_eval=payout_eval,
            eval_progress=eval_progress,
            buffer_progress=buffer_progress,
        )
        score = _weighted_score(components)
        status = _status_from_rules(rule_eval, score)
        score = _cap_score_for_status(status, score)
        recommended = _recommended_risk_pct(status, profile, score)

        return BuilderSurvivalSnapshot(
            status=status,
            score=score,
            recommended_risk_pct=recommended,
            eval_progress_pct=eval_progress,
            buffer_progress_pct=buffer_progress,
            distance_to_trailing_dd=rule_eval.distance_to_trailing_dd,
            distance_to_dll_soft_pause=rule_eval.distance_to_dll_soft_pause,
            payout_eligibility_state=payout_state,
            reason_codes=reason_codes,
            score_components=components,
        )


def _score_components(
    *,
    profile: BuilderProfile,
    rule_eval: BuilderRuleEvaluation,
    payout_eval: PayoutEvaluation | None,
    eval_progress: Decimal,
    buffer_progress: Decimal,
) -> dict[str, Decimal]:
    trailing_runway = _runway(rule_eval.distance_to_trailing_dd, profile.max_loss)
    dll_runway = _runway(rule_eval.distance_to_dll_soft_pause, profile.daily_loss_limit)
    eval_component = min(Decimal("1"), eval_progress / Decimal("100"))
    buffer_component = min(Decimal("1"), buffer_progress / Decimal("100"))
    consistency_component = Decimal("1")
    if payout_eval and payout_eval.consistency_ratio_live > profile.consistency_cap:
        consistency_component = Decimal("0.25")
    elif payout_eval and payout_eval.consistency_ratio_live > profile.consistency_cap * Decimal(
        "0.70"
    ):
        consistency_component = Decimal("0.60")
    return {
        "trailing_dd_runway": trailing_runway,
        "dll_runway": dll_runway,
        "eval_progress": eval_component,
        "buffer_progress": buffer_component,
        "consistency_runway": consistency_component,
    }


def _weighted_score(components: dict[str, Decimal]) -> Decimal:
    score = (
        components["trailing_dd_runway"] * Decimal("0.40")
        + components["dll_runway"] * Decimal("0.25")
        + components["eval_progress"] * Decimal("0.15")
        + components["buffer_progress"] * Decimal("0.10")
        + components["consistency_runway"] * Decimal("0.10")
    ) * Decimal("100")
    return score.quantize(Decimal("0.01"))


def _status_from_rules(
    rule_eval: BuilderRuleEvaluation,
    score: Decimal,
) -> BuilderSurvivalStatus:
    if BUILDER_TRAILING_DD_CRITICAL in rule_eval.reason_codes and (
        rule_eval.distance_to_trailing_dd <= Decimal("0")
        or rule_eval.is_breached
    ):
        return "WOULD_BREACH"
    if rule_eval.blocks_new_entries:
        return "AT_RISK"
    if score >= Decimal("70"):
        return "SAFE"
    if score >= Decimal("50"):
        return "MONITOR"
    return "AT_RISK"


def _cap_score_for_status(status: BuilderSurvivalStatus, score: Decimal) -> Decimal:
    if status == "WOULD_BREACH":
        return Decimal("0")
    if status == "AT_RISK":
        return min(score, Decimal("49"))
    if status == "MONITOR":
        return min(score, Decimal("65"))
    return score


def _recommended_risk_pct(
    status: BuilderSurvivalStatus,
    profile: BuilderProfile,
    score: Decimal,
) -> Decimal:
    base = profile.base_risk_per_trade_pct
    if status == "WOULD_BREACH":
        return Decimal("0")
    if status == "AT_RISK":
        return (base * Decimal("0.25")).quantize(Decimal("0.0001"))
    if status == "MONITOR":
        return (base * Decimal("0.50")).quantize(Decimal("0.0001"))
    factor = max(Decimal("0.25"), score / Decimal("100"))
    return (base * factor).quantize(Decimal("0.0001"))


def _eval_progress_pct(state: BuilderAccountState, profile: BuilderProfile) -> Decimal:
    if profile.profit_target <= Decimal("0"):
        return Decimal("0")
    progress = eval_profit(state) / profile.profit_target * Decimal("100")
    return max(Decimal("0"), progress).quantize(Decimal("0.01"))


def _runway(distance: Decimal, limit: Decimal) -> Decimal:
    if limit <= Decimal("0"):
        return Decimal("0")
    return max(Decimal("0"), min(Decimal("1"), distance / limit))


def _payout_state_label(
    state: BuilderAccountState,
    payout_eval: PayoutEvaluation | None,
) -> str:
    if payout_eval is None:
        return "not_applicable"
    if state.phase not in {"SIM_ACTIVE", "SIM_BUFFER_BUILDING", "SIM_PAYOUT_ELIGIBLE"}:
        return "not_applicable"
    if payout_eval.eligible:
        return "eligible"
    return "building_buffer"


def _merge_reason_codes(
    rule_eval: BuilderRuleEvaluation,
    payout_eval: PayoutEvaluation | None,
) -> tuple[str, ...]:
    merged: list[str] = []
    for code in rule_eval.reason_codes:
        if code not in merged:
            merged.append(code)
    if payout_eval:
        for code in payout_eval.reason_codes:
            if code not in merged:
                merged.append(code)
        if payout_eval.consistency_ratio_live > Decimal("0") and (
            BUILDER_PAYOUT_CONSISTENCY_RISK in payout_eval.reason_codes
        ):
            if BUILDER_PAYOUT_CONSISTENCY_RISK not in merged:
                merged.append(BUILDER_PAYOUT_CONSISTENCY_RISK)
    return tuple(merged)
