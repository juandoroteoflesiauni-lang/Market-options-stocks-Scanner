"""Structured Logger — correlation-ID-aware logging with DuckDB persistence.

Wraps the standard ``logging`` module with:
* Automatic correlation ID propagation via ``contextvars``.
* Enrichment with module name, tags, and arbitrary context data.
* A ``DuckDBHandler`` that batches writes to the ``audit_logs`` table.
* Convenience functions for logging errors with full stack traces.

Usage
-----
::

    from backend.audit.structured_logger import get_structured_logger, set_correlation_id

    set_correlation_id("abc-123")
    log = get_structured_logger("bingx_service", module="bingx")
    log.info("Cycle started", extra={"tags": ["cycle", "start"], "context_data": {"symbols": 3}})
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import traceback
from typing import Any

from backend.audit.audit_complex_store import AuditComplexStore, LogAuditEntry
from backend.config.logger_setup import get_logger, sanitize_log_message

# ── Correlation ID context ───────────────────────────────────────────────────

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def set_correlation_id(value: str | None) -> None:
    """Set the correlation ID for the current async context."""
    _correlation_id.set(value)


def get_correlation_id() -> str | None:
    """Return the current correlation ID, or ``None``."""
    return _correlation_id.get()


# ── Module-level singleton store ─────────────────────────────────────────────

_store: AuditComplexStore | None = None
_store_lock = threading.Lock()


def _get_store() -> AuditComplexStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from backend.config.settings import load_settings

                settings = load_settings()
                db_path = settings.audit_db_path
                _store = AuditComplexStore(db_path=db_path)
    return _store


def set_audit_store(store: AuditComplexStore) -> None:
    """Inject a store instance (useful for tests or shared connection)."""
    global _store
    _store = store


# ── DuckDB Handler ───────────────────────────────────────────────────────────


class DuckDBHandler(logging.Handler):
    """Logging handler that persists structured log entries to DuckDB.

    Batches entries in memory and flushes every ``batch_size`` records or
    when ``flush()`` is called explicitly.  Thread-safe.
    """

    def __init__(
        self,
        store: AuditComplexStore | None = None,
        *,
        batch_size: int = 50,
        module: str = "system",
    ) -> None:
        super().__init__()
        self._store = store
        self._batch_size = batch_size
        self._module = module
        self._buffer: list[LogAuditEntry] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = self._record_to_entry(record)
            with self._lock:
                self._buffer.append(entry)
                if len(self._buffer) >= self._batch_size:
                    self._flush_locked()
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        store = self._store or _get_store()
        with contextlib.suppress(Exception):
            store.persist_logs_batch(self._buffer)
        self._buffer.clear()

    def _record_to_entry(self, record: logging.LogRecord) -> LogAuditEntry:
        message = sanitize_log_message(record.getMessage())

        # Extract context_data from extra
        context_data: dict[str, Any] = {}
        for key in ("context_data", "extra_data", "data"):
            val = getattr(record, key, None)
            if isinstance(val, dict):
                context_data.update(val)

        # Extract tags from extra
        tags: list[str] = []
        raw_tags = getattr(record, "tags", None)
        if isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags]
        elif isinstance(raw_tags, str):
            tags = [raw_tags]

        # Stack trace for exceptions
        stack_trace = ""
        if record.exc_info and record.exc_info[1] is not None:
            stack_trace = "".join(traceback.format_exception(*record.exc_info))

        module = getattr(record, "audit_module", self._module)
        correlation = get_correlation_id() or ""

        return LogAuditEntry(
            level=record.levelname,
            module=module,
            logger_name=record.name,
            message=message,
            correlation_id=correlation,
            context_data=context_data,
            stack_trace=stack_trace,
            tags=tags,
        )


# ── Structured Logger wrapper ────────────────────────────────────────────────


class StructuredLogger:
    """Convenience wrapper around a standard logger with DuckDB persistence.

    Adds helper methods for error logging with full context capture.
    """

    def __init__(self, name: str, *, module: str = "system") -> None:
        self._logger = get_logger(name)
        self._module = module
        self._name = name

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def attach_duckdb_handler(
        self,
        store: AuditComplexStore | None = None,
        *,
        batch_size: int = 50,
    ) -> None:
        """Attach a DuckDB handler to the underlying logger."""
        handler = DuckDBHandler(store=store, batch_size=batch_size, module=self._module)
        handler.setLevel(logging.DEBUG)
        self._logger.addHandler(handler)

    def debug(self, msg: str, *, tags: list[str] | None = None, **ctx: Any) -> None:
        self._log(logging.DEBUG, msg, tags=tags, **ctx)

    def info(self, msg: str, *, tags: list[str] | None = None, **ctx: Any) -> None:
        self._log(logging.INFO, msg, tags=tags, **ctx)

    def warning(self, msg: str, *, tags: list[str] | None = None, **ctx: Any) -> None:
        self._log(logging.WARNING, msg, tags=tags, **ctx)

    def error(
        self,
        msg: str,
        *,
        exc: BaseException | None = None,
        tags: list[str] | None = None,
        **ctx: Any,
    ) -> None:
        self._log(logging.ERROR, msg, exc_info=exc, tags=tags, **ctx)

    def critical(
        self,
        msg: str,
        *,
        exc: BaseException | None = None,
        tags: list[str] | None = None,
        **ctx: Any,
    ) -> None:
        self._log(logging.CRITICAL, msg, exc_info=exc, tags=tags, **ctx)

    def log_error_to_audit(
        self,
        error_type: str,
        message: str,
        *,
        severity: str = "error",
        exc: BaseException | None = None,
        context: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Persist an error directly to audit_errors table AND log it.

        Returns the ``error_id``.
        """
        from backend.audit.audit_complex_store import ErrorAuditEntry

        stack = ""
        if exc is not None:
            stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

        entry = ErrorAuditEntry(
            module=self._module,
            severity=severity,
            error_type=error_type,
            message=message,
            stack_trace=stack,
            context=context or {},
            correlation_id=get_correlation_id() or "",
        )

        store = _get_store()
        error_id = store.persist_error(entry)

        # Also log normally
        extra_tags = (tags or []) + ["audit_error", self._module]
        self.error(message, exc=exc, tags=extra_tags, context_data={"error_id": error_id})

        return error_id

    def _log(
        self,
        level: int,
        msg: str,
        *,
        exc_info: BaseException | None = None,
        tags: list[str] | None = None,
        **ctx: Any,
    ) -> None:
        extra: dict[str, Any] = {}
        if tags:
            extra["tags"] = tags
        if ctx:
            extra["context_data"] = ctx
        extra["audit_module"] = self._module

        self._logger.log(
            level,
            msg,
            extra=extra,
            exc_info=(
                (type(exc_info), exc_info, exc_info.__traceback__) if exc_info is not None else None
            ),
        )


# ── Factory ──────────────────────────────────────────────────────────────────


def get_structured_logger(
    name: str,
    *,
    module: str = "system",
    attach_duckdb: bool = False,
) -> StructuredLogger:
    """Create or retrieve a ``StructuredLogger``.

    Parameters
    ----------
    name:
        Logger name (typically ``__name__``).
    module:
        Audit module tag (e.g., ``"bingx"``, ``"scanner"``).
    attach_duckdb:
        If ``True``, automatically attach a DuckDB handler so logs are
        persisted to the ``audit_logs`` table.
    """
    sl = StructuredLogger(name, module=module)
    if attach_duckdb:
        sl.attach_duckdb_handler()
    return sl


__all__ = [
    "DuckDBHandler",
    "StructuredLogger",
    "get_correlation_id",
    "get_structured_logger",
    "set_audit_store",
    "set_correlation_id",
]
