"""Agentic trade gate — orchestrates agents before execution. # [TH][PD-3]"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from backend.config.settings import load_settings
from backend.domain.agentic_models import (
    AgenticTradeDecisionEvent,
    Decision,
    ExecutionCommitteeResult,
    MacroRiskResult,
    OptionsAnalystResult,
    OptionsContractContext,
)
from backend.services.agentic_data_facade import AgenticDataFacade
from backend.services.agentic_macro_state import update_agentic_macro_state
from backend.services.agents.execution_committee import ExecutionCommittee
from backend.services.agents.options_analyst_agent import OptionsAnalystAgent
from backend.services.agents.risk_manager_agent import DynamicRiskManagerAgent
from backend.services.ai_core.agent_manager import AgentManager
from backend.services.llm_call_policy import should_run_agentic_committee

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgenticGateOutcome:
    """Result of agentic evaluation for a single trade candidate."""

    allow_execute: bool
    size_modifier: float
    final_decision: Decision
    quant_default_used: bool
    committee: ExecutionCommitteeResult | None = None
    macro_risk: MacroRiskResult | None = None
    options_analysis: OptionsAnalystResult | None = None
    correlation_id: str = ""


class AgenticTradeGate:
    """Thin orchestrator: macro refresh, options advisory, committee verdict."""

    def __init__(
        self,
        *,
        data_facade: AgenticDataFacade,
        agent_manager: AgentManager | None = None,
        risk_agent: DynamicRiskManagerAgent | None = None,
        options_agent: OptionsAnalystAgent | None = None,
        committee: ExecutionCommittee | None = None,
    ) -> None:
        settings = load_settings()
        self._timeout_s = settings.agentic_llm_timeout_s
        manager = agent_manager or AgentManager()
        self._data_facade = data_facade
        self._risk_agent = risk_agent or DynamicRiskManagerAgent(manager, timeout_s=self._timeout_s)
        self._options_agent = options_agent or OptionsAnalystAgent(
            manager, timeout_s=self._timeout_s
        )
        self._committee = committee or ExecutionCommittee(manager, timeout_s=self._timeout_s)

    async def refresh_macro_risk(self) -> MacroRiskResult:
        """Poll macro data and update shared macro state."""
        settings = load_settings()
        macro_result = await self._data_facade.fetch_macro_snapshot(
            horizon_days=settings.agentic_macro_horizon_days
        )
        if macro_result.is_failure:
            degraded = await self._risk_agent.assess(_empty_macro_snapshot())
            update_agentic_macro_state(degraded)
            return degraded

        snapshot = macro_result.unwrap()
        result = await self._risk_agent.assess(snapshot)
        update_agentic_macro_state(result)
        return result

    async def evaluate_trade(
        self,
        *,
        module: Literal["alpaca", "bingx"],
        symbol: str,
        contract_symbol: str,
        signal_score: float,
        options_context: OptionsContractContext | None = None,
        has_critical_risk: bool = False,
    ) -> AgenticGateOutcome:
        """Run agentic pipeline for one trade candidate."""
        correlation_id = str(uuid.uuid4())
        policy = should_run_agentic_committee(
            signal_score=signal_score,
            has_critical_risk=has_critical_risk,
        )

        macro_risk = await self.refresh_macro_risk()
        options_analysis: OptionsAnalystResult | None = None
        if options_context is not None:
            options_analysis = await self._options_agent.assess(options_context)

        if not policy.run:
            return AgenticGateOutcome(
                allow_execute=True,
                size_modifier=1.0,
                final_decision="EXECUTE",
                quant_default_used=True,
                macro_risk=macro_risk,
                options_analysis=options_analysis,
                correlation_id=correlation_id,
            )

        committee = await self._committee.deliberate(
            contract_symbol=contract_symbol,
            symbol=symbol,
            options_context=options_context,
            options_analysis=options_analysis,
            macro_risk=macro_risk,
        )
        return self._outcome_from_committee(
            committee=committee,
            macro_risk=macro_risk,
            options_analysis=options_analysis,
            quant_fallback=policy.quant_fallback_on_degraded,
            correlation_id=correlation_id,
        )

    def build_audit_event(
        self,
        outcome: AgenticGateOutcome,
        *,
        module: Literal["alpaca", "bingx"],
        symbol: str,
        contract_symbol: str,
    ) -> AgenticTradeDecisionEvent:
        """Build a persistable audit event from a gate outcome."""
        return AgenticTradeDecisionEvent(
            correlation_id=outcome.correlation_id,
            module=module,
            symbol=symbol,
            contract_symbol=contract_symbol,
            created_at=datetime.now(tz=UTC),
            macro_risk=outcome.macro_risk,
            options_analysis=outcome.options_analysis,
            committee=outcome.committee,
            final_decision=outcome.final_decision,
            quant_default_used=outcome.quant_default_used,
        )

    @staticmethod
    def apply_size_modifier(quantity: int | float, modifier: float) -> float:
        """Scale quantity by committee modifier; preserve perp fractional sizes."""
        scaled = float(quantity) * max(0.0, min(1.0, modifier))
        if scaled <= 0:
            return 0.0
        if scaled < 1.0:
            return round(scaled, 6)
        return float(max(1, math.floor(scaled)))

    @staticmethod
    def _outcome_from_committee(
        *,
        committee: ExecutionCommitteeResult,
        macro_risk: MacroRiskResult,
        options_analysis: OptionsAnalystResult | None,
        quant_fallback: bool,
        correlation_id: str,
    ) -> AgenticGateOutcome:
        degraded = committee.envelope.degraded
        verdict = committee.verdict

        if degraded and quant_fallback:
            return AgenticGateOutcome(
                allow_execute=True,
                size_modifier=1.0,
                final_decision="EXECUTE",
                quant_default_used=True,
                committee=committee,
                macro_risk=macro_risk,
                options_analysis=options_analysis,
                correlation_id=correlation_id,
            )

        if degraded or verdict.decision == "PASS":
            return AgenticGateOutcome(
                allow_execute=False,
                size_modifier=0.0,
                final_decision="PASS",
                quant_default_used=degraded,
                committee=committee,
                macro_risk=macro_risk,
                options_analysis=options_analysis,
                correlation_id=correlation_id,
            )

        return AgenticGateOutcome(
            allow_execute=True,
            size_modifier=verdict.recommended_position_size_modifier,
            final_decision="EXECUTE",
            quant_default_used=False,
            committee=committee,
            macro_risk=macro_risk,
            options_analysis=options_analysis,
            correlation_id=correlation_id,
        )


def _empty_macro_snapshot():
    from backend.domain.agentic_models import MacroDataSnapshot

    return MacroDataSnapshot(fetched_at=datetime.now(tz=UTC))


__all__ = ["AgenticGateOutcome", "AgenticTradeGate"]
