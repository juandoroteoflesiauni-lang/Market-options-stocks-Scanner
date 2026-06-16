"""Unit tests for ExecutionCommittee."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.domain.agentic_models import OptionsContractContext
from backend.services.agents.execution_committee import ExecutionCommittee
from backend.services.ai_core.agent_manager import AgentManager


@pytest.mark.asyncio
async def test_committee_runs_bull_bear_concurrently() -> None:
    calls: list[str] = []

    async def fake_llm(model: str, token: str, system: str, user: str) -> str:
        _ = (model, token, system)
        if "BULLISH" in user and "Argue BULLISH" in user:
            calls.append("bull")
            await asyncio.sleep(0.05)
            return json.dumps({"stance": "BULLISH", "thesis": "Momentum", "key_risks": ["macro"]})
        if "BEARISH" in user and "Argue BEARISH" in user:
            calls.append("bear")
            await asyncio.sleep(0.05)
            return json.dumps({"stance": "BEARISH", "thesis": "Overbought", "key_risks": ["gamma"]})
        return json.dumps(
            {
                "decision": "EXECUTE",
                "confidence_score": 70,
                "recommended_position_size_modifier": 0.5,
                "rationale": "Proceed with half size",
            }
        )

    manager = AgentManager(llm_callable=fake_llm)
    committee = ExecutionCommittee(manager, timeout_s=2.0)
    context = OptionsContractContext(
        contract_symbol="AAPL240119C00150000",
        underlying_ticker="AAPL",
        option_type="CALL",
        strike="150",
        implied_volatility=0.25,
        delta=0.55,
        gamma=0.02,
        open_interest=5000,
        volume=1000,
        composite_score=80.0,
    )
    result = await committee.deliberate(
        contract_symbol=context.contract_symbol,
        symbol="AAPL",
        options_context=context,
        options_analysis=None,
        macro_risk=None,
    )
    assert "bull" in calls and "bear" in calls
    assert result.verdict.decision == "EXECUTE"
    assert result.verdict.recommended_position_size_modifier == 0.5


@pytest.mark.asyncio
async def test_committee_degraded_returns_pass() -> None:
    async def bad_llm(model: str, token: str, system: str, user: str) -> str:
        _ = (model, token, system, user)
        return "invalid"

    manager = AgentManager(llm_callable=bad_llm)
    committee = ExecutionCommittee(manager, timeout_s=1.0)
    result = await committee.deliberate(
        contract_symbol="AAPL",
        symbol="AAPL",
        options_context=None,
        options_analysis=None,
        macro_risk=None,
    )
    assert result.verdict.decision == "PASS"
    assert result.envelope.degraded is True
