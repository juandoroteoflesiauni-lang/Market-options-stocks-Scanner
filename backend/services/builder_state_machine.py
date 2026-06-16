"""Pure Builder Plan phase transitions and trailing-DD breach detection."""

from __future__ import annotations

from decimal import Decimal

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.builder_models import (
    BUILDER_FLOOR_DRIFT_WARNING,
    BUILDER_PHASE_MISMATCH,
    BUILDER_STOP_TRIGGERS_SOFT_PAUSE,
    BUILDER_TRAILING_DD_CRITICAL,
    BUILDER_WOULD_BREACH_ON_STOP,
    BuilderAccountState,
    BuilderIntradayDdSnapshot,
    BuilderLossScenario,
    BuilderPhase,
    BuilderProfile,
    BuilderStateTransitionResult,
    mffu_builder_50k_profile,
)

_ACTIVE_PHASES: frozenset[BuilderPhase] = frozenset(
    {
        "EVAL_ACTIVE",
        "EVAL_PASSED_PENDING",
        "SIM_ACTIVE",
        "SIM_PAYOUT_ELIGIBLE",
        "SIM_BUFFER_BUILDING",
        "LIVE_ACTIVE",
    }
)


def default_builder_account_state(
    profile: BuilderProfile | None = None,
    *,
    account_id: str = "default",
) -> BuilderAccountState:
    """Return a fresh Builder account in EVAL_ACTIVE."""
    active_profile = profile or mffu_builder_50k_profile()
    balance = active_profile.starting_balance
    return BuilderAccountState(
        account_id=account_id,
        profile_id=active_profile.profile_id,
        phase="EVAL_ACTIVE",
        initial_capital=balance,
        current_equity=balance,
        start_of_day_balance=balance,
        high_watermark_balance=balance,
    )


def effective_equity(state: BuilderAccountState) -> Decimal:
    """Compute binding equity including open PnL."""
    return state.current_equity + state.unrealized_pnl


def trailing_dd_floor(state: BuilderAccountState, profile: BuilderProfile) -> Decimal:
    """Compute the EOD trailing drawdown floor for the account."""
    initial = state.initial_capital
    hwm = max(initial, state.high_watermark_balance or initial)
    trailing_limit = hwm - profile.max_loss
    capped = max(initial - profile.max_loss, min(initial, trailing_limit))
    return capped


def distance_to_trailing_dd(state: BuilderAccountState, profile: BuilderProfile) -> Decimal:
    """Return remaining capital above the trailing drawdown floor."""
    return effective_equity(state) - trailing_dd_floor(state, profile)


def projected_trailing_dd_floor(
    state: BuilderAccountState,
    profile: BuilderProfile,
) -> Decimal:
    """Floor that would bind tomorrow if today's intraday high consolidated at EOD.

    Under EOD trailing drawdown, pushing a new intraday high raises the EOD high
    watermark, which lifts the floor (until it locks at the initial balance). This
    projects that worse floor so the desk can see the cost of chasing a new high.
    """
    equity = effective_equity(state)
    current_hwm = max(state.initial_capital, state.high_watermark_balance or state.initial_capital)
    projected_hwm = max(current_hwm, equity)
    projected_state = state.model_copy(update={"high_watermark_balance": projected_hwm})
    return trailing_dd_floor(projected_state, profile)


def build_intraday_dd_snapshot(
    state: BuilderAccountState,
    profile: BuilderProfile,
    *,
    thresholds: FundingThresholds | None = None,
) -> BuilderIntradayDdSnapshot:
    """Build an intraday trailing-DD snapshot with EOD floor-drift early warning."""
    active = thresholds or FundingThresholds()
    equity = effective_equity(state)
    current_floor = trailing_dd_floor(state, profile)
    projected_floor = projected_trailing_dd_floor(state, profile)
    drift = projected_floor - current_floor
    current_hwm = max(state.initial_capital, state.high_watermark_balance or state.initial_capital)
    is_new_high = equity > current_hwm
    is_warning = drift >= active.builder_trailing_dd_critical_usd
    reason_codes = (BUILDER_FLOOR_DRIFT_WARNING,) if is_warning else ()
    return BuilderIntradayDdSnapshot(
        intraday_equity=equity,
        current_eod_floor=current_floor,
        projected_eod_floor=projected_floor,
        floor_drift_usd=max(Decimal("0"), drift),
        distance_to_current_floor=max(Decimal("0"), equity - current_floor),
        distance_to_projected_floor=max(Decimal("0"), equity - projected_floor),
        is_new_high_watermark=is_new_high,
        is_floor_drift_warning=is_warning,
        reason_codes=reason_codes,
    )


def eval_profit(state: BuilderAccountState) -> Decimal:
    """Return realized eval profit relative to the starting balance."""
    return effective_equity(state) - state.initial_capital


def is_breached(state: BuilderAccountState, profile: BuilderProfile) -> bool:
    """Return True when binding equity is at or below the trailing floor."""
    return distance_to_trailing_dd(state, profile) <= Decimal("0")


def with_updated_watermark(state: BuilderAccountState) -> BuilderAccountState:
    """Refresh the high watermark from the current binding equity."""
    equity = effective_equity(state)
    current_hwm = state.high_watermark_balance or state.initial_capital
    if equity <= current_hwm:
        return state
    return state.model_copy(update={"high_watermark_balance": equity})


def simulate_loss_scenario(
    state: BuilderAccountState,
    profile: BuilderProfile,
    *,
    contracts: int,
    risk_per_contract_usd: Decimal,
) -> BuilderLossScenario:
    """Project the account state if a candidate trade hits its stop.

    A loss does not raise the high watermark, so the trailing floor is unchanged;
    only equity and the intraday daily-loss usage move against the account.
    """
    loss = max(Decimal("0"), Decimal(contracts) * risk_per_contract_usd)
    equity = effective_equity(state)
    equity_after = equity - loss
    floor = trailing_dd_floor(state, profile)
    distance_trailing_after = equity_after - floor

    daily_used = max(Decimal("0"), state.start_of_day_balance - intraday_equity_value(state))
    daily_used_after = daily_used + loss
    distance_dll_after = profile.daily_loss_limit - daily_used_after

    breaches = distance_trailing_after <= Decimal("0")
    soft_pause = daily_used_after >= profile.daily_loss_limit
    codes: list[str] = []
    if breaches:
        codes.append(BUILDER_WOULD_BREACH_ON_STOP)
    if soft_pause:
        codes.append(BUILDER_STOP_TRIGGERS_SOFT_PAUSE)

    return BuilderLossScenario(
        contracts=max(0, contracts),
        risk_per_contract_usd=risk_per_contract_usd,
        loss_if_stopped_usd=loss,
        equity_after_loss=equity_after,
        distance_to_trailing_dd_after=distance_trailing_after,
        distance_to_dll_after=distance_dll_after,
        breaches_trailing_dd=breaches,
        triggers_daily_soft_pause=soft_pause,
        reason_codes=tuple(codes),
    )


def intraday_equity_value(state: BuilderAccountState) -> Decimal:
    """Intraday equity under the Builder daily-loss (start-of-day anchored) rule."""
    return state.start_of_day_balance + state.realized_daily_pnl + state.unrealized_pnl


def evaluate_builder_phase(
    state: BuilderAccountState,
    profile: BuilderProfile,
) -> BuilderStateTransitionResult:
    """Evaluate breach and MVP eval transitions for the current Builder state."""
    previous_phase = state.phase
    refreshed = with_updated_watermark(state)

    if refreshed.phase in _ACTIVE_PHASES and is_breached(refreshed, profile):
        breached = refreshed.model_copy(update={"phase": "BREACHED"})
        return BuilderStateTransitionResult(
            previous_phase=previous_phase,
            new_phase="BREACHED",
            state=breached,
            transitioned=previous_phase != "BREACHED",
            reason="Trailing drawdown limit breached",
            reason_codes=(BUILDER_TRAILING_DD_CRITICAL,),
        )

    if refreshed.phase == "EVAL_ACTIVE" and _eval_target_met(refreshed, profile):
        pending = refreshed.model_copy(update={"phase": "EVAL_PASSED_PENDING"})
        return BuilderStateTransitionResult(
            previous_phase=previous_phase,
            new_phase="EVAL_PASSED_PENDING",
            state=pending,
            transitioned=True,
            reason="Evaluation profit target reached",
        )

    return BuilderStateTransitionResult(
        previous_phase=previous_phase,
        new_phase=refreshed.phase,
        state=refreshed,
        transitioned=False,
        reason="No phase transition",
    )


def advance_eval_to_sim(state: BuilderAccountState) -> BuilderStateTransitionResult:
    """Move a pending evaluation account into SIM_ACTIVE."""
    previous_phase = state.phase
    if state.phase != "EVAL_PASSED_PENDING":
        return BuilderStateTransitionResult(
            previous_phase=previous_phase,
            new_phase=previous_phase,
            state=state,
            transitioned=False,
            reason="Phase mismatch: expected EVAL_PASSED_PENDING",
            reason_codes=(BUILDER_PHASE_MISMATCH,),
        )

    sim_state = state.model_copy(update={"phase": "SIM_ACTIVE"})
    return BuilderStateTransitionResult(
        previous_phase=previous_phase,
        new_phase="SIM_ACTIVE",
        state=sim_state,
        transitioned=True,
        reason="Evaluation approved; sim-funded phase started",
    )


def _eval_target_met(state: BuilderAccountState, profile: BuilderProfile) -> bool:
    profit = eval_profit(state)
    return (
        profit >= profile.profit_target
        and state.trading_days_count >= profile.min_trading_days
    )
