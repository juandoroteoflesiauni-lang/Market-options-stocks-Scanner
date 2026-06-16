"""Builder-native funding pipeline orchestration."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from backend.config.builder_contracts_loader import resolve_builder_contract
from backend.config.funding_thresholds import FundingThresholds
from backend.domain.builder_models import (
    BUILDER_PHASE_MISMATCH,
    BuilderAccountState,
    BuilderDailyPnl,
    BuilderDecision,
    BuilderPayoutCycleRecord,
    BuilderPhase,
    BuilderProfile,
    MFFU_BUILDER_PROFILE_ID,
    mffu_builder_50k_profile,
)
from backend.domain.portfolio_risk_models import AccountState, FundingRulePreset, TradeCandidate
from backend.models.global_context_snapshot import GlobalContextSnapshot
from backend.services.builder_payout_engine import BuilderPayoutEngine
from backend.services.builder_rule_engine import BuilderRuleEngine
from backend.services.builder_sizing_overlay import BuilderSizingOverlay
from backend.services.builder_state_machine import evaluate_builder_phase
from backend.services.builder_survival_engine import BuilderSurvivalEngine
from backend.services.convergence_gate import ConvergenceGate


class BuilderEvaluationInputs(BaseModel):
    """Optional Builder-specific inputs for orchestration."""

    model_config = ConfigDict(frozen=True)

    state: BuilderAccountState | None = None
    daily_pnls: tuple[BuilderDailyPnl, ...] = ()
    payout_cycle: BuilderPayoutCycleRecord | None = None
    stop_ticks: int | None = None
    open_mini_contracts: int = 0
    open_micro_contracts: int = 0
    prefer_micro: bool = False

    # Options sizer extended inputs
    asset_type: str = "future"
    premium_per_contract: Decimal = Decimal("0")
    bid_ask_spread_pct: Decimal = Decimal("0")
    margin_required_per_contract: Decimal = Decimal("0")
    available_buying_power: Decimal = Decimal("0")
    max_bid_ask_spread_pct: Decimal = Decimal("0")


class BuilderFundingPipeline:
    """End-to-end Builder evaluation: phase → rules → payout → survival → sizing."""

    def __init__(
        self,
        *,
        thresholds: FundingThresholds | None = None,
        rule_engine: BuilderRuleEngine | None = None,
        payout_engine: BuilderPayoutEngine | None = None,
        survival_engine: BuilderSurvivalEngine | None = None,
        sizing_overlay: BuilderSizingOverlay | None = None,
        convergence_gate: ConvergenceGate | None = None,
    ) -> None:
        active_thresholds = thresholds or FundingThresholds()
        self._rule_engine = rule_engine or BuilderRuleEngine(active_thresholds)
        self._payout_engine = payout_engine or BuilderPayoutEngine(active_thresholds)
        self._survival_engine = survival_engine or BuilderSurvivalEngine(active_thresholds)
        self._sizing_overlay = sizing_overlay or BuilderSizingOverlay(
            active_thresholds,
            self._rule_engine,
        )
        self._convergence_gate = convergence_gate or ConvergenceGate()

    def evaluate(
        self,
        candidate: TradeCandidate,
        account: AccountState,
        preset: FundingRulePreset,
        *,
        builder_inputs: BuilderEvaluationInputs | None = None,
        context: GlobalContextSnapshot | None = None,
    ) -> BuilderDecision:
        """Evaluate a trade candidate under Builder funding rules."""
        profile = builder_profile_from_preset(preset)
        inputs = builder_inputs or BuilderEvaluationInputs()
        state = inputs.state or builder_account_from_portfolio(account, preset)
        transition = evaluate_builder_phase(state, profile)
        state = transition.state

        is_option = getattr(inputs, "asset_type", "future") == "option"

        if is_option:
            stop_ticks = 0
            rules = self._rule_engine.evaluate(
                state,
                profile,
                contract_is_micro=False,
                open_mini_contracts=inputs.open_mini_contracts,
                open_micro_contracts=inputs.open_micro_contracts,
            )
        else:
            stop_ticks = inputs.stop_ticks or _stop_ticks_from_candidate(
                candidate,
                symbol=candidate.symbol,
                prefer_micro=inputs.prefer_micro,
            )
            rules = self._rule_engine.evaluate(
                state,
                profile,
                contract_is_micro=_contract_is_micro(candidate.symbol, inputs.prefer_micro),
                open_mini_contracts=inputs.open_mini_contracts,
                open_micro_contracts=inputs.open_micro_contracts,
            )
        payout_eval = None
        if state.phase in {"SIM_ACTIVE", "SIM_BUFFER_BUILDING", "SIM_PAYOUT_ELIGIBLE"}:
            cycle = inputs.payout_cycle or _default_payout_cycle(state, profile)
            payout_eval = self._payout_engine.evaluate(
                state,
                profile,
                cycle,
                inputs.daily_pnls,
            )
            if payout_eval.suggested_phase and payout_eval.suggested_phase != state.phase:
                state = state.model_copy(update={"phase": payout_eval.suggested_phase})

        survival = self._survival_engine.evaluate(state, profile, rules, payout_eval)
        reason_codes = survival.reason_codes

        if rules.is_breached:
            return _builder_reject(
                state=state,
                reason_codes=reason_codes,
                capped_by="trailing_dd_breach",
                payout_eval=payout_eval,
            )
        if rules.blocks_new_entries:
            capped_by = (
                "daily_soft_pause"
                if rules.is_daily_soft_pause
                else "builder_risk_block"
            )
            return _builder_reject(
                state=state,
                reason_codes=reason_codes,
                capped_by=capped_by,
                payout_eval=payout_eval,
            )
        if state.phase not in _TRADEABLE_PHASES:
            merged = _merge_codes(reason_codes, (BUILDER_PHASE_MISMATCH,))
            return _builder_reject(
                state=state,
                reason_codes=merged,
                capped_by="phase_mismatch",
                payout_eval=payout_eval,
            )

        conv = self._convergence_gate.evaluate(
            candidate.direction,
            context or GlobalContextSnapshot(is_valid=False),
        )
        if not conv.is_allowed:
            return _builder_reject(
                state=state,
                reason_codes=reason_codes,
                capped_by="convergence_gate",
                payout_eval=payout_eval,
            )

        consistency_ratio = (
            payout_eval.consistency_ratio_live if payout_eval else Decimal("0")
        )
        sizing = self._sizing_overlay.compute_contracts(
            state,
            profile,
            symbol=candidate.symbol,
            stop_ticks=stop_ticks,
            rule_eval=rules,
            f_signal=conv.conviction_multiplier,
            f_regime=Decimal("1.0"),
            consistency_ratio_live=consistency_ratio,
            open_mini_contracts=inputs.open_mini_contracts,
            open_micro_contracts=inputs.open_micro_contracts,
            prefer_micro=inputs.prefer_micro,
            asset_type=getattr(inputs, "asset_type", "future"),
            premium_per_contract=getattr(inputs, "premium_per_contract", Decimal("0")),
            bid_ask_spread_pct=getattr(inputs, "bid_ask_spread_pct", Decimal("0")),
            margin_required_per_contract=getattr(inputs, "margin_required_per_contract", Decimal("0")),
            available_buying_power=getattr(inputs, "available_buying_power", Decimal("0")),
            max_bid_ask_spread_pct=getattr(inputs, "max_bid_ask_spread_pct", Decimal("0")),
        )
        if sizing.contracts <= 0:
            return _builder_reject(
                state=state,
                reason_codes=reason_codes,
                capped_by=sizing.capped_by or "zero_contracts",
                payout_eval=payout_eval,
            )

        return BuilderDecision(
            is_allowed=True,
            contracts=sizing.contracts,
            phase=state.phase,
            allowed_risk_pct=sizing.allowed_risk_pct,
            risk_used_usd=sizing.risk_used_usd,
            capped_by=sizing.capped_by,
            payout_state=payout_eval,
            reason_codes=reason_codes,
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


def is_builder_preset(preset: FundingRulePreset) -> bool:
    """Return True when the portfolio preset targets the Builder plan."""
    return preset.id in {MFFU_BUILDER_PROFILE_ID, "mffu_builder_50k"}


def builder_profile_from_preset(preset: FundingRulePreset) -> BuilderProfile:
    """Map a funding preset into a Builder profile."""
    if preset.max_loss_amount == 1500.0:
        return mffu_builder_50k_profile(dd_option="addon")
    return mffu_builder_50k_profile()


def builder_account_from_portfolio(
    account: AccountState,
    preset: FundingRulePreset,
) -> BuilderAccountState:
    """Translate a generic account snapshot into Builder state."""
    profile = builder_profile_from_preset(preset)
    return BuilderAccountState(
        initial_capital=Decimal(str(account.initial_capital)),
        current_equity=Decimal(str(account.current_equity)),
        start_of_day_balance=Decimal(str(account.start_of_day_balance)),
        high_watermark_balance=(
            Decimal(str(account.high_watermark_balance))
            if account.high_watermark_balance is not None
            else Decimal(str(account.initial_capital))
        ),
        phase=_map_account_phase(account.phase),
        profile_id=profile.profile_id,
    )


def _map_account_phase(phase: str) -> BuilderPhase:
    normalized = phase.strip().lower()
    if normalized in {"sim", "sim_active", "funded", "sim_funded"}:
        return "SIM_ACTIVE"
    if normalized in {"live", "live_active"}:
        return "LIVE_ACTIVE"
    if normalized in {"breached"}:
        return "BREACHED"
    return "EVAL_ACTIVE"


def _default_payout_cycle(
    state: BuilderAccountState,
    profile: BuilderProfile,
) -> BuilderPayoutCycleRecord:
    return BuilderPayoutCycleRecord(
        cycle_id=f"{state.account_id}-cycle-1",
        account_id=state.account_id,
        cycle_number=1,
        buffer_target=profile.payout_buffer,
    )


def _stop_ticks_from_candidate(
    candidate: TradeCandidate,
    *,
    symbol: str,
    prefer_micro: bool,
) -> int:
    contract = resolve_builder_contract(symbol, prefer_micro=prefer_micro)
    if candidate.stop is None or candidate.entry <= 0:
        return 0
    price_diff = abs(float(candidate.entry) - float(candidate.stop))
    tick_size = float(contract.tick_size)
    if tick_size <= 0:
        return 0
    return max(0, math.floor(price_diff / tick_size))


def _contract_is_micro(symbol: str, prefer_micro: bool) -> bool:
    return resolve_builder_contract(symbol, prefer_micro=prefer_micro).is_micro


def _builder_reject(
    *,
    state: BuilderAccountState,
    reason_codes: Sequence[str],
    capped_by: str,
    payout_eval: object | None,
) -> BuilderDecision:
    from backend.domain.builder_models import PayoutEvaluation

    payout = payout_eval if isinstance(payout_eval, PayoutEvaluation) else None
    return BuilderDecision(
        is_allowed=False,
        contracts=0,
        phase=state.phase,
        capped_by=capped_by,
        payout_state=payout,
        reason_codes=tuple(reason_codes),
    )


def _merge_codes(
    existing: Sequence[str],
    extra: Sequence[str],
) -> tuple[str, ...]:
    merged = list(existing)
    for code in extra:
        if code not in merged:
            merged.append(code)
    return tuple(merged)


__all__ = [
    "BuilderEvaluationInputs",
    "BuilderFundingPipeline",
    "builder_account_from_portfolio",
    "builder_profile_from_preset",
    "is_builder_preset",
]
