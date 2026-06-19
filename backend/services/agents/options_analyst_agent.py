"""Options analyst agent (Massive/Greeks context). # [TH]"""

from __future__ import annotations

import json
import time

from backend.domain.agentic_models import (
    AgentEnvelope,
    OptionsAnalystAssessment,
    OptionsAnalystResult,
    OptionsContractContext,
)
from backend.services.agents.base import AgentDegraded, BaseAgent
from backend.services.ai_core.agent_manager import AgentManager

_DEFAULT_ASSESSMENT = OptionsAnalystAssessment(
    contract_symbol="UNKNOWN",
    iv_rank_eval="FAIR",
    gamma_squeeze_risk="NONE",
    liquidity_ok=True,
    directional_bias="NEUTRAL",
    confidence_score=0,
    rationale="Quant default: no options override",
)

_JSON_HINT = json.dumps(
    {
        "contract_symbol": "string",
        "iv_rank_eval": "CHEAP|FAIR|RICH",
        "gamma_squeeze_risk": "NONE|LOW|MEDIUM|HIGH|CRITICAL",
        "liquidity_ok": True,
        "directional_bias": "BULLISH|BEARISH|NEUTRAL",
        "confidence_score": 0,
        "rationale": "string",
    }
)


class OptionsAnalystAgent(BaseAgent):
    """Assesses a single options contract for trade suitability."""

    def __init__(self, agent_manager: AgentManager, *, timeout_s: float = 3.0) -> None:
        super().__init__(agent_manager, agent_name="options_gex", timeout_s=timeout_s)

    async def assess(self, context: OptionsContractContext) -> OptionsAnalystResult:
        """Return options analysis or neutral advisory default on degradation."""
        start = time.perf_counter()
        user = (
            "Analyze this options contract for execution suitability.\n"
            f"Contract context:\n{context.model_dump_json()}"
        )
        try:
            assessment = await self._invoke_structured(
                user=user,
                schema=OptionsAnalystAssessment,
                json_hint=_JSON_HINT,
            )
            latency = (time.perf_counter() - start) * 1000.0
            return OptionsAnalystResult(
                assessment=assessment,
                envelope=AgentEnvelope.model_validate(self._envelope(latency_ms=latency)),
            )
        except AgentDegraded as exc:
            latency = (time.perf_counter() - start) * 1000.0
            fallback = _DEFAULT_ASSESSMENT.model_copy(
                update={"contract_symbol": context.contract_symbol}
            )
            return OptionsAnalystResult(
                assessment=fallback,
                envelope=AgentEnvelope.model_validate(
                    self._envelope(
                        latency_ms=latency,
                        degraded=True,
                        fallback_reason=exc.reason,
                    )
                ),
            )


__all__ = ["OptionsAnalystAgent"]
