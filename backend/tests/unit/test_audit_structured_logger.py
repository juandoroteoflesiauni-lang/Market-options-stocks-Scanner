"""Tests for StructuredLogger and DuckDBHandler."""

from __future__ import annotations

import logging

from backend.audit.audit_complex_store import AuditComplexStore, LogAuditEntry
from backend.audit.structured_logger import (
    DuckDBHandler,
    get_correlation_id,
    get_structured_logger,
    set_audit_store,
    set_correlation_id,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def _store() -> AuditComplexStore:
    return AuditComplexStore(":memory:")


# ═══════════════════════════════════════════════════════════════════════════════
# get_correlation_id / set_correlation_id
# ═══════════════════════════════════════════════════════════════════════════════


def test_correlation_id_defaults_to_none() -> None:
    cid = get_correlation_id()
    assert cid is None


def test_set_correlation_id_returns_value() -> None:
    set_correlation_id("test-corr-123")
    assert get_correlation_id() == "test-corr-123"


def test_set_correlation_id_none_clears() -> None:
    set_correlation_id("some-value")
    set_correlation_id(None)
    assert get_correlation_id() is None


# ═══════════════════════════════════════════════════════════════════════════════
# DuckDBHandler — _record_to_entry
# ═══════════════════════════════════════════════════════════════════════════════


def test_handler_record_to_entry_basic() -> None:
    store = _store()
    handler = DuckDBHandler(store=store, module="test_module")
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="/test.py",
        lineno=42,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    entry = handler._record_to_entry(record)
    assert isinstance(entry, LogAuditEntry)
    assert entry.level == "INFO"
    assert entry.logger_name == "test_logger"
    assert entry.message == "hello world"
    assert entry.module == "test_module"


def test_handler_record_to_entry_extra_fields() -> None:
    store = _store()
    handler = DuckDBHandler(store=store, module="extra_test")
    record = logging.LogRecord(
        name="logger_x",
        level=logging.WARNING,
        pathname="/x.py",
        lineno=10,
        msg="warn: %s",
        args=("danger",),
        exc_info=None,
    )
    set_correlation_id("corr-abc")
    record.__dict__["tags"] = ["critical"]
    entry = handler._record_to_entry(record)
    assert entry.correlation_id == "corr-abc"
    assert entry.tags == ["critical"]
    set_correlation_id(None)


def test_handler_record_to_entry_with_exc_info() -> None:
    store = _store()
    handler = DuckDBHandler(store=store)
    try:
        raise ZeroDivisionError("test")
    except ZeroDivisionError:
        import sys

        exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="t",
            level=logging.ERROR,
            pathname="/t.py",
            lineno=1,
            msg="boom",
            args=(),
            exc_info=exc_info,
        )
    entry = handler._record_to_entry(record)
    assert "ZeroDivisionError" in entry.stack_trace
    assert entry.level == "ERROR"


# ═══════════════════════════════════════════════════════════════════════════════
# DuckDBHandler — emit + flush
# ═══════════════════════════════════════════════════════════════════════════════


def test_handler_emit_single_flushes_at_batch() -> None:
    store = _store()
    handler = DuckDBHandler(store=store, batch_size=3, module="batch_test")
    for i in range(3):
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="/t.py",
            lineno=i,
            msg=f"msg_{i}",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
    # batch_size=3 so it auto-flushes on the 3rd emit
    assert store.count_logs() == 3


def test_handler_flush_writes_buffered() -> None:
    store = _store()
    handler = DuckDBHandler(store=store, batch_size=50, module="flush_test")
    for i in range(5):
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="/t.py",
            lineno=i,
            msg=f"buf_{i}",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
    # buffer has 5, not yet flushed
    assert store.count_logs() == 0
    handler.flush()
    assert store.count_logs() == 5


# ═══════════════════════════════════════════════════════════════════════════════
# StructuredLogger — basic logging
# ═══════════════════════════════════════════════════════════════════════════════


def test_structured_logger_default_module() -> None:
    slog = get_structured_logger("test_logger")
    assert slog._name == "test_logger"


def test_structured_logger_log_methods_do_not_raise() -> None:
    slog = get_structured_logger("test_logger", module="test_mod")
    slog.debug("debug message", user="alice")
    slog.info("info message")
    slog.warning("warn message", tags=["important"])
    slog.error("error message")
    slog.critical("critical message")


# ═══════════════════════════════════════════════════════════════════════════════
# StructuredLogger — DuckDB handler attachment
# ═══════════════════════════════════════════════════════════════════════════════


def test_attach_duckdb_handler_writes_to_store() -> None:
    store = _store()
    set_audit_store(store)
    slog = get_structured_logger("attach_test", module="attach_mod")
    slog.attach_duckdb_handler(store)
    slog.info("persisted message", tags=["attach"])
    # flush the handler
    for handler in slog.logger.handlers:
        if isinstance(handler, DuckDBHandler):
            handler.flush()
    logs = store.search_logs(module="attach_mod")
    assert len(logs) >= 1
    assert logs[0]["message"] == "persisted message"


def test_attach_duckdb_handler_multiple_messages() -> None:
    store = _store()
    set_audit_store(store)
    slog = get_structured_logger("multi_test_unique", module="multi_mod")
    for i in range(5):
        slog.info(f"msg_{i}")
    duck_handlers = [h for h in slog.logger.handlers if isinstance(h, DuckDBHandler)]
    for handler in duck_handlers:
        handler.flush()
    all_logs = store.search_logs(module="multi_mod")
    duck_logs = [rec for rec in all_logs if "msg_" in rec.get("message", "")]
    assert len(duck_logs) == 5


# ═══════════════════════════════════════════════════════════════════════════════
# StructuredLogger — log_error_to_audit
# ═══════════════════════════════════════════════════════════════════════════════


def test_log_error_to_audit_persists_error() -> None:
    store = _store()
    set_audit_store(store)
    slog = get_structured_logger("err_test", module="err_mod")
    error_id = slog.log_error_to_audit(
        error_type="TEST_ERROR",
        message="something broke",
        severity="warning",
        context={"key": "val"},
    )
    assert error_id.startswith("err_")
    retrieved = store.get_error(error_id)
    assert retrieved is not None
    assert retrieved["error_type"] == "TEST_ERROR"
    assert retrieved["severity"] == "warning"
    assert retrieved["context"]["key"] == "val"


def test_log_error_to_audit_with_exception() -> None:
    store = _store()
    set_audit_store(store)
    slog = get_structured_logger("exc_test", module="exc_mod")
    try:
        _ = 1 / 0
    except ZeroDivisionError as exc:
        error_id = slog.log_error_to_audit(
            error_type="DIV_ZERO",
            message="division by zero",
            severity="critical",
            exc=exc,
        )
    retrieved = store.get_error(error_id)
    assert retrieved is not None
    assert "ZeroDivisionError" in retrieved["stack_trace"]


# ═══════════════════════════════════════════════════════════════════════════════
# DuckDBHandler — store not available
# ═══════════════════════════════════════════════════════════════════════════════


def test_handler_emit_no_store_does_not_raise() -> None:
    handler = DuckDBHandler(store=None, module="no_store")
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="/t.py",
        lineno=1,
        msg="no store",
        args=(),
        exc_info=None,
    )
    handler.emit(record)
    handler.flush()
