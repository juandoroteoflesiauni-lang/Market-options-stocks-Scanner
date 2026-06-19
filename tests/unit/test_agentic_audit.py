"""Unit tests for agentic audit persistence."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.audit.audit_complex_store import AuditComplexStore
from backend.audit.hooks import audit_agentic_decision
from backend.domain.agentic_models import AgenticTradeDecisionEvent


@pytest.mark.asyncio
async def test_audit_agentic_decision_round_trip() -> None:
    store = AuditComplexStore(db_path=":memory:")
    event = AgenticTradeDecisionEvent(
        correlation_id="corr-test",
        module="bingx",
        symbol="BTC-USDT",
        contract_symbol="BTC-USDT",
        created_at=datetime.now(tz=UTC),
        final_decision="EXECUTE",
        quant_default_used=False,
    )

    import backend.audit.hooks as hooks

    original_get = hooks._get_store
    hooks._get_store = lambda: store  # type: ignore[assignment]
    try:
        event_id = await audit_agentic_decision(event=event)
        assert event_id is not None
        loaded = store.get_agentic_decision(event_id)
        assert loaded is not None
        assert loaded["final_decision"] == "EXECUTE"
        assert loaded["payload"]["correlation_id"] == "corr-test"
    finally:
        hooks._get_store = original_get  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_audit_agentic_decision_never_raises_on_store_failure() -> None:
    class _BrokenStore:
        def persist_agentic_decision(self, entry: object) -> str:
            raise RuntimeError("db down")

    import backend.audit.hooks as hooks

    original_get = hooks._get_store
    hooks._get_store = lambda: _BrokenStore()  # type: ignore[assignment]
    try:
        event = AgenticTradeDecisionEvent(
            correlation_id="x",
            module="alpaca",
            symbol="AAPL",
            contract_symbol="AAPL",
            created_at=datetime.now(tz=UTC),
            final_decision="PASS",
        )
        result = await audit_agentic_decision(event=event)
        assert result is None
    finally:
        hooks._get_store = original_get  # type: ignore[assignment]
