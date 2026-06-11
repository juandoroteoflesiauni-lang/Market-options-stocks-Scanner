"""Audit Complex — unified audit, logging and process recording subsystem."""

from backend.audit.audit_complex_store import (
    ApiCallAuditEntry,
    AuditComplexStore,
    ErrorAuditEntry,
    LogAuditEntry,
    ProcessSnapshotEntry,
)
from backend.audit.structured_logger import (
    DuckDBHandler,
    StructuredLogger,
    get_correlation_id,
    get_structured_logger,
    set_audit_store,
    set_correlation_id,
)

__all__ = [
    "ApiCallAuditEntry",
    "AuditComplexStore",
    "DuckDBHandler",
    "ErrorAuditEntry",
    "LogAuditEntry",
    "ProcessSnapshotEntry",
    "StructuredLogger",
    "get_correlation_id",
    "get_structured_logger",
    "set_audit_store",
    "set_correlation_id",
]
