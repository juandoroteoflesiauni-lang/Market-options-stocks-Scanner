"""AAA unit tests for backend.domain.alpaca_models. # [TH][IM]"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.domain.alpaca_models import (
    AlpacaCandidateAnalysis,
    AlpacaDecision,
    EquityCycleResult,
    EquityOrderIntent,
    EquityRiskDecision,
)


def test_equity_order_intent_valid_is_immutable() -> None:
    # ARRANGE
    intent = EquityOrderIntent(
        symbol="AAPL",
        quantity=10,
        reference_price=190.0,
        notional_usd=1900.0,
        client_order_id="qa-AAPL-1",
    )
    # ACT / ASSERT
    with pytest.raises(ValidationError):
        intent.quantity = 20  # frozen → immutable


def test_equity_order_intent_rejects_zero_quantity() -> None:
    # ARRANGE / ACT / ASSERT
    with pytest.raises(ValidationError):
        EquityOrderIntent(
            symbol="AAPL",
            quantity=0,
            reference_price=190.0,
            notional_usd=0.0,
            client_order_id="qa-AAPL-1",
        )


def test_equity_order_intent_rejects_non_buy_side() -> None:
    # ARRANGE / ACT / ASSERT
    with pytest.raises(ValidationError):
        EquityOrderIntent(
            symbol="AAPL",
            side="SELL",  # type: ignore[arg-type]
            quantity=10,
            reference_price=190.0,
            notional_usd=1900.0,
            client_order_id="qa-AAPL-1",
        )


def test_alpaca_decision_long_only_accepts_long() -> None:
    # ARRANGE / ACT
    decision = AlpacaDecision(
        symbol="MSFT", decision="ALLOW", direction="LONG", score=0.8
    )
    # ASSERT
    assert decision.direction == "LONG"


def test_alpaca_decision_rejects_short_direction() -> None:
    # ARRANGE / ACT / ASSERT
    with pytest.raises(ValidationError):
        AlpacaDecision(
            symbol="MSFT",
            decision="ALLOW",
            direction="SHORT",  # type: ignore[arg-type]
            score=0.8,
        )


def test_equity_cycle_result_to_dict_is_json_safe() -> None:
    # ARRANGE
    analysis = AlpacaCandidateAnalysis(symbol="AAPL", timestamp="2026-06-12T00:00:00Z")
    intent = EquityOrderIntent(
        symbol="AAPL",
        quantity=5,
        reference_price=190.0,
        notional_usd=950.0,
        client_order_id="qa-AAPL-1",
    )
    risk = EquityRiskDecision(authorized=True, intent=intent, idempotency_key="k1")
    result = EquityCycleResult(
        started_at="t0",
        finished_at="t1",
        universe=("AAPL", "MSFT"),
        prefiltered=("AAPL",),
        analyses=(analysis,),
        risk_decisions=(risk,),
    )
    # ACT
    payload = result.to_dict()
    # ASSERT
    assert payload["universe"] == ["AAPL", "MSFT"]
    assert payload["risk_decisions"][0]["intent"]["symbol"] == "AAPL"
