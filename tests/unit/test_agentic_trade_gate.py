"""Unit tests for agentic trade gate and execution bridge."""

from __future__ import annotations

import pytest

from backend.domain.agentic_models import (
    AgentEnvelope,
    ExecutionCommitteeResult,
    MacroRiskAssessment,
    MacroRiskResult,
    ResearcherArgument,
    TraderVerdict,
)
from backend.domain.alpaca_models import EquityOrderIntent, EquityRiskDecision
from backend.services.agentic_data_facade import AgenticDataFacade
from backend.services.agentic_execution_bridge import apply_agentic_gate_to_equity_decisions
from backend.services.agentic_trade_gate import AgenticTradeGate


class _FakeFacade(AgenticDataFacade):
    def __init__(self) -> None:
        super().__init__(_NoFMP())  # type: ignore[arg-type]


class _NoFMP:
    async def get_economic_calendar(self, *args: object, **kwargs: object) -> list:
        return []

    async def get_treasury_rates(self, *args: object, **kwargs: object) -> list:
        return []

    async def get_economic_indicator(self, *args: object, **kwargs: object) -> list:
        return []


def _committee_result(
    decision: str, modifier: float, degraded: bool = False
) -> ExecutionCommitteeResult:
    return ExecutionCommitteeResult(
        contract_symbol="AAPL",
        bull=ResearcherArgument(stance="BULLISH", thesis="up", key_risks=[]),
        bear=ResearcherArgument(stance="BEARISH", thesis="down", key_risks=[]),
        verdict=TraderVerdict(
            decision=decision,  # type: ignore[arg-type]
            confidence_score=70,
            recommended_position_size_modifier=modifier,
            rationale="test",
        ),
        envelope=AgentEnvelope(
            agent_name="orchestrator",
            model="fake",
            provider="gemini",
            latency_ms=1.0,
            degraded=degraded,
        ),
    )


@pytest.mark.asyncio
async def test_gate_execute_scales_quantity(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = AgenticTradeGate(data_facade=_FakeFacade())

    async def fake_evaluate(**kwargs: object) -> object:
        _ = kwargs
        from backend.services.agentic_trade_gate import AgenticGateOutcome

        return AgenticGateOutcome(
            allow_execute=True,
            size_modifier=0.5,
            final_decision="EXECUTE",
            quant_default_used=False,
            committee=_committee_result("EXECUTE", 0.5),
            correlation_id="c1",
        )

    monkeypatch.setattr(gate, "evaluate_trade", fake_evaluate)

    intent = EquityOrderIntent(
        symbol="AAPL",
        quantity=10,
        reference_price=100.0,
        notional_usd=1000.0,
        client_order_id="qa-aapl-1",
        cycle_id="cycle-1",
        route="priority",
    )
    decision = EquityRiskDecision(
        authorized=True,
        intent=intent,
        idempotency_key="qa-aapl-1",
        adjusted_quantity=10,
    )
    out = await apply_agentic_gate_to_equity_decisions([decision], gate=gate)
    assert len(out) == 1
    assert out[0].adjusted_quantity == 5


@pytest.mark.asyncio
async def test_gate_pass_aborts_trade(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = AgenticTradeGate(data_facade=_FakeFacade())

    async def fake_evaluate(**kwargs: object) -> object:
        _ = kwargs
        from backend.services.agentic_trade_gate import AgenticGateOutcome

        return AgenticGateOutcome(
            allow_execute=False,
            size_modifier=0.0,
            final_decision="PASS",
            quant_default_used=False,
            committee=_committee_result("PASS", 0.0),
            correlation_id="c2",
        )

    monkeypatch.setattr(gate, "evaluate_trade", fake_evaluate)
    intent = EquityOrderIntent(
        symbol="AAPL",
        quantity=10,
        reference_price=100.0,
        notional_usd=1000.0,
        client_order_id="qa-aapl-2",
        cycle_id="cycle-1",
        route="priority",
    )
    decision = EquityRiskDecision(
        authorized=True,
        intent=intent,
        idempotency_key="qa-aapl-2",
        adjusted_quantity=10,
    )
    out = await apply_agentic_gate_to_equity_decisions([decision], gate=gate)
    assert out == []


def test_apply_size_modifier_floor() -> None:
    assert AgenticTradeGate.apply_size_modifier(11, 0.5) == 5


def test_macro_risk_result_defaults() -> None:
    result = MacroRiskResult(
        assessment=MacroRiskAssessment(severity="NONE", rationale="ok"),
        envelope=AgentEnvelope(
            agent_name="macro_micro",
            model="fake",
            provider="gemini",
            latency_ms=0.0,
            degraded=True,
        ),
    )
    assert result.envelope.degraded is True
