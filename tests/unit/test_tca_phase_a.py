"""Tests TCA — implementation shortfall y reporte EOD. # [PD-6][TH]"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.services.tca.implementation_shortfall import compute_implementation_shortfall
from backend.services.tca.journal_tca import persist_equity_tca_execution
from backend.services.tca.tca_eod_report import build_tca_eod_report
from backend.services.trade_journal_service import init_trade_journal_table, list_trades


def test_buy_adverse_slippage_positive_bps() -> None:
    metrics = compute_implementation_shortfall(
        route="R1",
        side="BUY",
        quantity=10.0,
        decision_price=100.0,
        fill_price=100.5,
        decision_timestamp="2026-06-17T15:00:00+00:00",
        execution_timestamp="2026-06-17T15:00:02+00:00",
    )
    assert metrics.implementation_shortfall_bps == pytest.approx(50.0)
    assert metrics.slippage_usd == pytest.approx(5.0)
    assert metrics.delay_ms == 2000


def test_sell_adverse_slippage() -> None:
    metrics = compute_implementation_shortfall(
        route="BINGX",
        side="SELL",
        quantity=2.0,
        decision_price=50.0,
        fill_price=49.0,
        decision_timestamp="2026-06-17T15:00:00+00:00",
        execution_timestamp="2026-06-17T15:00:01+00:00",
    )
    assert metrics.implementation_shortfall_bps == pytest.approx(200.0)
    assert metrics.slippage_usd == pytest.approx(2.0)


def test_tca_eod_report_groups_by_route(tmp_path: Path) -> None:
    db = tmp_path / "journal.duckdb"
    init_trade_journal_table(db)
    today = datetime.now(tz=UTC).date().isoformat()
    assert persist_equity_tca_execution(
        symbol="AAPL",
        side="BUY",
        quantity=5.0,
        decision_price=200.0,
        fill_price=200.4,
        route="R1",
        cycle_id="c1",
        venue_order_id="alp-1",
        dry_run=True,
        decision_timestamp=f"{today}T14:00:00+00:00",
        execution_timestamp=f"{today}T14:00:01+00:00",
        db_path=db,
    )
    report = build_tca_eod_report(db)
    assert report["trades_with_tca"] == 1
    assert "R1" in report["by_route"]
    assert report["by_route"]["R1"]["trade_count"] == 1
    assert report["by_route"]["R1"]["avg_is_bps"] == pytest.approx(20.0)


def test_journal_lists_tca_columns(tmp_path: Path) -> None:
    db = tmp_path / "journal.duckdb"
    init_trade_journal_table(db)
    today = datetime.now(tz=UTC).date().isoformat()
    persist_equity_tca_execution(
        symbol="MSFT",
        side="BUY",
        quantity=1.0,
        decision_price=400.0,
        fill_price=400.0,
        route="R2",
        cycle_id="c2",
        venue_order_id=None,
        dry_run=True,
        execution_timestamp=f"{today}T16:00:00+00:00",
        db_path=db,
    )
    rows = list_trades(db, limit=5)
    assert rows[0]["route"] == "R2"
    assert rows[0]["implementation_shortfall_bps"] == pytest.approx(0.0)
