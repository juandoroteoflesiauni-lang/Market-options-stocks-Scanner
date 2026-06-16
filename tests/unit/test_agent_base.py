"""Unit tests for BaseAgent structured invocation."""

from __future__ import annotations

import json

import pytest

from backend.domain.agentic_models import MacroRiskAssessment
from backend.services.agents.base import AgentDegraded, BaseAgent, extract_json_payload
from backend.services.ai_core.agent_manager import AgentManager


@pytest.mark.asyncio
async def test_invoke_structured_parses_json() -> None:
    payload = {
        "severity": "LOW",
        "halt_scanner": False,
        "stop_loss_multiplier": 1.0,
        "rationale": "Calm macro",
    }

    async def fake_llm(model: str, token: str, system: str, user: str) -> str:
        _ = (model, token, system, user)
        return json.dumps(payload)

    manager = AgentManager(llm_callable=fake_llm)
    agent = BaseAgent(manager, agent_name="macro_micro", timeout_s=2.0)
    result = await agent._invoke_structured(
        user="test",
        schema=MacroRiskAssessment,
        json_hint="{}",
    )
    assert result.severity == "LOW"


@pytest.mark.asyncio
async def test_invoke_structured_malformed_json_raises_degraded() -> None:
    async def fake_llm(model: str, token: str, system: str, user: str) -> str:
        _ = (model, token, system, user)
        return "not json"

    manager = AgentManager(llm_callable=fake_llm)
    agent = BaseAgent(manager, agent_name="macro_micro", timeout_s=2.0)
    with pytest.raises(AgentDegraded):
        await agent._invoke_structured(
            user="test",
            schema=MacroRiskAssessment,
            json_hint="{}",
        )


def test_extract_json_payload_strips_fences() -> None:
    raw = (
        '```json\n{"severity":"NONE","halt_scanner":false,'
        '"stop_loss_multiplier":1.0,"rationale":"ok"}\n```'
    )
    parsed = extract_json_payload(raw)
    assert parsed["severity"] == "NONE"
