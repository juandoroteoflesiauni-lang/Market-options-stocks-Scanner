"""Tests for audit DuckDB compaction and retention. # [PD-6][TH]"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.audit_duckdb_utils import (
    compact_bot_audit_payload,
    connect_audit_duckdb,
    payload_json_for_audit,
    prune_audit_cycles,
)
from backend.services.bingx_audit_store import BingXAuditEntry, BingXAuditStore


def test_compact_payload_strips_klines_and_marks_compact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_COMPACT_PAYLOAD", "true")
    payload = {
        "cycle_id": "c1",
        "executions": [{"symbol": "META-USDT", "ok": True}],
        "candidate_analyses": [
            {
                "venue_symbol": "META-USDT",
                "readiness_score": 0.8,
                "venue": {"klines": [{"close": 100.0}] * 500},
            }
        ],
        "l2_snapshots": {"META-USDT": {"bids": [[1, 2]]}},
    }
    compact = compact_bot_audit_payload(payload)
    assert compact["_audit_compact"] is True
    venue = compact["candidate_analyses"][0]["venue"]
    assert "klines" not in venue
    assert compact["l2_snapshots"]["_omitted"] is True
    assert compact["executions"][0]["symbol"] == "META-USDT"


def test_payload_json_smaller_than_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_COMPACT_PAYLOAD", "true")
    payload = {
        "candidate_analyses": [{"venue_symbol": "X", "venue": {"klines": [1] * 1000}}],
        "executions": [],
    }
    raw_len = len(json.dumps(payload))
    compact_len = len(payload_json_for_audit(payload))
    assert compact_len < raw_len


def test_prune_audit_cycles_retains_recent(tmp_path: Path) -> None:
    store = BingXAuditStore(tmp_path / "audit.duckdb")
    for idx in range(5):
        store.persist(
            BingXAuditEntry(
                started_at=f"2026-06-17T10:0{idx}:00Z",
                finished_at=f"2026-06-17T10:0{idx}:30Z",
                dry_run=True,
                universe=["META-USDT"],
                cycle_id=f"cycle_{idx}",
            )
        )
    assert store.count() == 5

    conn = connect_audit_duckdb(store.db_path, read_only=False)
    try:
        deleted = prune_audit_cycles(conn, "bingx_audit_cycles", retain_max=2)
    finally:
        conn.close()
    assert deleted == 3
    assert store.count() == 2


def test_bingx_store_uses_read_only_for_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_RETAIN_MAX_CYCLES", "100")
    store = BingXAuditStore(tmp_path / "audit.duckdb")
    store.persist(
        BingXAuditEntry(
            started_at="2026-06-17T10:00:00Z",
            finished_at="2026-06-17T10:01:00Z",
            dry_run=True,
            universe=["AAPL-USDT"],
        )
    )
    assert store.count() == 1
