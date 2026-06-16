"""Builder Plan contractual rule engine (trailing DD, DLL soft pause, contract cap)."""

from __future__ import annotations

from decimal import Decimal

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.builder_models import (
    BUILDER_CONTRACT_CAP_EXCEEDED,
    BUILDER_DAILY_SOFT_PAUSE_THREAT,
    BUILDER_PHASE_MISMATCH,
    BUILDER_TRAILING_DD_CRITICAL,
    BuilderAccountState,
    BuilderPhase,
    BuilderProfile,
    BuilderRuleEvaluation,
)
from backend.services.builder_state_machine import (
    distance_to_trailing_dd,
    effective_equity,
    is_breached,
    trailing_dd_floor,
)

_TRADEABLE_PHASES: frozenset[BuilderPhase] = frozenset(
    {
        "EVAL_ACTIVE",
        "SIM_ACTIVE",
        "SIM_PAYOUT_ELIGIBLE",
        "SIM_BUFFER_BUILDING",
        "LIVE_ACTIVE",
    }
)


class BuilderRuleEngine:
    """Evaluate Builder contractual constraints for an account snapshot."""

    def __init__(self, thresholds: FundingThresholds | None = None) -> None:
        active = thresholds or FundingThresholds()
        self._trailing_critical_usd = active.builder_trailing_dd_critical_usd
        self._dll_threat_usd = active.builder_dll_soft_pause_threat_usd

    def evaluate(
        self,
        state: BuilderAccountState,
        profile: BuilderProfile,
        *,
        requested_contracts: int = 0,
        contract_is_micro: bool = False,
        open_mini_contracts: int = 0,
        open_micro_contracts: int = 0,
        allowed_phases: frozenset[BuilderPhase] | None = None,
    ) -> BuilderRuleEvaluation:
        """Compute Builder risk distances, caps and reason codes."""
        trailing_distance = distance_to_trailing_dd(state, profile)
        daily_used = _daily_loss_used(state)
        dll_distance = profile.daily_loss_limit - daily_used
        remaining_daily = max(Decimal("0"), dll_distance)
        remaining_cycle = min(remaining_daily, max(Decimal("0"), trailing_distance))
        breached = is_breached(state, profile)
        soft_pause = daily_used >= profile.daily_loss_limit
        available_cap = _available_contract_cap(
            profile,
            contract_is_micro=contract_is_micro,
            open_mini_contracts=open_mini_contracts,
            open_micro_contracts=open_micro_contracts,
        )
        cap_exceeded = requested_contracts > available_cap if requested_contracts > 0 else False
        reason_codes = _collect_reason_codes(
            state=state,
            trailing_distance=trailing_distance,
            dll_distance=dll_distance,
            breached=breached,
            soft_pause=soft_pause,
            cap_exceeded=cap_exceeded,
            allowed_phases=allowed_phases,
            trailing_critical_usd=self._trailing_critical_usd,
            dll_threat_usd=self._dll_threat_usd,
        )
        return BuilderRuleEvaluation(
            distance_to_trailing_dd=max(Decimal("0"), trailing_distance),
            distance_to_dll_soft_pause=max(Decimal("0"), dll_distance),
            remaining_daily_risk=remaining_daily,
            remaining_cycle_risk=remaining_cycle,
            available_contract_cap=available_cap,
            trailing_dd_floor=trailing_dd_floor(state, profile),
            dll_soft_pause_floor=state.start_of_day_balance - profile.daily_loss_limit,
            daily_loss_used=daily_used,
            is_breached=breached,
            is_daily_soft_pause=soft_pause,
            blocks_new_entries=breached or soft_pause,
            contract_cap_exceeded=cap_exceeded,
            reason_codes=reason_codes,
        )


def intraday_equity(state: BuilderAccountState) -> Decimal:
    """Compute intraday equity using the Builder equity daily-loss rule."""
    return (
        state.start_of_day_balance + state.realized_daily_pnl + state.unrealized_pnl
    )


def _daily_loss_used(state: BuilderAccountState) -> Decimal:
    return max(Decimal("0"), state.start_of_day_balance - intraday_equity(state))


def _available_contract_cap(
    profile: BuilderProfile,
    *,
    contract_is_micro: bool,
    open_mini_contracts: int,
    open_micro_contracts: int,
) -> int:
    if contract_is_micro:
        return max(0, profile.max_micros - open_micro_contracts)
    return max(0, profile.max_minis - open_mini_contracts)


def _collect_reason_codes(
    *,
    state: BuilderAccountState,
    trailing_distance: Decimal,
    dll_distance: Decimal,
    breached: bool,
    soft_pause: bool,
    cap_exceeded: bool,
    allowed_phases: frozenset[BuilderPhase] | None,
    trailing_critical_usd: Decimal,
    dll_threat_usd: Decimal,
) -> tuple[str, ...]:
    codes: list[str] = []
    if breached or trailing_distance <= Decimal("0"):
        codes.append(BUILDER_TRAILING_DD_CRITICAL)
    elif trailing_distance <= trailing_critical_usd:
        codes.append(BUILDER_TRAILING_DD_CRITICAL)
    if soft_pause or dll_distance <= Decimal("0"):
        codes.append(BUILDER_DAILY_SOFT_PAUSE_THREAT)
    elif dll_distance <= dll_threat_usd:
        codes.append(BUILDER_DAILY_SOFT_PAUSE_THREAT)
    if cap_exceeded:
        codes.append(BUILDER_CONTRACT_CAP_EXCEEDED)
    phase_gate = allowed_phases if allowed_phases is not None else _TRADEABLE_PHASES
    if state.phase not in phase_gate:
        codes.append(BUILDER_PHASE_MISMATCH)
    return tuple(_dedupe(codes))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
