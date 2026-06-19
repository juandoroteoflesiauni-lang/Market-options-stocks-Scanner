"""Dynamic risk manager agent (FMP macro). # [TH]"""

from __future__ import annotations

import json
import time

from backend.domain.agentic_models import (
    AgentEnvelope,
    MacroDataSnapshot,
    MacroRiskAssessment,
    MacroRiskResult,
)
from backend.services.agents.base import AgentDegraded, BaseAgent
from backend.services.ai_core.agent_manager import AgentManager

_DEFAULT_ASSESSMENT = MacroRiskAssessment(
    severity="NONE",
    halt_scanner=False,
    stop_loss_multiplier=1.0,
    rationale="Quant default: no macro override",
)

_JSON_HINT = json.dumps(
    {
        "severity": "NONE|LOW|MEDIUM|HIGH|CRITICAL",
        "imminent_event": "string or null",
        "minutes_to_event": 0,
        "halt_scanner": False,
        "stop_loss_multiplier": 1.0,
        "rationale": "string",
    }
)


class DynamicRiskManagerAgent(BaseAgent):
    """Assesses macro risk from FMP snapshot data."""

    def __init__(self, agent_manager: AgentManager, *, timeout_s: float = 3.0) -> None:
        super().__init__(agent_manager, agent_name="macro_micro", timeout_s=timeout_s)

    async def assess(self, macro_snapshot: MacroDataSnapshot) -> MacroRiskResult:
        """Return macro risk assessment or quant-safe default on degradation."""
        start = time.perf_counter()
        user = (
            "Evaluate imminent macro risk for active trading.\n"
            f"Macro snapshot:\n{macro_snapshot.model_dump_json()}"
        )
        try:
            assessment = await self._invoke_structured(
                user=user,
                schema=MacroRiskAssessment,
                json_hint=_JSON_HINT,
            )
            latency = (time.perf_counter() - start) * 1000.0
            return MacroRiskResult(
                assessment=assessment,
                envelope=AgentEnvelope.model_validate(self._envelope(latency_ms=latency)),
            )
        except AgentDegraded as exc:
            latency = (time.perf_counter() - start) * 1000.0
            return MacroRiskResult(
                assessment=_DEFAULT_ASSESSMENT,
                envelope=AgentEnvelope.model_validate(
                    self._envelope(
                        latency_ms=latency,
                        degraded=True,
                        fallback_reason=exc.reason,
                    )
                ),
            )


__all__ = ["DynamicRiskManagerAgent"]
