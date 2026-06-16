from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.domain.portfolio_risk_models import (
    AccountState,
    PortfolioRiskRequest,
    TradeCandidate,
)
from backend.models.trade_record import TradeRecord
from backend.models import CanonicalSignalPayload
from backend.services.consistency_rule_manager import ConsistencyRuleManager
from backend.services.convergence_gate import ConvergenceGate
from backend.services.global_context_engine import GlobalContextEngine
from backend.services.performance_analytics_engine import PerformanceAnalyticsEngine
from backend.services.portfolio_risk_service import PortfolioRiskService
from backend.services.pre_market_check import PreMarketCheck
from backend.services.predictive_risk_gate import PredictiveRiskGate
from backend.domain.builder_models import MFFU_BUILDER_PROFILE_ID
from backend.services.builder_orchestrator import (
    BuilderEvaluationInputs,
    BuilderFundingPipeline,
    is_builder_preset,
)
from backend.services.sizing_engine import MultiFactorInputs, SizingEngine, SizingRequest


class OrchestrationResult(BaseModel):
    """Result of the full funding and risk orchestration pipeline."""

    model_config = ConfigDict(frozen=True)
    is_allowed: bool
    allowed_risk_pct: Decimal
    position_notional: Decimal
    reason: str
    funding_profile: str = "ftmo"
    contracts: int = 0
    phase: str = ""
    reason_codes: tuple[str, ...] = ()
    risk_used_usd: Decimal = Decimal("0")


class FundingOrchestrator:
    """End-to-End Orchestrator for Phase D (Funding)."""

    def __init__(
        self,
        portfolio_risk_svc: PortfolioRiskService,
        perf_engine: PerformanceAnalyticsEngine,
        global_ctx_engine: GlobalContextEngine,
        convergence_gate: ConvergenceGate,
        predictive_risk_gate: PredictiveRiskGate,
        sizing_engine: SizingEngine,
        consistency_mgr: ConsistencyRuleManager,
        pre_market_check: PreMarketCheck,
        builder_pipeline: BuilderFundingPipeline | None = None,
    ) -> None:
        self.portfolio_risk_svc = portfolio_risk_svc
        self.perf_engine = perf_engine
        self.global_ctx_engine = global_ctx_engine
        self.convergence_gate = convergence_gate
        self.predictive_risk_gate = predictive_risk_gate
        self.sizing_engine = sizing_engine
        self.consistency_mgr = consistency_mgr
        self.pre_market_check = pre_market_check
        self.builder_pipeline = builder_pipeline or BuilderFundingPipeline(
            convergence_gate=convergence_gate,
        )

    def evaluate_candidate(
        self,
        candidate: TradeCandidate,
        account: AccountState,
        trades: Sequence[TradeRecord],
        context_data: dict[str, Any],
        portfolio_request: PortfolioRiskRequest,
        *,
        builder_inputs: BuilderEvaluationInputs | None = None,
    ) -> OrchestrationResult:
        """Evaluate a trade candidate through the full funding pipeline."""

        # 0. Pre-Market Check
        pre_decision = self.pre_market_check.evaluate()
        if not pre_decision.is_allowed:
            return self._reject(pre_decision.reason)

        if is_builder_preset(portfolio_request.preset):
            return self._evaluate_builder_candidate(
                candidate=candidate,
                account=account,
                portfolio_request=portfolio_request,
                context_data=context_data,
                builder_inputs=builder_inputs,
            )

        # 1. Consistency Rule Check
        cons_decision = self.consistency_mgr.evaluate(trades)
        if not cons_decision.is_allowed:
            return self._reject(cons_decision.reason)

        # 2. Evaluate Portfolio Risk (Tier 4 to Tier 1)
        risk_resp = self.portfolio_risk_svc.evaluate(portfolio_request)
        if risk_resp.account_status in {"BREACHED", "LOCKED"}:
            return self._reject(f"Account status: {risk_resp.account_status}")

        candidate_decision = next(
            (
                d
                for d in risk_resp.candidate_decisions
                if d.symbol == candidate.symbol and d.direction == candidate.direction
            ),
            None,
        )
        if not candidate_decision or candidate_decision.decision == "BLOCK":
            return self._reject("Blocked by PortfolioRiskService")

        # 3. Performance Metrics (Kelly, BUR, etc.)
        perf_snapshot = self.perf_engine.compute_snapshot(trades, account, window=100)
        kelly_base = Decimal(str(perf_snapshot.kelly_applied))

        # 4. Global Context
        ctx_snapshot = self.global_ctx_engine.evaluate(context_data)

        # 5. Convergence Gate
        conv_decision = self.convergence_gate.evaluate(candidate.direction, ctx_snapshot)
        if not conv_decision.is_allowed:
            return self._reject(conv_decision.reason)

        # 5.5 Predictive Risk Gate (Advanced Options & NLP)
        pred_decision = self.predictive_risk_gate.evaluate(
            direction=candidate.direction,
            symbol=candidate.symbol,
            context_data=context_data,
            entry=float(candidate.entry),
        )
        if not pred_decision.is_allowed:
            reject_reason = " | ".join(pred_decision.reasons) if pred_decision.reasons else "Blocked by PredictiveRiskGate"
            return self._reject(reject_reason)

        # Combine conviction multipliers
        final_conviction = conv_decision.conviction_multiplier * pred_decision.size_multiplier

        # 6. Sizing Engine
        # Calculate stop distance pct
        if candidate.stop and candidate.entry > 0:
            stop_pct = Decimal(str(abs(candidate.entry - candidate.stop) / candidate.entry * 100))
        else:
            stop_pct = Decimal("1.0")  # Fallback

        sizing_req = SizingRequest(
            kelly_base=kelly_base,
            global_factor=ctx_snapshot.global_factor,
            multi_factors=MultiFactorInputs(
                f_conviction=final_conviction,
            ),
            survival_recommended_risk_pct=Decimal(str(candidate_decision.allowed_risk_pct)),
            remaining_daily_risk_pct=Decimal(str(candidate_decision.remaining_daily_risk_pct)),
            remaining_max_risk_pct=Decimal(str(candidate_decision.remaining_max_loss_pct)),
            equity=Decimal(str(account.current_equity)),
            stop_distance_pct=stop_pct,
        )

        size_decision = self.sizing_engine.compute_position_size(sizing_req)

        is_allowed = size_decision.allowed_risk_pct > Decimal("0.0")

        if not is_allowed:
            return self._reject(f"Rejected. Capped by: {size_decision.capped_by}")

        return OrchestrationResult(
            is_allowed=True,
            allowed_risk_pct=size_decision.allowed_risk_pct,
            position_notional=size_decision.position_notional,
            reason=f"Approved. Capped by: {size_decision.capped_by}",
        )

    def _evaluate_builder_candidate(
        self,
        *,
        candidate: TradeCandidate,
        account: AccountState,
        portfolio_request: PortfolioRiskRequest,
        context_data: dict[str, Any],
        builder_inputs: BuilderEvaluationInputs | None,
    ) -> OrchestrationResult:
        ctx_snapshot = self.global_ctx_engine.evaluate(context_data)
        decision = self.builder_pipeline.evaluate(
            candidate,
            account,
            portfolio_request.preset,
            builder_inputs=builder_inputs,
            context=ctx_snapshot,
        )
        if not decision.is_allowed:
            reason = f"Builder blocked: {decision.capped_by}"
            if decision.reason_codes:
                reason = f"{reason} | {', '.join(decision.reason_codes)}"
            return OrchestrationResult(
                is_allowed=False,
                allowed_risk_pct=Decimal("0"),
                position_notional=Decimal("0"),
                reason=reason,
                funding_profile=MFFU_BUILDER_PROFILE_ID,
                contracts=0,
                phase=decision.phase,
                reason_codes=decision.reason_codes,
                risk_used_usd=Decimal("0"),
            )
        return OrchestrationResult(
            is_allowed=True,
            allowed_risk_pct=decision.allowed_risk_pct,
            position_notional=Decimal("0"),
            reason=(
                f"Builder approved: {decision.contracts} "
                f"{decision.phase} capped_by={decision.capped_by}"
            ),
            funding_profile=MFFU_BUILDER_PROFILE_ID,
            contracts=decision.contracts,
            phase=decision.phase,
            reason_codes=decision.reason_codes,
            risk_used_usd=decision.risk_used_usd,
        )

    def _reject(self, reason: str) -> OrchestrationResult:
        return OrchestrationResult(
            is_allowed=False,
            allowed_risk_pct=Decimal("0.0"),
            position_notional=Decimal("0.0"),
            reason=reason,
        )

    def evaluate_canonical(
        self,
        payload: CanonicalSignalPayload,
        account: AccountState,
        trades: Sequence[TradeRecord],
        context_data: dict[str, Any],
        portfolio_request: PortfolioRiskRequest,
        *,
        builder_inputs: BuilderEvaluationInputs | None = None,
    ) -> OrchestrationResult:
        """Evaluate a canonical signal payload through the full funding pipeline."""
        candidate = candidate_from_canonical(payload)

        if payload.asset_type == "option":
            # Extract option parameters for sizing
            b_inputs = builder_inputs or BuilderEvaluationInputs()
            bid_ask_spread = Decimal(str(context_data.get("bid_ask_spread_pct", 0.0)))
            max_spread = Decimal(str(context_data.get("max_bid_ask_spread_pct", 8.0)))
            premium = payload.max_loss_usd if payload.max_loss_usd is not None else Decimal("100.00")
            margin = premium
            buying_power = Decimal(str(account.current_equity))

            extended_inputs = b_inputs.model_copy(
                update={
                    "asset_type": "option",
                    "premium_per_contract": premium,
                    "bid_ask_spread_pct": bid_ask_spread,
                    "margin_required_per_contract": margin,
                    "available_buying_power": buying_power,
                    "max_bid_ask_spread_pct": max_spread,
                }
            )
            builder_inputs = extended_inputs

        return self.evaluate_candidate(
            candidate=candidate,
            account=account,
            trades=trades,
            context_data=context_data,
            portfolio_request=portfolio_request,
            builder_inputs=builder_inputs,
        )


def candidate_from_canonical(payload: CanonicalSignalPayload) -> TradeCandidate:
    """Translate CanonicalSignalPayload to the internal TradeCandidate representation."""
    direction_map = {
        "bullish": "LONG",
        "bearish": "SHORT",
        "neutral": "LONG",
    }
    stop_val = float(payload.stop_loss_price) if payload.stop_loss_price is not None else None

    return TradeCandidate(
        symbol=payload.symbol,
        direction=direction_map.get(payload.direction, "LONG"),
        entry=float(payload.entry_price),
        stop=stop_val,
        confidence=payload.confidence,
        source_module=payload.source_engine,
        evidence_by_module={"reason_codes": list(payload.reason_codes)},
    )
