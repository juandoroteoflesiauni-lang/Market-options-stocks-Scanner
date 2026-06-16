from __future__ import annotations
from typing import Any
"""Tests for AuditComplexStore — DuckDB-backed unified audit subsystem."""


import json

from backend.audit.audit_complex_store import (
    ApiCallAuditEntry,
    AuditComplexStore,
    ErrorAuditEntry,
    LogAuditEntry,
    ProcessSnapshotEntry,
    _new_id,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def _store() -> AuditComplexStore:
    return AuditComplexStore(":memory:")


def _api_call_entry(**overrides: Any) -> ApiCallAuditEntry:
    defaults: dict[str, Any] = {
        "module": "scanner",
        "provider": "fmp",
        "endpoint": "/v3/quote/AAPL",
        "status": "success",
        "duration_ms": 145.2,
        "estimated_cost": 0.001,
    }
    defaults.update(overrides)
    return ApiCallAuditEntry(**defaults)


def _snapshot_entry(**overrides: Any) -> ProcessSnapshotEntry:
    defaults: dict[str, Any] = {
        "module": "bingx",
        "symbol": "BTC-USDT",
        "indicators": {"rsi": 55.0, "macd": 0.5},
    }
    defaults.update(overrides)
    return ProcessSnapshotEntry(**defaults)


def _error_entry(**overrides: Any) -> ErrorAuditEntry:
    defaults: dict[str, Any] = {
        "module": "scanner",
        "severity": "error",
        "error_type": "API_TIMEOUT",
        "message": "Request timed out after 30s",
    }
    defaults.update(overrides)
    return ErrorAuditEntry(**defaults)


def _log_entry(**overrides: Any) -> LogAuditEntry:
    defaults: dict[str, Any] = {
        "level": "INFO",
        "module": "system",
        "logger_name": "test_logger",
        "message": "Started cycle",
    }
    defaults.update(overrides)
    return LogAuditEntry(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# _new_id
# ═══════════════════════════════════════════════════════════════════════════════


def test_new_id_format() -> None:
    nid = _new_id("test_")
    assert nid.startswith("test_")
    ts_hex = nid.split("_", 1)[1]
    assert "_" in ts_hex  # timestamp_hex8
    hex_part = ts_hex.split("_")[1]
    assert len(hex_part) == 8
    assert hex_part.isalnum()


def test_new_id_default_prefix() -> None:
    nid = _new_id()
    assert nid  # non-empty


def test_new_id_unique() -> None:
    ids = {_new_id() for _ in range(50)}
    assert len(ids) == 50


# ═══════════════════════════════════════════════════════════════════════════════
# ApiCallAuditEntry — to_row
# ═══════════════════════════════════════════════════════════════════════════════


def test_api_call_entry_to_row_includes_all_fields() -> None:
    entry = _api_call_entry()
    row = entry.to_row()
    assert len(row) == 16
    assert row[0] == entry.call_id
    assert row[1] == entry.timestamp
    assert row[2] == "scanner"
    assert row[3] == "fmp"
    assert row[4] == "/v3/quote/AAPL"


def test_api_call_entry_to_row_json_fields() -> None:
    entry = _api_call_entry(request_context={"retry": 1})
    row = entry.to_row()
    ctx = json.loads(row[14])
    assert ctx["retry"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# ProcessSnapshotEntry — to_row
# ═══════════════════════════════════════════════════════════════════════════════


def test_snapshot_entry_to_row_includes_all_fields() -> None:
    entry = _snapshot_entry()
    row = entry.to_row()
    assert len(row) == 14
    assert row[0] == entry.snapshot_id
    assert row[2] == "bingx"
    assert row[3] == "BTC-USDT"


def test_snapshot_entry_to_row_serializes_dicts() -> None:
    entry = _snapshot_entry(decisions={"direction": "LONG", "confidence": 0.8})
    row = entry.to_row()
    indicators = json.loads(row[6])
    assert indicators["rsi"] == 55.0
    decisions = json.loads(row[10])  # index 10 = decisions in to_row
    assert decisions["direction"] == "LONG"


# ═══════════════════════════════════════════════════════════════════════════════
# ErrorAuditEntry — to_row
# ═══════════════════════════════════════════════════════════════════════════════


def test_error_entry_to_row_includes_all_fields() -> None:
    entry = _error_entry()
    row = entry.to_row()
    assert len(row) == 13
    assert row[0] == entry.error_id
    assert row[2] == "scanner"
    assert row[3] == "error"


def test_error_entry_to_row_with_stack_trace() -> None:
    entry = _error_entry(stack_trace="Traceback (most recent call last):\n...")
    row = entry.to_row()
    assert "Traceback" in row[6]


# ═══════════════════════════════════════════════════════════════════════════════
# LogAuditEntry — to_row
# ═══════════════════════════════════════════════════════════════════════════════


def test_log_entry_to_row_includes_all_fields() -> None:
    entry = _log_entry()
    row = entry.to_row()
    assert len(row) == 10
    assert row[0] == entry.log_id
    assert row[2] == "INFO"
    assert row[3] == "system"


def test_log_entry_to_row_tags_serialized() -> None:
    entry = _log_entry(tags=["startup", "critical"])
    row = entry.to_row()
    tags = json.loads(row[9])
    assert tags == ["startup", "critical"]


# ═══════════════════════════════════════════════════════════════════════════════
# AuditComplexStore — schema
# ═══════════════════════════════════════════════════════════════════════════════


def test_store_creates_all_tables() -> None:
    store = _store()
    with store._connect() as con:
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    names = {r[0] for r in tables}
    for expected in (
        "audit_api_calls",
        "audit_process_snapshots",
        "audit_errors",
        "audit_logs",
    ):
        assert expected in names


def test_in_memory_store_is_not_persistent() -> None:
    store = _store()
    assert not store.is_persistent


# ═══════════════════════════════════════════════════════════════════════════════
# AuditComplexStore — persist_api_call / list_api_calls
# ═══════════════════════════════════════════════════════════════════════════════


def test_persist_api_call_returns_call_id() -> None:
    store = _store()
    entry = _api_call_entry()
    cid = store.persist_api_call(entry)
    assert cid == entry.call_id


def test_persist_api_call_stores_retrievable() -> None:
    store = _store()
    entry = _api_call_entry()
    store.persist_api_call(entry)
    calls = store.list_api_calls()
    assert len(calls) == 1
    assert calls[0]["call_id"] == entry.call_id
    assert calls[0]["module"] == "scanner"
    assert calls[0]["status"] == "success"


def test_list_api_calls_empty() -> None:
    store = _store()
    assert store.list_api_calls() == []


def test_list_api_calls_newest_first() -> None:
    store = _store()
    e1 = _api_call_entry(timestamp="2026-01-01T00:00:00", duration_ms=100.0)
    e2 = _api_call_entry(timestamp="2026-06-01T00:00:00", duration_ms=200.0)
    store.persist_api_call(e1)
    store.persist_api_call(e2)
    calls = store.list_api_calls()
    assert len(calls) == 2
    assert calls[0]["call_id"] == e2.call_id
    assert calls[1]["call_id"] == e1.call_id


def test_list_api_calls_filters_by_module() -> None:
    store = _store()
    store.persist_api_call(_api_call_entry(module="scanner"))
    store.persist_api_call(_api_call_entry(module="bingx", provider="bingx", endpoint="/trade"))
    calls = store.list_api_calls(module="bingx")
    assert len(calls) == 1
    assert calls[0]["module"] == "bingx"


def test_list_api_calls_filters_by_provider() -> None:
    store = _store()
    store.persist_api_call(_api_call_entry(provider="fmp"))
    store.persist_api_call(_api_call_entry(provider="alpaca"))
    calls = store.list_api_calls(provider="alpaca")
    assert len(calls) == 1


def test_list_api_calls_filters_by_status() -> None:
    store = _store()
    store.persist_api_call(_api_call_entry(status="success"))
    store.persist_api_call(_api_call_entry(status="error"))
    calls = store.list_api_calls(status="error")
    assert len(calls) == 1
    assert calls[0]["status"] == "error"


def test_list_api_calls_limit_clamping() -> None:
    store = _store()
    for _ in range(5):
        store.persist_api_call(_api_call_entry())
    assert len(store.list_api_calls(limit=0)) == 1
    assert len(store.list_api_calls(limit=3)) == 3
    assert len(store.list_api_calls(limit=9999)) == 5


def test_count_api_calls() -> None:
    store = _store()
    assert store.count_api_calls() == 0
    store.persist_api_call(_api_call_entry())
    assert store.count_api_calls() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# AuditComplexStore — persist_process_snapshot / list / get
# ═══════════════════════════════════════════════════════════════════════════════


def test_persist_process_snapshot_returns_id() -> None:
    store = _store()
    entry = _snapshot_entry()
    sid = store.persist_process_snapshot(entry)
    assert sid == entry.snapshot_id


def test_persist_process_snapshot_stores_retrievable() -> None:
    store = _store()
    entry = _snapshot_entry()
    store.persist_process_snapshot(entry)
    snapshots = store.list_process_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0]["symbol"] == "BTC-USDT"


def test_list_process_snapshots_empty() -> None:
    store = _store()
    assert store.list_process_snapshots() == []


def test_list_process_snapshots_filters_by_module() -> None:
    store = _store()
    store.persist_process_snapshot(_snapshot_entry(module="scanner", symbol="ETH-USDT"))
    store.persist_process_snapshot(_snapshot_entry(module="bingx"))
    results = store.list_process_snapshots(module="scanner")
    assert len(results) == 1
    assert results[0]["symbol"] == "ETH-USDT"


def test_list_process_snapshots_filters_by_symbol() -> None:
    store = _store()
    store.persist_process_snapshot(_snapshot_entry(symbol="BTC-USDT"))
    store.persist_process_snapshot(_snapshot_entry(symbol="ETH-USDT"))
    results = store.list_process_snapshots(symbol="ETH-USDT")
    assert len(results) == 1


def test_get_process_snapshot_by_id() -> None:
    store = _store()
    entry = _snapshot_entry()
    store.persist_process_snapshot(entry)
    retrieved = store.get_process_snapshot(entry.snapshot_id)
    assert retrieved is not None
    assert retrieved["snapshot_id"] == entry.snapshot_id


def test_get_process_snapshot_unknown_id_returns_none() -> None:
    store = _store()
    assert store.get_process_snapshot("nonexistent") is None


def test_list_process_snapshots_limit_clamping() -> None:
    store = _store()
    for _ in range(5):
        store.persist_process_snapshot(_snapshot_entry())
    assert len(store.list_process_snapshots(limit=0)) == 1
    assert len(store.list_process_snapshots(limit=3)) == 3


def test_count_process_snapshots() -> None:
    store = _store()
    assert store.count_process_snapshots() == 0
    store.persist_process_snapshot(_snapshot_entry())
    assert store.count_process_snapshots() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# AuditComplexStore — persist_error / list / get / resolve
# ═══════════════════════════════════════════════════════════════════════════════


def test_persist_error_returns_error_id() -> None:
    store = _store()
    entry = _error_entry()
    eid = store.persist_error(entry)
    assert eid == entry.error_id


def test_list_errors_empty() -> None:
    store = _store()
    assert store.list_errors() == []


def test_list_errors_filters_by_module() -> None:
    store = _store()
    store.persist_error(_error_entry(module="scanner"))
    store.persist_error(_error_entry(module="bingx", error_type="EXECUTION_FAILURE"))
    results = store.list_errors(module="bingx")
    assert len(results) == 1
    assert results[0]["error_type"] == "EXECUTION_FAILURE"


def test_list_errors_filters_by_severity() -> None:
    store = _store()
    store.persist_error(_error_entry(severity="warning"))
    store.persist_error(_error_entry(severity="critical"))
    results = store.list_errors(severity="critical")
    assert len(results) == 1


def test_list_errors_filters_by_resolved() -> None:
    store = _store()
    e1 = _error_entry()
    e2 = _error_entry(error_type="OTHER")
    store.persist_error(e1)
    store.persist_error(e2)
    store.resolve_error(e2.error_id)
    unresolved = store.list_errors(resolved=False)
    assert len(unresolved) == 1
    assert unresolved[0]["error_id"] == e1.error_id
    resolved = store.list_errors(resolved=True)
    assert len(resolved) == 1


def test_get_error_by_id() -> None:
    store = _store()
    entry = _error_entry(stack_trace="err")
    store.persist_error(entry)
    retrieved = store.get_error(entry.error_id)
    assert retrieved is not None
    assert retrieved["error_id"] == entry.error_id
    assert retrieved["stack_trace"] == "err"


def test_get_error_unknown_id_returns_none() -> None:
    store = _store()
    assert store.get_error("nonexistent") is None


def test_resolve_error_updates_fields() -> None:
    store = _store()
    entry = _error_entry()
    store.persist_error(entry)
    result = store.resolve_error(entry.error_id, resolved_by="operator", notes="fixed")
    assert result is True
    retrieved = store.get_error(entry.error_id)
    assert retrieved is not None
    assert retrieved["resolved"] is True
    assert retrieved["resolved_by"] == "operator"
    assert retrieved["notes"] == "fixed"
    assert retrieved["resolved_at"]


def test_resolve_error_unknown_id_returns_false() -> None:
    store = _store()
    assert store.resolve_error("nonexistent") is False


def test_list_errors_limit_clamping() -> None:
    store = _store()
    for _ in range(5):
        store.persist_error(_error_entry())
    assert len(store.list_errors(limit=0)) == 1
    assert len(store.list_errors(limit=9999)) == 5


def test_count_errors() -> None:
    store = _store()
    assert store.count_errors() == 0
    store.persist_error(_error_entry())
    assert store.count_errors() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# AuditComplexStore — persist_log / search_logs / get_logs_by_correlation_id
# ═══════════════════════════════════════════════════════════════════════════════


def test_persist_log_returns_log_id() -> None:
    store = _store()
    entry = _log_entry()
    lid = store.persist_log(entry)
    assert lid == entry.log_id


def test_persist_logs_batch() -> None:
    store = _store()
    entries = [_log_entry(message=f"msg_{i}") for i in range(10)]
    count = store.persist_logs_batch(entries)
    assert count == 10
    assert store.count_logs() == 10


def test_search_logs_empty() -> None:
    store = _store()
    assert store.search_logs() == []


def test_search_logs_filters_by_level() -> None:
    store = _store()
    store.persist_log(_log_entry(level="INFO"))
    store.persist_log(_log_entry(level="ERROR"))
    results = store.search_logs(level="ERROR")
    assert len(results) == 1
    assert results[0]["level"] == "ERROR"


def test_search_logs_filters_by_module() -> None:
    store = _store()
    store.persist_log(_log_entry(module="system"))
    store.persist_log(_log_entry(module="scanner"))
    results = store.search_logs(module="scanner")
    assert len(results) == 1


def test_search_logs_filters_by_correlation_id() -> None:
    store = _store()
    store.persist_log(_log_entry(correlation_id="corr-111"))
    store.persist_log(_log_entry(correlation_id="corr-222"))
    results = store.search_logs(correlation_id="corr-111")
    assert len(results) == 1
    assert results[0]["correlation_id"] == "corr-111"


def test_search_logs_full_text_query() -> None:
    store = _store()
    store.persist_log(_log_entry(message="Started cycle 42"))
    store.persist_log(_log_entry(message="Completed analysis"))
    results = store.search_logs(query="cycle")
    assert len(results) == 1


def test_search_logs_filters_by_tag() -> None:
    store = _store()
    store.persist_log(_log_entry(tags=["startup"]))
    store.persist_log(_log_entry(tags=["shutdown"]))
    results = store.search_logs(tag="startup")
    assert len(results) == 1


def test_get_logs_by_correlation_id() -> None:
    store = _store()
    store.persist_log(_log_entry(correlation_id="trace-1", message="first"))
    store.persist_log(_log_entry(correlation_id="trace-1", message="second"))
    store.persist_log(_log_entry(correlation_id="trace-2", message="other"))
    results = store.get_logs_by_correlation_id("trace-1")
    assert len(results) == 2
    messages = {r["message"] for r in results}
    assert messages == {"first", "second"}


def test_search_logs_limit_clamping() -> None:
    store = _store()
    for _ in range(5):
        store.persist_log(_log_entry())
    assert len(store.search_logs(limit=0)) == 1
    assert len(store.search_logs(limit=9999)) == 5


def test_count_logs() -> None:
    store = _store()
    assert store.count_logs() == 0
    store.persist_log(_log_entry())
    assert store.count_logs() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# AuditComplexStore — stats & aggregation
# ═══════════════════════════════════════════════════════════════════════════════


def test_get_api_call_stats_by_module() -> None:
    store = _store()
    e1 = _api_call_entry(module="scanner", duration_ms=100.0, estimated_cost=0.001)
    e2 = _api_call_entry(module="scanner", duration_ms=50.0, estimated_cost=0.002)
    e3 = _api_call_entry(module="bingx", provider="bingx", endpoint="/trade")
    store.persist_api_call(e1)
    store.persist_api_call(e2)
    store.persist_api_call(e3)
    stats = store.get_api_call_stats_by_module()
    assert "scanner" in stats
    assert "bingx" in stats
    assert stats["scanner"]["total_calls"] == 2
    assert stats["scanner"]["total_cost_usd"] == 0.003
    assert stats["bingx"]["total_calls"] == 1


def test_get_api_call_stats_by_module_returns_zero_when_empty() -> None:
    store = _store()
    assert store.get_api_call_stats_by_module() == {}


def test_get_error_stats_by_module() -> None:
    store = _store()
    store.persist_error(_error_entry(module="scanner", severity="error"))
    store.persist_error(_error_entry(module="scanner", severity="error"))
    store.persist_error(_error_entry(module="bingx", severity="critical"))
    stats = store.get_error_stats_by_module()
    assert stats["scanner"]["total"] == 2
    assert stats["scanner"]["errors"] == 2
    assert stats["bingx"]["total"] == 1
    assert stats["bingx"]["critical"] == 1


def test_get_error_stats_by_module_returns_empty_dict_when_no_errors() -> None:
    store = _store()
    assert store.get_error_stats_by_module() == {}


def test_get_log_stats() -> None:
    store = _store()
    store.persist_log(_log_entry(level="INFO", module="system"))
    store.persist_log(_log_entry(level="INFO", module="system"))
    store.persist_log(_log_entry(level="ERROR", module="scanner"))
    stats = store.get_log_stats()
    assert stats["total_logs"] == 3
    assert stats["by_level"]["INFO"] == 2
    assert stats["by_level"]["ERROR"] == 1
    assert stats["by_module"]["system"]["total"] == 2
    assert stats["by_module"]["scanner"]["total"] == 1


def test_get_log_stats_returns_zero_counts_when_empty() -> None:
    store = _store()
    stats = store.get_log_stats()
    assert stats["total_logs"] == 0
    assert stats["by_level"] == {}
    assert stats["by_module"] == {}


# ═══════════════════════════════════════════════════════════════════════════════
# AuditComplexStore — cross-table
# ═══════════════════════════════════════════════════════════════════════════════


def test_get_audit_health() -> None:
    store = _store()
    store.persist_api_call(_api_call_entry())
    store.persist_process_snapshot(_snapshot_entry())
    store.persist_error(_error_entry())
    store.persist_log(_log_entry())
    health = store.get_audit_health()
    assert health["db_path"] == ":memory:"
    assert health["persistent"] is False
    tables = health["tables"]
    assert tables["audit_api_calls"] == 1
    assert tables["audit_process_snapshots"] == 1
    assert tables["audit_errors"] == 1
    assert tables["audit_logs"] == 1


def test_get_module_summary() -> None:
    store = _store()
    store.persist_api_call(_api_call_entry(module="scanner"))
    store.persist_process_snapshot(_snapshot_entry(module="scanner", symbol="ETH-USDT"))
    store.persist_error(_error_entry(module="bingx"))
    summary = store.get_module_summary()
    assert "scanner" in summary
    assert "bingx" in summary
    assert summary["scanner"]["api_calls"] == 1
    assert summary["scanner"]["api_calls"] == 1
    assert summary["bingx"]["errors_total"] == 1
    assert summary.get("system") is None


# ═══════════════════════════════════════════════════════════════════════════════
# Data integrity & edge cases
# ═══════════════════════════════════════════════════════════════════════════════


def test_persist_api_call_replaces_on_same_call_id() -> None:
    store = _store()
    entry = _api_call_entry()
    store.persist_api_call(entry)
    entry2 = _api_call_entry(call_id=entry.call_id, duration_ms=999.0)
    store.persist_api_call(entry2)
    calls = store.list_api_calls()
    assert len(calls) == 1
    assert calls[0]["duration_ms"] == 999.0


def test_two_in_memory_stores_are_independent() -> None:
    store = _store()
    store.persist_api_call(_api_call_entry())
    assert store.count_api_calls() == 1
    store2 = _store()
    assert store2.count_api_calls() == 0


def test_error_entry_default_resolved_false() -> None:
    entry = _error_entry()
    assert entry.resolved is False
    assert entry.resolved_at == ""
    assert entry.resolved_by == ""


def test_api_call_entry_default_cache_hit_false() -> None:
    entry = _api_call_entry()
    assert entry.cache_hit is False


def test_log_entry_default_tags_empty() -> None:
    entry = _log_entry()
    assert entry.tags == []


def test_snapshot_entry_id_starts_with_snap() -> None:
    entry = _snapshot_entry()
    assert entry.snapshot_id.startswith("snap_")


def test_error_entry_id_starts_with_err() -> None:
    entry = _error_entry()
    assert entry.error_id.startswith("err_")


def test_log_entry_id_starts_with_log() -> None:
    entry = _log_entry()
    assert entry.log_id.startswith("log_")


def test_api_call_entry_id_starts_with_call() -> None:
    entry = _api_call_entry()
    assert entry.call_id.startswith("call_")


def test_entry_timestamps_are_iso_format() -> None:
    iso = "2026-06-11T12:00:00"
    entry = _api_call_entry(timestamp=iso)
    assert entry.timestamp == iso


def test_indicators_json_decoded_in_list() -> None:
    store = _store()
    entry = _snapshot_entry(indicators={"rsi": 70.0, "macd": 1.2})
    store.persist_process_snapshot(entry)
    snapshots = store.list_process_snapshots()
    indicators = snapshots[0]["indicators"]
    assert isinstance(indicators, dict)
    assert indicators["rsi"] == 70


def test_request_context_json_decoded_in_list() -> None:
    store = _store()
    entry = _api_call_entry(request_context={"attempt": 3})
    store.persist_api_call(entry)
    calls = store.list_api_calls()
    ctx = calls[0]["request_context"]
    assert isinstance(ctx, dict)
    assert ctx["attempt"] == 3
