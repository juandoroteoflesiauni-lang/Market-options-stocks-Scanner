"""Tests for BingXAuditStore — DuckDB-backed cycle audit persistence."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from backend.services.bingx_audit_store import BingXAuditEntry, BingXAuditStore, _new_cycle_id

# ── Helpers ────────────────────────────────────────────────────────────────────


def _store() -> BingXAuditStore:
    return BingXAuditStore(":memory:")


def _entry(**overrides: object) -> BingXAuditEntry:
    defaults: dict = {
        "started_at": "2026-05-21T00:00:00Z",
        "finished_at": "2026-05-21T00:01:00Z",
        "dry_run": True,
        "universe": ["BTC-USDT", "AAPL-USDT"],
    }
    defaults.update(overrides)
    return BingXAuditEntry(**defaults)  # type: ignore[arg-type]


def _cycle_result_mock(*, dry_run: bool = True) -> MagicMock:
    result = MagicMock()
    result.to_dict.return_value = {
        "started_at": "2026-05-21T00:00:00Z",
        "finished_at": "2026-05-21T00:01:00Z",
        "dry_run": dry_run,
        "universe": ["BTC-USDT"],
        "snapshots": [],
        "signals": [],
        "decisions": [],
        "plans": [],
        "executions": [],
    }
    return result


# ── _new_cycle_id ──────────────────────────────────────────────────────────────


def test_new_cycle_id_format() -> None:
    cid = _new_cycle_id()
    # Expected: "20260521T000000_abcdef12"
    parts = cid.split("_")
    assert len(parts) == 2
    ts_part, hex_part = parts
    assert len(ts_part) == 15  # YYYYMMDDTHHmmSS
    assert ts_part[8] == "T"
    assert len(hex_part) == 8
    assert hex_part.isalnum()


def test_new_cycle_id_unique() -> None:
    ids = {_new_cycle_id() for _ in range(50)}
    assert len(ids) == 50


# ── BingXAuditEntry ────────────────────────────────────────────────────────────


def test_entry_default_cycle_id_generated() -> None:
    e = _entry()
    assert e.cycle_id
    assert "_" in e.cycle_id


def test_entry_to_payload_includes_required_fields() -> None:
    e = _entry()
    payload = e.to_payload()
    for key in ("cycle_id", "started_at", "finished_at", "dry_run", "universe"):
        assert key in payload


def test_entry_to_payload_omits_none_optional_blocks() -> None:
    e = _entry()
    payload = e.to_payload()
    for block in (
        "candidate_analyses",
        "engine_decisions",
        "risk_decisions",
        "order_intents",
        "exchange_responses",
    ):
        assert block not in payload


def test_entry_to_payload_includes_optional_blocks_when_set() -> None:
    e = _entry(candidate_analyses=[{"sym": "X"}], order_intents=[{"id": "1"}])
    payload = e.to_payload()
    assert payload["candidate_analyses"] == [{"sym": "X"}]
    assert payload["order_intents"] == [{"id": "1"}]
    assert "engine_decisions" not in payload
    assert "risk_decisions" not in payload
    assert "exchange_responses" not in payload


def test_entry_from_cycle_result_populates_fields() -> None:
    mock_result = _cycle_result_mock(dry_run=False)
    e = BingXAuditEntry.from_cycle_result(mock_result)
    assert e.dry_run is False
    assert e.universe == ["BTC-USDT"]
    assert e.started_at == "2026-05-21T00:00:00Z"
    assert e.finished_at == "2026-05-21T00:01:00Z"
    assert e.snapshots == []
    assert e.signals == []
    assert e.decisions == []
    assert e.plans == []
    assert e.executions == []


def test_entry_from_cycle_result_generates_cycle_id() -> None:
    mock_result = _cycle_result_mock()
    e = BingXAuditEntry.from_cycle_result(mock_result)
    assert e.cycle_id
    assert "_" in e.cycle_id


# ── BingXAuditStore.persist ────────────────────────────────────────────────────


def test_persist_returns_cycle_id() -> None:
    store = _store()
    e = _entry()
    cid = store.persist(e)
    assert cid == e.cycle_id


def test_persist_stores_retrievable_cycle() -> None:
    store = _store()
    e = _entry()
    store.persist(e)
    retrieved = store.get_cycle(e.cycle_id)
    assert retrieved is not None
    assert retrieved["cycle_id"] == e.cycle_id
    assert retrieved["dry_run"] is True
    assert retrieved["universe"] == ["BTC-USDT", "AAPL-USDT"]


def test_persist_multiple_cycles() -> None:
    store = _store()
    e1 = _entry()
    e2 = _entry()
    store.persist(e1)
    store.persist(e2)
    assert store.count() == 2


def test_persist_replaces_on_same_cycle_id() -> None:
    store = _store()
    e = _entry()
    store.persist(e)
    # Mutate and re-persist with same cycle_id
    e2 = _entry(cycle_id=e.cycle_id, dry_run=False)
    store.persist(e2)
    assert store.count() == 1
    retrieved = store.get_cycle(e.cycle_id)
    assert retrieved is not None
    assert retrieved["dry_run"] is False


# ── BingXAuditStore.list_cycles ────────────────────────────────────────────────


def test_list_cycles_newest_first() -> None:
    store = _store()
    e1 = _entry(started_at="2026-05-21T00:00:00Z", finished_at="2026-05-21T00:01:00Z")
    e2 = _entry(started_at="2026-05-21T00:02:00Z", finished_at="2026-05-21T00:03:00Z")
    store.persist(e1)
    store.persist(e2)
    cycles = store.list_cycles()
    assert len(cycles) == 2
    assert cycles[0]["started_at"] == "2026-05-21T00:02:00Z"
    assert cycles[1]["started_at"] == "2026-05-21T00:00:00Z"


def test_list_cycles_no_payload_column() -> None:
    store = _store()
    store.persist(_entry())
    cycles = store.list_cycles()
    assert len(cycles) == 1
    assert "payload" not in cycles[0]


def test_list_cycles_includes_expected_fields() -> None:
    store = _store()
    e = _entry()
    store.persist(e)
    cycles = store.list_cycles()
    record = cycles[0]
    assert "cycle_id" in record
    assert "started_at" in record
    assert "finished_at" in record
    assert "dry_run" in record
    assert "universe" in record
    assert "created_at" in record


def test_list_cycles_universe_decoded_as_list() -> None:
    store = _store()
    e = _entry(universe=["A-USDT", "B-USDT"])
    store.persist(e)
    cycles = store.list_cycles()
    assert cycles[0]["universe"] == ["A-USDT", "B-USDT"]


def test_list_cycles_dry_run_is_bool() -> None:
    store = _store()
    store.persist(_entry(dry_run=True))
    assert store.list_cycles()[0]["dry_run"] is True


def test_list_cycles_respects_limit() -> None:
    store = _store()
    for _ in range(10):
        store.persist(_entry())
    assert len(store.list_cycles(limit=3)) == 3


def test_list_cycles_limit_clamped_to_1() -> None:
    store = _store()
    store.persist(_entry())
    assert len(store.list_cycles(limit=0)) == 1


def test_list_cycles_limit_clamped_to_500() -> None:
    store = _store()
    for _ in range(5):
        store.persist(_entry())
    assert len(store.list_cycles(limit=9999)) == 5


def test_list_cycles_empty_store() -> None:
    store = _store()
    assert store.list_cycles() == []


# ── BingXAuditStore.get_cycle ──────────────────────────────────────────────────


def test_list_operations_flattens_executions_with_pnl_and_reasons() -> None:
    store = _store()
    entry = _entry(
        cycle_id="cycle-a",
        started_at="2026-05-21T00:02:00Z",
        dry_run=True,
        decisions=[
            {
                "symbol": "BTC-USDT",
                "suitability": "ALLOW",
                "probability": 0.72,
                "reason_codes": ["VSA_SPIKE", "L2_IMBALANCE"],
            }
        ],
        risk_decisions=[
            {
                "symbol": "BTC-USDT",
                "authorized": True,
                "notional_usdt": 25.0,
                "reason_codes": ["RISK_OK"],
            }
        ],
        order_intents=[
            {
                "symbol": "BTC-USDT",
                "side": "BUY",
                "quantity": 0.001,
                "reference_price": 50_000.0,
            }
        ],
        executions=[
            {
                "symbol": "BTC-USDT",
                "side": "BUY",
                "ok": True,
                "dry_run": True,
                "venue_order_id": "dry_BTC",
                "realized_pnl_usdt": 1.25,
            }
        ],
    )
    store.persist(entry)

    operations = store.list_operations(limit=10)

    assert len(operations) == 1
    op = operations[0]
    assert op["cycle_id"] == "cycle-a"
    assert op["event_type"] == "execution"
    assert op["symbol"] == "BTC-USDT"
    assert op["side"] == "BUY"
    assert op["dry_run"] is True
    assert op["execution_ok"] is True
    assert op["notional_usdt"] == 25.0
    assert op["quantity"] == 0.001
    assert op["reference_price"] == 50_000.0
    assert op["realized_pnl_usdt"] == 1.25
    assert op["pnl_pct"] == 5.0
    assert op["reason_codes"] == ["VSA_SPIKE", "L2_IMBALANCE", "RISK_OK"]


def test_list_operations_records_blocked_decisions_without_execution() -> None:
    store = _store()
    entry = _entry(
        cycle_id="cycle-block",
        started_at="2026-05-21T00:03:00Z",
        dry_run=True,
        decisions=[
            {
                "symbol": "SOL-USDT",
                "suitability": "BLOCK",
                "probability": 0.41,
                "reason_codes": ["NO_VOLUME_SPIKE"],
            }
        ],
        risk_decisions=[
            {
                "symbol": "SOL-USDT",
                "authorized": False,
                "reason_codes": ["RISK_REJECTED"],
            }
        ],
    )
    store.persist(entry)

    operations = store.list_operations(limit=10)

    assert len(operations) == 1
    assert operations[0]["event_type"] == "decision"
    assert operations[0]["symbol"] == "SOL-USDT"
    assert operations[0]["suitability"] == "BLOCK"
    assert operations[0]["authorized"] is False
    assert operations[0]["execution_ok"] is None
    assert operations[0]["realized_pnl_usdt"] is None
    assert operations[0]["reason_codes"] == ["NO_VOLUME_SPIKE", "RISK_REJECTED"]


def test_get_cycle_returns_none_for_unknown_id() -> None:
    store = _store()
    assert store.get_cycle("nonexistent") is None


def test_get_cycle_returns_full_payload() -> None:
    store = _store()
    e = _entry(candidate_analyses=[{"sym": "BTC-USDT", "score": 0.9}])
    store.persist(e)
    payload = store.get_cycle(e.cycle_id)
    assert payload is not None
    assert payload["candidate_analyses"] == [{"sym": "BTC-USDT", "score": 0.9}]


def test_get_cycle_payload_is_dict_not_str() -> None:
    store = _store()
    e = _entry()
    store.persist(e)
    payload = store.get_cycle(e.cycle_id)
    assert isinstance(payload, dict)


# ── BingXAuditStore.count ──────────────────────────────────────────────────────


def test_count_empty() -> None:
    store = _store()
    assert store.count() == 0


def test_count_after_persists() -> None:
    store = _store()
    for _ in range(7):
        store.persist(_entry())
    assert store.count() == 7


# ── Secrets safety ─────────────────────────────────────────────────────────────


def test_payload_does_not_contain_obvious_secrets() -> None:
    """Caller must scrub secrets before calling persist(); the store itself
    never injects them. This test verifies no secrets leak from the test
    fixtures themselves."""
    store = _store()
    e = _entry(
        snapshots=[{"symbol": "BTC-USDT", "price": 60000.0}],
        signals=[{"direction": "LONG"}],
    )
    store.persist(e)
    payload = store.get_cycle(e.cycle_id)
    assert payload is not None
    payload_str = json.dumps(payload)
    for forbidden in ("api_key", "secret_key", "password", "token"):
        assert forbidden not in payload_str.lower()


# ── Independent stores (isolation) ────────────────────────────────────────────


def test_two_in_memory_stores_are_independent() -> None:
    s1 = _store()
    s2 = _store()
    s1.persist(_entry())
    assert s1.count() == 1
    assert s2.count() == 0
