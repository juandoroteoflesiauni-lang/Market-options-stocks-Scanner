"""Unit tests for agentic Pydantic models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.domain.agentic_models import AgentEnvelope, MacroRiskAssessment, TraderVerdict


def test_trader_verdict_modifier_bounds() -> None:
    verdict = TraderVerdict(
        decision="EXECUTE",
        confidence_score=80,
        recommended_position_size_modifier=0.5,
        rationale="Sized down on liquidity",
    )
    assert verdict.recommended_position_size_modifier == 0.5


def test_trader_verdict_rejects_modifier_above_one() -> None:
    with pytest.raises(ValidationError):
        TraderVerdict(
            decision="EXECUTE",
            confidence_score=80,
            recommended_position_size_modifier=1.5,
            rationale="invalid",
        )


def test_macro_risk_stop_multiplier_bounds() -> None:
    with pytest.raises(ValidationError):
        MacroRiskAssessment(
            severity="HIGH",
            stop_loss_multiplier=5.0,
            rationale="too wide",
        )


def test_agent_envelope_latency_non_negative() -> None:
    envelope = AgentEnvelope(
        agent_name="test",
        model="fake",
        provider="gemini",
        latency_ms=12.5,
    )
    assert envelope.latency_ms >= 0.0


def test_macro_risk_valid_assessment() -> None:
    assessment = MacroRiskAssessment(
        severity="CRITICAL",
        imminent_event="FOMC",
        minutes_to_event=30,
        halt_scanner=True,
        stop_loss_multiplier=1.5,
        rationale="Imminent macro event",
    )
    assert assessment.halt_scanner is True


def test_agentic_trade_decision_event_roundtrip() -> None:
    from backend.domain.agentic_models import AgenticTradeDecisionEvent

    event = AgenticTradeDecisionEvent(
        correlation_id="corr-1",
        module="alpaca",
        symbol="AAPL",
        contract_symbol="AAPL240119C00150000",
        created_at=datetime.now(tz=UTC),
        final_decision="PASS",
        quant_default_used=True,
    )
    dumped = event.model_dump(mode="json")
    restored = AgenticTradeDecisionEvent.model_validate(dumped)
    assert restored.final_decision == "PASS"
    assert restored.quant_default_used is True
