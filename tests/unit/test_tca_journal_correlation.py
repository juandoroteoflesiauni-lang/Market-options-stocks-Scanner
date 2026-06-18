"""Tests Blueprint P0 Turno A — correlation_id + decision_score en trade_journal. # [PD-6]

Cubre (AAA):
- roundtrip persist→list con correlation_id y decision_score reales;
- migración idempotente que agrega correlation_id a una DB legacy sin la columna;
- persist_equity_tca_execution propaga score/correlation_id y produce IS≠0 con slippage.
"""

from __future__ import annotations

import duckdb

from backend.services.tca.journal_tca import persist_equity_tca_execution
from backend.services.trade_journal_service import (
    TradeJournalEntry,
    _migrate_trade_journal_tca,
    init_trade_journal_table,
    list_trades,
    persist_trade_execution,
)


def _entry(**over):
    base = {
        "execution_timestamp": "2026-06-17T20:00:00+00:00",
        "symbol": "AAPL-USDT",
        "side": "BUY",
        "quantity": 10.0,
        "notional_usdt": 1000.0,
        "entry_price": 100.0,
        "decision_score": 0.72,
        "reason_codes": ["full_confluence"],
        "venue_order_id": "VO-1",
        "realized_pnl": 0.0,
        "institutional_research_snapshot": {"k": "v"},
        "engine_decision_payload": {"d": 1},
        "dry_run": False,
        "cycle_id": "cyc-1",
        "correlation_id": "corr-123",
    }
    base.update(over)
    return TradeJournalEntry(**base)


def test_persist_roundtrip_with_score_and_id(tmp_path):
    # ARRANGE
    db = tmp_path / "j.duckdb"
    init_trade_journal_table(db)
    # ACT
    ok = persist_trade_execution(_entry(), db)
    rows = list_trades(db, limit=10)
    # ASSERT
    assert ok is True
    assert len(rows) == 1
    assert rows[0]["correlation_id"] == "corr-123"
    assert rows[0]["decision_score"] == 0.72
    assert rows[0]["dry_run"] is False


def test_migration_idempotent_adds_correlation_id(tmp_path):
    # ARRANGE — DB legacy con trade_journal SIN correlation_id
    db = tmp_path / "legacy.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(
        """
        CREATE TABLE trade_journal (
            execution_timestamp VARCHAR, symbol VARCHAR, side VARCHAR,
            quantity DOUBLE, notional_usdt DOUBLE, entry_price DOUBLE,
            decision_score DOUBLE, reason_codes JSON, venue_order_id VARCHAR,
            realized_pnl DOUBLE, institutional_research_snapshot JSON,
            engine_decision_payload JSON, dry_run BOOLEAN, cycle_id VARCHAR,
            _created_at VARCHAR
        )
        """
    )
    # ACT — migrar dos veces (idempotente)
    _migrate_trade_journal_tca(conn)
    _migrate_trade_journal_tca(conn)
    cols = [
        r[0]
        for r in conn.execute(
            "select column_name from information_schema.columns where table_name='trade_journal'"
        ).fetchall()
    ]
    conn.close()
    # ASSERT
    assert "correlation_id" in cols


def test_equity_tca_persists_score_id_and_nonzero_is(tmp_path):
    # ARRANGE
    db = tmp_path / "eq.duckdb"
    init_trade_journal_table(db)
    # ACT — BUY con fill peor que decisión → IS adverso (>0)
    ok = persist_equity_tca_execution(
        symbol="MSFT",
        side="BUY",
        quantity=5.0,
        decision_price=100.0,
        fill_price=101.0,
        route="R1",
        cycle_id="cyc-9",
        venue_order_id="VO-9",
        dry_run=False,
        decision_score=0.66,
        correlation_id="corr-eq-9",
        db_path=db,
    )
    rows = list_trades(db, limit=10)
    # ASSERT
    assert ok is True
    assert len(rows) == 1
    assert rows[0]["correlation_id"] == "corr-eq-9"
    assert rows[0]["decision_score"] == 0.66
    assert rows[0]["implementation_shortfall_bps"] > 0.0  # 101 vs 100 BUY = coste
