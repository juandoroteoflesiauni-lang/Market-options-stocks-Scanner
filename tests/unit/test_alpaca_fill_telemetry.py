"""Tests for telemetry-only fill/slippage hook."""

from __future__ import annotations

from decimal import Decimal

from backend.domain.alpaca_models import EquityOrderIntent, EquityRiskDecision
from backend.services.telemetry.fill_slippage_telemetry import log_fill_slippage_telemetry


def test_telemetry_record_well_formed_without_contract() -> None:
    record = log_fill_slippage_telemetry(
        module="alpaca_equity",
        symbol="AAPL",
        side="buy",
        quantity=Decimal("10"),
        limit_or_market_price=Decimal("150.25"),
    )
    assert record["symbol"] == "AAPL"
    assert record["quantity"] == "10"


def test_order_intent_unchanged_after_telemetry() -> None:
    intent = EquityOrderIntent(
        symbol="AAPL",
        quantity=5,
        reference_price=150.0,
        notional_usd=750.0,
        client_order_id="test-1",
    )
    decision = EquityRiskDecision(authorized=True, intent=intent, idempotency_key="k1")
    before = decision.model_dump_json()
    log_fill_slippage_telemetry(
        module="alpaca_equity",
        symbol=intent.symbol,
        side="buy",
        quantity=Decimal(str(intent.quantity)),
        limit_or_market_price=Decimal(str(intent.reference_price)),
    )
    assert decision.model_dump_json() == before
