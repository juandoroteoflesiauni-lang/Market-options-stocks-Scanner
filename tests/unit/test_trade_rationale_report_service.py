"""Unit tests for TradeRationaleReportService."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.audit.audit_complex_store import AgenticDecisionAuditEntry, AuditComplexStore
from backend.services.reports.trade_rationale_report_service import TradeRationaleReportService


@pytest.mark.asyncio
async def test_markdown_contains_consensus_and_technical() -> None:
    store = AuditComplexStore(db_path=":memory:")
    payload = {
        "correlation_id": "c1",
        "module": "alpaca",
        "symbol": "AAPL",
        "contract_symbol": "AAPL",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "final_decision": "EXECUTE",
        "quant_default_used": False,
        "committee": {
            "bull": {"thesis": "Momentum strong"},
            "bear": {"thesis": "Macro risk"},
            "verdict": {"rationale": "Execute half size", "decision": "EXECUTE"},
        },
    }
    entry = AgenticDecisionAuditEntry(
        module="alpaca",
        symbol="AAPL",
        contract_symbol="AAPL",
        final_decision="EXECUTE",
        payload=payload,
        correlation_id="c1",
    )
    event_id = store.persist_agentic_decision(entry)
    service = TradeRationaleReportService(store)
    report = await service.load_report(event_id)
    assert report is not None
    md = service.render_markdown(report)
    assert "AAPL" in md
    assert "Momentum strong" in md
    assert report.consensus_available is True


@pytest.mark.asyncio
async def test_missing_consensus_fallback() -> None:
    store = AuditComplexStore(db_path=":memory:")
    payload = {
        "correlation_id": "c2",
        "module": "bingx",
        "symbol": "BTC-USDT",
        "contract_symbol": "BTC-USDT",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "final_decision": "PASS",
        "quant_default_used": True,
    }
    entry = AgenticDecisionAuditEntry(
        module="bingx",
        symbol="BTC-USDT",
        contract_symbol="BTC-USDT",
        final_decision="PASS",
        payload=payload,
    )
    event_id = store.persist_agentic_decision(entry)
    service = TradeRationaleReportService(store)
    report = await service.load_report(event_id)
    assert report is not None
    assert report.consensus_available is False
    md = service.render_markdown(report)
    assert "unavailable" in md.lower()


@pytest.mark.asyncio
async def test_pdf_returns_bytes() -> None:
    store = AuditComplexStore(db_path=":memory:")
    entry = AgenticDecisionAuditEntry(
        module="alpaca",
        symbol="SPY",
        contract_symbol="SPY",
        final_decision="EXECUTE",
        payload={"created_at": datetime.now(tz=UTC).isoformat()},
    )
    event_id = store.persist_agentic_decision(entry)
    service = TradeRationaleReportService(store)
    report = await service.load_report(event_id)
    assert report is not None
    try:
        pdf = await service.render_pdf_bytes(report)
        assert pdf[:4] == b"%PDF"
    except RuntimeError as exc:
        pytest.skip(f"fpdf2 not installed: {exc}")
