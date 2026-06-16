"""Builder dashboard aggregation service for API exposure."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.config.builder_contracts_loader import resolve_builder_contract
from backend.domain.builder_models import (
    BuilderAccountState,
    BuilderDailyPnl,
    BuilderPayoutCycleRecord,
    MFFU_BUILDER_PROFILE_ID,
    mffu_builder_50k_profile,
)
from backend.domain.portfolio_risk_models import AccountState, TradeCandidate
from backend.models.global_context_snapshot import GlobalContextSnapshot
from backend.services.builder_orchestrator import (
    BuilderEvaluationInputs,
    BuilderFundingPipeline,
)
from backend.services.builder_payout_engine import BuilderPayoutEngine
from backend.services.builder_rule_engine import BuilderRuleEngine
from backend.services.builder_state_machine import (
    build_intraday_dd_snapshot,
    simulate_loss_scenario,
)
from backend.services.builder_state_store import BuilderStateStore
from backend.services.builder_survival_engine import BuilderSurvivalEngine
from backend.services.funding_lab_service import DEFAULT_PREDICTIONS_DB


class BuilderMetricsResponse(BaseModel):
    """Builder-native metrics for the funding dashboard."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    profile_id: str
    phase: str
    eval_progress_pct: str
    distance_to_trailing_dd: str
    distance_to_dll_soft_pause: str
    buffer_progress_pct: str
    consistency_ratio_live: str
    qualified_days_count: int
    payout_eligibility_state: str
    survival_score: str
    recommended_risk_pct: str
    survival_status: str
    reason_codes: tuple[str, ...] = ()
    withdrawable_amount: str = "0"
    projected_eod_floor: str = "0"
    floor_drift_usd: str = "0"
    distance_to_projected_floor: str = "0"
    is_floor_drift_warning: bool = False
    max_profit_today_usd: str = "0"
    is_consistency_at_risk: bool = False
    buffer_remaining: str = "0"
    qualified_days_required: int = 0
    qualified_days_remaining: int = 0
    avg_daily_profit: str = "0"
    estimated_days_to_payout: int | None = None


class BuilderStateResponse(BaseModel):
    """Persisted Builder account state plus live metrics."""

    model_config = ConfigDict(frozen=True)

    state: BuilderAccountState
    metrics: BuilderMetricsResponse


class BuilderEvaluateRequest(BaseModel):
    """Trade candidate evaluation request for Builder funding."""

    symbol: str = Field(min_length=1)
    direction: Literal["LONG", "SHORT"] = "LONG"
    entry: float = Field(gt=0)
    stop: float | None = Field(default=None, gt=0)
    stop_ticks: int | None = Field(default=None, ge=0)
    prefer_micro: bool = False
    account_id: str = "default"


class BuilderEvaluateResponse(BaseModel):
    """Builder orchestration response for a single candidate."""

    model_config = ConfigDict(frozen=True)

    is_allowed: bool
    contracts: int = Field(ge=0)
    phase: str
    allowed_risk_pct: str
    risk_used_usd: str
    capped_by: str = ""
    reason: str
    reason_codes: tuple[str, ...] = ()
    loss_if_stopped_usd: str = "0"
    equity_after_loss: str = "0"
    distance_to_trailing_dd_after: str = "0"
    distance_to_dll_after: str = "0"
    breaches_on_stop: bool = False
    triggers_soft_pause_on_stop: bool = False


class BuilderEvaluateBatchRequest(BaseModel):
    """Batch trade-candidate evaluation request (e.g. from the market scanner)."""

    candidates: list[BuilderEvaluateRequest] = Field(default_factory=list)


class BuilderEvaluateBatchResponse(BaseModel):
    """Per-symbol Builder gating for a batch of candidates."""

    model_config = ConfigDict(frozen=True)

    results: list[BuilderEvaluateResponse] = Field(default_factory=list)


class BuilderDashboardService:
    """Read Builder state and evaluate candidates for the funding API."""

    def __init__(
        self,
        *,
        predictions_db: str | Path = DEFAULT_PREDICTIONS_DB,
        pipeline: BuilderFundingPipeline | None = None,
    ) -> None:
        self._store = BuilderStateStore(predictions_db=predictions_db)
        self._profile = mffu_builder_50k_profile()
        self._rule_engine = BuilderRuleEngine()
        self._payout_engine = BuilderPayoutEngine()
        self._survival_engine = BuilderSurvivalEngine()
        self._pipeline = pipeline or BuilderFundingPipeline()

    def get_state(self, account_id: str = "default") -> BuilderStateResponse:
        """Load persisted Builder state with computed metrics."""
        state = self._store.load_state(account_id)
        metrics = self._build_metrics(state, account_id)
        return BuilderStateResponse(state=state, metrics=metrics)

    def get_metrics(self, account_id: str = "default") -> BuilderMetricsResponse:
        """Return Builder-native metrics for the dashboard."""
        state = self._store.load_state(account_id)
        return self._build_metrics(state, account_id)

    def evaluate_candidate(
        self,
        request: BuilderEvaluateRequest,
        *,
        context: dict[str, Any] | None = None,
    ) -> BuilderEvaluateResponse:
        """Evaluate a futures candidate under Builder funding rules."""
        state = self._store.load_state(request.account_id)
        account = _account_from_builder_state(state)
        preset = self._profile.to_funding_rule_preset()
        candidate = TradeCandidate(
            symbol=request.symbol.upper(),
            direction=request.direction,
            entry=request.entry,
            stop=request.stop,
        )
        builder_inputs = BuilderEvaluationInputs(
            state=state,
            stop_ticks=request.stop_ticks,
            prefer_micro=request.prefer_micro,
        )
        ctx = GlobalContextSnapshot(is_valid=False)
        decision = self._pipeline.evaluate(
            candidate,
            account,
            preset,
            builder_inputs=builder_inputs,
            context=ctx,
        )
        reason = (
            f"Builder approved: {decision.contracts} contracts"
            if decision.is_allowed
            else f"Builder blocked: {decision.capped_by}"
        )
        if decision.reason_codes:
            reason = f"{reason} | {', '.join(decision.reason_codes)}"

        risk_per_contract = _risk_per_contract(request)
        scenario_contracts = decision.contracts if decision.contracts > 0 else 1
        scenario = simulate_loss_scenario(
            state,
            self._profile,
            contracts=scenario_contracts,
            risk_per_contract_usd=risk_per_contract,
        )
        return BuilderEvaluateResponse(
            is_allowed=decision.is_allowed,
            contracts=decision.contracts,
            phase=decision.phase,
            allowed_risk_pct=_dec(decision.allowed_risk_pct),
            risk_used_usd=_dec(decision.risk_used_usd),
            capped_by=decision.capped_by,
            reason=reason,
            reason_codes=decision.reason_codes,
            loss_if_stopped_usd=_dec(scenario.loss_if_stopped_usd),
            equity_after_loss=_dec(scenario.equity_after_loss),
            distance_to_trailing_dd_after=_dec(scenario.distance_to_trailing_dd_after),
            distance_to_dll_after=_dec(scenario.distance_to_dll_after),
            breaches_on_stop=scenario.breaches_trailing_dd,
            triggers_soft_pause_on_stop=scenario.triggers_daily_soft_pause,
        )

    def evaluate_batch(
        self,
        request: BuilderEvaluateBatchRequest,
    ) -> BuilderEvaluateBatchResponse:
        """Evaluate many candidates (e.g. scanner leaders) under Builder rules."""
        results = [self.evaluate_candidate(candidate) for candidate in request.candidates]
        return BuilderEvaluateBatchResponse(results=results)

    def _build_metrics(
        self,
        state: BuilderAccountState,
        account_id: str,
    ) -> BuilderMetricsResponse:
        rules = self._rule_engine.evaluate(state, self._profile)
        cycle = self._resolve_cycle(account_id)
        daily_pnls = self._daily_pnls(account_id, cycle)
        payout = self._payout_engine.evaluate(
            state,
            self._profile,
            cycle,
            daily_pnls,
        )
        survival = self._survival_engine.evaluate(
            state,
            self._profile,
            rules,
            payout,
        )
        intraday = build_intraday_dd_snapshot(state, self._profile)
        guidance = self._payout_engine.consistency_guidance(self._profile, daily_pnls)
        plan = self._payout_engine.payout_plan(state, self._profile, cycle, daily_pnls)
        merged_codes = _merge_reason_codes(
            survival.reason_codes,
            intraday.reason_codes,
        )
        return BuilderMetricsResponse(
            account_id=account_id,
            profile_id=MFFU_BUILDER_PROFILE_ID,
            phase=state.phase,
            eval_progress_pct=_dec(survival.eval_progress_pct),
            distance_to_trailing_dd=_dec(rules.distance_to_trailing_dd),
            distance_to_dll_soft_pause=_dec(rules.distance_to_dll_soft_pause),
            buffer_progress_pct=_dec(payout.buffer_progress_pct),
            consistency_ratio_live=_dec(payout.consistency_ratio_live),
            qualified_days_count=payout.qualified_days_count,
            payout_eligibility_state=survival.payout_eligibility_state,
            survival_score=_dec(survival.score),
            recommended_risk_pct=_dec(survival.recommended_risk_pct),
            survival_status=survival.status,
            reason_codes=merged_codes,
            withdrawable_amount=_dec(payout.withdrawable_amount),
            projected_eod_floor=_dec(intraday.projected_eod_floor),
            floor_drift_usd=_dec(intraday.floor_drift_usd),
            distance_to_projected_floor=_dec(intraday.distance_to_projected_floor),
            is_floor_drift_warning=intraday.is_floor_drift_warning,
            max_profit_today_usd=_dec(guidance.max_profit_today_usd),
            is_consistency_at_risk=guidance.is_consistency_at_risk,
            buffer_remaining=_dec(plan.buffer_remaining),
            qualified_days_required=plan.qualified_days_required,
            qualified_days_remaining=plan.qualified_days_remaining,
            avg_daily_profit=_dec(plan.avg_daily_profit),
            estimated_days_to_payout=plan.estimated_days_to_payout,
        )

    def _daily_pnls(
        self,
        account_id: str,
        cycle: BuilderPayoutCycleRecord,
    ) -> tuple[BuilderDailyPnl, ...]:
        persisted = tuple(self._store.list_daily_pnls(account_id))
        if persisted:
            return persisted
        return _daily_pnls_from_cycle(cycle)

    def _resolve_cycle(self, account_id: str) -> BuilderPayoutCycleRecord:
        cycles = self._store.list_payout_cycles(account_id)
        if cycles:
            return cycles[-1]
        return self._store.create_payout_cycle(account_id)


def _account_from_builder_state(state: BuilderAccountState) -> AccountState:
    return AccountState(
        initial_capital=float(state.initial_capital),
        current_equity=float(state.current_equity),
        start_of_day_balance=float(state.start_of_day_balance),
        high_watermark_balance=(
            float(state.high_watermark_balance)
            if state.high_watermark_balance is not None
            else None
        ),
        phase=state.phase.lower(),
    )


def _daily_pnls_from_cycle(
    cycle: BuilderPayoutCycleRecord,
) -> tuple[BuilderDailyPnl, ...]:
    if cycle.qualified_days_count <= 0:
        return ()
    per_day = (cycle.buffer_progress / Decimal(cycle.qualified_days_count)).quantize(
        Decimal("0.01")
    )
    return tuple(
        BuilderDailyPnl(date=f"day-{index + 1}", pnl=per_day)
        for index in range(cycle.qualified_days_count)
    )


def _risk_per_contract(request: BuilderEvaluateRequest) -> Decimal:
    """Resolve the USD risk for a single contract given the candidate stop."""
    contract = resolve_builder_contract(request.symbol, prefer_micro=request.prefer_micro)
    tick_size = contract.tick_size
    if request.stop_ticks is not None and request.stop_ticks > 0:
        stop_ticks = request.stop_ticks
    elif request.stop is not None and tick_size > 0:
        price_diff = abs(Decimal(str(request.entry)) - Decimal(str(request.stop)))
        stop_ticks = int((price_diff / tick_size).to_integral_value(rounding="ROUND_FLOOR"))
    else:
        stop_ticks = 0
    return Decimal(stop_ticks) * contract.tick_value


def _merge_reason_codes(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    for group in groups:
        for code in group:
            if code not in merged:
                merged.append(code)
    return tuple(merged)


def _dec(value: Decimal) -> str:
    return format(value, "f")
