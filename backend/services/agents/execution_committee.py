"""Execution committee: Bull + Bear debate + Trader verdict. # [TH]"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from backend.domain.agentic_models import (
    AgentEnvelope,
    ExecutionCommitteeResult,
    MacroRiskResult,
    OptionsAnalystResult,
    OptionsContractContext,
    ResearcherArgument,
    TraderVerdict,
)
from backend.services.agents.base import AgentDegraded, BaseAgent
from backend.services.ai_core.agent_manager import AgentManager

_BULL_HINT = json.dumps({"stance": "BULLISH", "thesis": "string", "key_risks": ["string"]})
_BEAR_HINT = json.dumps({"stance": "BEARISH", "thesis": "string", "key_risks": ["string"]})
_TRADER_HINT = json.dumps(
    {
        "decision": "EXECUTE|PASS",
        "confidence_score": 0,
        "recommended_position_size_modifier": 1.0,
        "rationale": "string",
    }
)

_DEFAULT_VERDICT = TraderVerdict(
    decision="PASS",
    confidence_score=0,
    recommended_position_size_modifier=0.0,
    rationale="Quant default: committee degraded, abort trade",
)


class _ResearcherAgent(BaseAgent):
    def __init__(self, agent_manager: AgentManager, agent_name: str, *, timeout_s: float) -> None:
        super().__init__(agent_manager, agent_name=agent_name, timeout_s=timeout_s)


class ExecutionCommittee:
    """Runs Bull/Bear concurrently, then Trader synthesis."""

    def __init__(self, agent_manager: AgentManager, *, timeout_s: float = 3.0) -> None:
        self._bull = _ResearcherAgent(agent_manager, "sentiment", timeout_s=timeout_s)
        self._bear = _ResearcherAgent(agent_manager, "forensic", timeout_s=timeout_s)
        self._trader = _ResearcherAgent(agent_manager, "orchestrator", timeout_s=timeout_s)

    async def deliberate(
        self,
        *,
        contract_symbol: str,
        symbol: str,
        options_context: OptionsContractContext | None,
        options_analysis: OptionsAnalystResult | None,
        macro_risk: MacroRiskResult | None,
    ) -> ExecutionCommitteeResult:
        """Debate and return a typed committee verdict."""
        start = time.perf_counter()
        context_blob = self._build_context(
            symbol=symbol,
            contract_symbol=contract_symbol,
            options_context=options_context,
            options_analysis=options_analysis,
            macro_risk=macro_risk,
        )
        try:
            bull_task = self._bull._invoke_structured(
                user=f"Argue BULLISH case:\n{context_blob}",
                schema=ResearcherArgument,
                json_hint=_BULL_HINT,
            )
            bear_task = self._bear._invoke_structured(
                user=f"Argue BEARISH case:\n{context_blob}",
                schema=ResearcherArgument,
                json_hint=_BEAR_HINT,
            )
            bull, bear = await asyncio.gather(bull_task, bear_task)
            trader_user = (
                f"Synthesize final trade verdict.\n{context_blob}\n\n"
                f"Bull:\n{bull.model_dump_json()}\n\nBear:\n{bear.model_dump_json()}"
            )
            verdict = await self._trader._invoke_structured(
                user=trader_user,
                schema=TraderVerdict,
                json_hint=_TRADER_HINT,
            )
            latency = (time.perf_counter() - start) * 1000.0
            return ExecutionCommitteeResult(
                contract_symbol=contract_symbol,
                bull=bull,
                bear=bear,
                verdict=verdict,
                envelope=AgentEnvelope.model_validate(self._trader._envelope(latency_ms=latency)),
            )
        except AgentDegraded as exc:
            latency = (time.perf_counter() - start) * 1000.0
            return self._degraded_result(
                contract_symbol=contract_symbol,
                reason=exc.reason,
                latency_ms=latency,
            )

    @staticmethod
    def _build_context(
        *,
        symbol: str,
        contract_symbol: str,
        options_context: OptionsContractContext | None,
        options_analysis: OptionsAnalystResult | None,
        macro_risk: MacroRiskResult | None,
    ) -> str:
        parts: dict[str, Any] = {
            "symbol": symbol,
            "contract_symbol": contract_symbol,
        }
        if options_context is not None:
            parts["options_context"] = options_context.model_dump(mode="json")
        if options_analysis is not None:
            parts["options_analysis"] = options_analysis.model_dump(mode="json")
        if macro_risk is not None:
            parts["macro_risk"] = macro_risk.model_dump(mode="json")
        return json.dumps(parts, indent=2)

    def _degraded_result(
        self,
        *,
        contract_symbol: str,
        reason: str,
        latency_ms: float,
    ) -> ExecutionCommitteeResult:
        placeholder = ResearcherArgument(
            stance="BULLISH",
            thesis="Unavailable",
            key_risks=[],
        )
        bear = ResearcherArgument(stance="BEARISH", thesis="Unavailable", key_risks=[])
        return ExecutionCommitteeResult(
            contract_symbol=contract_symbol,
            bull=placeholder,
            bear=bear,
            verdict=_DEFAULT_VERDICT,
            envelope=AgentEnvelope.model_validate(
                self._trader._envelope(
                    latency_ms=latency_ms,
                    degraded=True,
                    fallback_reason=reason,
                )
            ),
        )


__all__ = ["ExecutionCommittee"]
