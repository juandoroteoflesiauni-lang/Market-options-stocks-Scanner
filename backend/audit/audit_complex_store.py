"""Audit Complex Store — DuckDB-backed unified audit subsystem.

Extends the existing BingXAuditStore with four new tables for comprehensive
system-wide audit, logging and process recording.

Tables
------
* ``audit_api_calls``          — per-API-call cost/latency/error tracking by module
* ``audit_process_snapshots``  — full engine/indicator state at decision time
* ``audit_errors``             — centralised error registry with stack traces
* ``audit_logs``               — structured log entries with correlation IDs

Design notes
------------
* Reuses the same DuckDB file as BingXAuditStore so a single DB contains all
  audit data.  Table names are stable — do not rename without a migration.
* All payloads use JSON VARCHAR columns for forward-compat (new fields do not
  need ALTER TABLE).
* Secrets are never persisted.  Callers must strip credentials before passing
  data to any ``persist_*`` method.
* File-backed stores open a fresh connection per call to avoid holding write
  locks.  In-memory stores keep a single persistent connection.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# DDL — schema for the four new tables
# ═══════════════════════════════════════════════════════════════════════════════

_DDL_API_CALLS = """
CREATE TABLE IF NOT EXISTS audit_api_calls (
    call_id         VARCHAR PRIMARY KEY,
    timestamp       VARCHAR NOT NULL,
    module          VARCHAR NOT NULL,
    provider        VARCHAR NOT NULL,
    endpoint        VARCHAR NOT NULL,
    api_key_label   VARCHAR NOT NULL,
    status          VARCHAR NOT NULL,
    duration_ms     DOUBLE NOT NULL,
    estimated_cost  DOUBLE NOT NULL,
    cache_hit       BOOLEAN DEFAULT FALSE,
    bytes_received  INTEGER DEFAULT 0,
    retry_count     INTEGER DEFAULT 0,
    error_message   VARCHAR DEFAULT '',
    error_stack     VARCHAR DEFAULT '',
    request_context VARCHAR DEFAULT '{}',
    correlation_id  VARCHAR DEFAULT ''
)
"""

_DDL_PROCESS_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS audit_process_snapshots (
    snapshot_id     VARCHAR PRIMARY KEY,
    timestamp       VARCHAR NOT NULL,
    module          VARCHAR NOT NULL,
    symbol          VARCHAR NOT NULL,
    operation_id    VARCHAR DEFAULT '',
    correlation_id  VARCHAR DEFAULT '',
    indicators      VARCHAR NOT NULL,
    orderbook       VARCHAR DEFAULT '{}',
    market_data     VARCHAR DEFAULT '{}',
    signals         VARCHAR DEFAULT '{}',
    decisions       VARCHAR DEFAULT '{}',
    risk_metrics    VARCHAR DEFAULT '{}',
    engine_state    VARCHAR DEFAULT '{}',
    context         VARCHAR DEFAULT '{}'
)
"""

_DDL_ERRORS = """
CREATE TABLE IF NOT EXISTS audit_errors (
    error_id        VARCHAR PRIMARY KEY,
    timestamp       VARCHAR NOT NULL,
    module          VARCHAR NOT NULL,
    severity        VARCHAR NOT NULL,
    error_type      VARCHAR NOT NULL,
    message         VARCHAR NOT NULL,
    stack_trace     VARCHAR DEFAULT '',
    context         VARCHAR DEFAULT '{}',
    correlation_id  VARCHAR DEFAULT '',
    resolved        BOOLEAN DEFAULT FALSE,
    resolved_at     VARCHAR DEFAULT '',
    resolved_by     VARCHAR DEFAULT '',
    notes           VARCHAR DEFAULT ''
)
"""

_DDL_LOGS = """
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id          VARCHAR PRIMARY KEY,
    timestamp       VARCHAR NOT NULL,
    level           VARCHAR NOT NULL,
    module          VARCHAR NOT NULL,
    logger_name     VARCHAR NOT NULL,
    message         VARCHAR NOT NULL,
    correlation_id  VARCHAR DEFAULT '',
    context_data    VARCHAR DEFAULT '{}',
    stack_trace     VARCHAR DEFAULT '',
    tags            VARCHAR DEFAULT '[]'
)
"""

_DDL_TRADE_RESULTS = """
CREATE TABLE IF NOT EXISTS audit_trade_results (
    trade_id        VARCHAR PRIMARY KEY,
    timestamp       VARCHAR NOT NULL,
    module          VARCHAR NOT NULL,
    symbol          VARCHAR NOT NULL,
    operation_id    VARCHAR DEFAULT '',
    correlation_id  VARCHAR DEFAULT '',
    pnl_pct         DOUBLE NOT NULL,
    pnl_usd         DOUBLE NOT NULL,
    exit_reason     VARCHAR NOT NULL,
    context         VARCHAR DEFAULT '{}'
)
"""

_DDL_AGENTIC_TRADE_DECISIONS = """
CREATE TABLE IF NOT EXISTS audit_agentic_trade_decisions (
    event_id            VARCHAR PRIMARY KEY,
    timestamp           VARCHAR NOT NULL,
    module              VARCHAR NOT NULL,
    symbol              VARCHAR NOT NULL,
    contract_symbol     VARCHAR NOT NULL,
    correlation_id      VARCHAR DEFAULT '',
    final_decision      VARCHAR NOT NULL,
    quant_default_used  BOOLEAN DEFAULT FALSE,
    payload             VARCHAR NOT NULL
)
"""

_ALL_DDLS: list[str] = [
    _DDL_API_CALLS,
    _DDL_PROCESS_SNAPSHOTS,
    _DDL_ERRORS,
    _DDL_LOGS,
    _DDL_TRADE_RESULTS,
    _DDL_AGENTIC_TRADE_DECISIONS,
]

# Columnas añadidas tras el primer deploy — migración idempotente (F10).
_SCHEMA_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("audit_process_snapshots", "correlation_id", "VARCHAR DEFAULT ''"),
    ("audit_api_calls", "correlation_id", "VARCHAR DEFAULT ''"),
    ("audit_errors", "correlation_id", "VARCHAR DEFAULT ''"),
    ("audit_logs", "correlation_id", "VARCHAR DEFAULT ''"),
    ("audit_trade_results", "correlation_id", "VARCHAR DEFAULT ''"),
)


# ═══════════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════════


def _new_id(prefix: str = "") -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"{prefix}{ts}_{uid}" if prefix else f"{ts}_{uid}"


# ── API Call Record ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ApiCallAuditEntry:
    """Single API call record for the audit_api_calls table."""

    module: str
    provider: str
    endpoint: str
    status: str  # success / error / timeout / rate_limited / circuit_open / cache_hit
    duration_ms: float
    estimated_cost: float
    api_key_label: str = "default"
    cache_hit: bool = False
    bytes_received: int = 0
    retry_count: int = 0
    error_message: str = ""
    error_stack: str = ""
    request_context: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    call_id: str = field(default_factory=lambda: _new_id("call_"))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.call_id,
            self.timestamp,
            self.module,
            self.provider,
            self.endpoint,
            self.api_key_label,
            self.status,
            self.duration_ms,
            self.estimated_cost,
            self.cache_hit,
            self.bytes_received,
            self.retry_count,
            self.error_message,
            self.error_stack,
            json.dumps(self.request_context, default=str),
            self.correlation_id,
        )


# ── Process Snapshot ─────────────────────────────────────────────────────────


@dataclass
class ProcessSnapshotEntry:
    """Full engine/indicator state captured at decision time.

    ``indicators`` is the only required JSON block — it must contain the
    indicator values (RSI, MACD, VWAP, etc.) the engine used.  All other
    blocks are optional and default to empty dicts.
    """

    module: str
    symbol: str
    indicators: dict[str, Any]
    orderbook: dict[str, Any] = field(default_factory=dict)
    market_data: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    decisions: dict[str, Any] = field(default_factory=dict)
    risk_metrics: dict[str, Any] = field(default_factory=dict)
    engine_state: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    operation_id: str = ""
    correlation_id: str = ""
    snapshot_id: str = field(default_factory=lambda: _new_id("snap_"))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.snapshot_id,
            self.timestamp,
            self.module,
            self.symbol,
            self.operation_id,
            self.correlation_id,
            json.dumps(self.indicators, default=str),
            json.dumps(self.orderbook, default=str),
            json.dumps(self.market_data, default=str),
            json.dumps(self.signals, default=str),
            json.dumps(self.decisions, default=str),
            json.dumps(self.risk_metrics, default=str),
            json.dumps(self.engine_state, default=str),
            json.dumps(self.context, default=str),
        )


# ── Error Entry ──────────────────────────────────────────────────────────────


@dataclass
class ErrorAuditEntry:
    """Centralised error record with full stack trace and context."""

    module: str
    severity: str  # critical / error / warning
    error_type: str
    message: str
    stack_trace: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    resolved: bool = False
    resolved_at: str = ""
    resolved_by: str = ""
    notes: str = ""
    error_id: str = field(default_factory=lambda: _new_id("err_"))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.error_id,
            self.timestamp,
            self.module,
            self.severity,
            self.error_type,
            self.message,
            self.stack_trace,
            json.dumps(self.context, default=str),
            self.correlation_id,
            self.resolved,
            self.resolved_at,
            self.resolved_by,
            self.notes,
        )


# ── Log Entry ────────────────────────────────────────────────────────────────


@dataclass
class LogAuditEntry:
    """Structured log entry with correlation ID and tags."""

    level: str  # DEBUG / INFO / WARNING / ERROR / CRITICAL
    module: str
    logger_name: str
    message: str
    correlation_id: str = ""
    context_data: dict[str, Any] = field(default_factory=dict)
    stack_trace: str = ""
    tags: list[str] = field(default_factory=list)
    log_id: str = field(default_factory=lambda: _new_id("log_"))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.log_id,
            self.timestamp,
            self.level,
            self.module,
            self.logger_name,
            self.message,
            self.correlation_id,
            json.dumps(self.context_data, default=str),
            self.stack_trace,
            json.dumps(self.tags),
        )


# ── Trade Result Entry ───────────────────────────────────────────────────────


@dataclass
class TradeResultAuditEntry:
    """Trade result record (PnL at exit)."""

    module: str
    symbol: str
    pnl_pct: float
    pnl_usd: float
    exit_reason: str
    operation_id: str = ""
    correlation_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    trade_id: str = field(default_factory=lambda: _new_id("trd_"))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.trade_id,
            self.timestamp,
            self.module,
            self.symbol,
            self.operation_id,
            self.correlation_id,
            self.pnl_pct,
            self.pnl_usd,
            self.exit_reason,
            json.dumps(self.context, default=str),
        )


@dataclass
class AgenticDecisionAuditEntry:
    """Agentic trade decision record for audit_agentic_trade_decisions."""

    module: str
    symbol: str
    contract_symbol: str
    final_decision: str
    payload: dict[str, Any]
    correlation_id: str = ""
    quant_default_used: bool = False
    event_id: str = field(default_factory=lambda: _new_id("agt_"))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.event_id,
            self.timestamp,
            self.module,
            self.symbol,
            self.contract_symbol,
            self.correlation_id,
            self.final_decision,
            self.quant_default_used,
            json.dumps(self.payload, default=str),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Store
# ═══════════════════════════════════════════════════════════════════════════════


class AuditComplexStore:
    """DuckDB-backed unified audit store.

    Parameters
    ----------
    db_path:
        Path to the DuckDB file.  Defaults to ``":memory:"`` for tests.
        When set to the same file as BingXAuditStore both stores share the
        same database, enabling cross-table queries.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._in_memory = self._db_path == ":memory:"
        if not self._in_memory:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._mem_conn: duckdb.DuckDBPyConnection | None = (
            duckdb.connect(":memory:") if self._in_memory else None
        )
        self._ensure_schema()

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def is_persistent(self) -> bool:
        return not self._in_memory

    # ── Context manager ───────────────────────────────────────────────────────

    @contextmanager
    def _connect(self) -> Any:
        if self._in_memory:
            assert self._mem_conn is not None
            yield self._mem_conn
        else:
            con = duckdb.connect(self._db_path)
            try:
                yield con
            finally:
                con.close()

    def _ensure_schema(self) -> None:
        with self._connect() as con:
            for ddl in _ALL_DDLS:
                con.execute(ddl)
            self._migrate_schema(con)

    def _migrate_schema(self, con: duckdb.DuckDBPyConnection) -> None:
        """Añade columnas nuevas en DBs creadas antes de F10."""
        for table, column, typedef in _SCHEMA_MIGRATIONS:
            try:
                rows = con.execute(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = ? AND column_name = ?
                    """,
                    [table, column],
                ).fetchall()
                if rows:
                    continue
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")
                logger.info("audit_schema.migrated table=%s column=%s", table, column)
            except Exception as exc:
                logger.warning(
                    "audit_schema.migration_failed table=%s column=%s error=%s",
                    table,
                    column,
                    exc,
                )

    # ══════════════════════════════════════════════════════════════════════════
    # API CALLS
    # ══════════════════════════════════════════════════════════════════════════

    def persist_api_call(self, entry: ApiCallAuditEntry) -> str:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO audit_api_calls
                    (call_id, timestamp, module, provider, endpoint,
                     api_key_label, status, duration_ms, estimated_cost,
                     cache_hit, bytes_received, retry_count,
                     error_message, error_stack, request_context, correlation_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                entry.to_row(),
            )
        logger.debug("audit_api_call.persisted call_id=%s module=%s", entry.call_id, entry.module)
        return entry.call_id

    def list_api_calls(
        self,
        *,
        module: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if module:
            clauses.append("module = ?")
            params.append(module)
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 2000))
        params.append(limit)

        with self._connect() as con:
            rows = con.execute(
                f"""  # nosec B608
                SELECT call_id, timestamp, module, provider, endpoint,
                       api_key_label, status, duration_ms, estimated_cost,
                       cache_hit, bytes_received, retry_count,
                       error_message, error_stack, request_context, correlation_id
                FROM audit_api_calls
                {where}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [
            {
                "call_id": r[0],
                "timestamp": r[1],
                "module": r[2],
                "provider": r[3],
                "endpoint": r[4],
                "api_key_label": r[5],
                "status": r[6],
                "duration_ms": r[7],
                "estimated_cost": r[8],
                "cache_hit": bool(r[9]),
                "bytes_received": r[10],
                "retry_count": r[11],
                "error_message": r[12],
                "error_stack": r[13],
                "request_context": json.loads(r[14]) if r[14] else {},
                "correlation_id": r[15],
            }
            for r in rows
        ]

    def get_api_call_stats_by_module(self) -> dict[str, dict[str, Any]]:
        """Aggregate API call statistics grouped by module."""
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT
                    module,
                    COUNT(*)                        AS total_calls,
                    SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_calls,
                    SUM(CASE WHEN status IN ('error','timeout') THEN 1 ELSE 0 END) AS error_calls,
                    SUM(CASE WHEN status='rate_limited' THEN 1 ELSE 0 END) AS rate_limited,
                    SUM(estimated_cost)             AS total_cost,
                    SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) AS cache_hits,
                    AVG(duration_ms)                AS avg_duration_ms,
                    MIN(timestamp)                  AS first_call,
                    MAX(timestamp)                  AS last_call
                FROM audit_api_calls
                GROUP BY module
                ORDER BY total_cost DESC
                """
            ).fetchall()

        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            total = r[1] or 0
            result[r[0]] = {
                "module": r[0],
                "total_calls": total,
                "success_calls": r[2] or 0,
                "error_calls": r[3] or 0,
                "rate_limited": r[4] or 0,
                "total_cost_usd": round(r[5] or 0, 6),
                "cache_hits": r[6] or 0,
                "avg_duration_ms": round(r[7] or 0, 2),
                "error_rate_pct": round((r[3] or 0) / total * 100, 2) if total > 0 else 0.0,
                "cache_hit_rate_pct": round((r[6] or 0) / total * 100, 2) if total > 0 else 0.0,
                "first_call": r[8],
                "last_call": r[9],
            }
        return result

    def get_api_call_stats_by_provider_per_module(
        self,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Nested: module → provider → stats."""
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT
                    module, provider,
                    COUNT(*)                  AS calls,
                    SUM(estimated_cost)       AS cost,
                    SUM(CASE WHEN status IN ('error','timeout') THEN 1 ELSE 0 END) AS errors,
                    AVG(duration_ms)          AS avg_ms,
                    SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) AS hits
                FROM audit_api_calls
                GROUP BY module, provider
                ORDER BY module, cost DESC
                """
            ).fetchall()

        result: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for r in rows:
            total = r[2] or 0
            result[r[0]][r[1]] = {
                "provider": r[1],
                "calls": total,
                "cost_usd": round(r[3] or 0, 6),
                "errors": r[4] or 0,
                "avg_duration_ms": round(r[5] or 0, 2),
                "cache_hit_rate_pct": round((r[6] or 0) / total * 100, 2) if total > 0 else 0.0,
            }
        return dict(result)

    def count_api_calls(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) FROM audit_api_calls").fetchone()
            return int(row[0]) if row else 0

    # ══════════════════════════════════════════════════════════════════════════
    # PROCESS SNAPSHOTS
    # ══════════════════════════════════════════════════════════════════════════

    def persist_process_snapshot(self, entry: ProcessSnapshotEntry) -> str:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO audit_process_snapshots
                    (snapshot_id, timestamp, module, symbol, operation_id,
                     correlation_id, indicators, orderbook, market_data, signals,
                     decisions, risk_metrics, engine_state, context)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                entry.to_row(),
            )
        logger.debug(
            "audit_snapshot.persisted id=%s module=%s symbol=%s",
            entry.snapshot_id,
            entry.module,
            entry.symbol,
        )
        return entry.snapshot_id

    def list_process_snapshots(
        self,
        *,
        module: str | None = None,
        symbol: str | None = None,
        operation_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if module:
            clauses.append("module = ?")
            params.append(module)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if operation_id:
            clauses.append("operation_id = ?")
            params.append(operation_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 500))
        params.append(limit)

        with self._connect() as con:
            rows = con.execute(
                f"""  # nosec B608
                SELECT snapshot_id, timestamp, module, symbol, operation_id,
                       correlation_id, indicators, orderbook, market_data, signals,
                       decisions, risk_metrics, engine_state, context
                FROM audit_process_snapshots
                {where}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [
            {
                "snapshot_id": r[0],
                "timestamp": r[1],
                "module": r[2],
                "symbol": r[3],
                "operation_id": r[4],
                "correlation_id": r[5],
                "indicators": json.loads(r[6]),
                "orderbook": json.loads(r[7]) if r[7] else {},
                "market_data": json.loads(r[8]) if r[8] else {},
                "signals": json.loads(r[9]) if r[9] else {},
                "decisions": json.loads(r[10]) if r[10] else {},
                "risk_metrics": json.loads(r[11]) if r[11] else {},
                "engine_state": json.loads(r[12]) if r[12] else {},
                "context": json.loads(r[13]) if r[13] else {},
            }
            for r in rows
        ]

    def get_process_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT snapshot_id, timestamp, module, symbol, operation_id,
                       correlation_id, indicators, orderbook, market_data, signals,
                       decisions, risk_metrics, engine_state, context
                FROM audit_process_snapshots
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchall()
        if not rows:
            return None
        r = rows[0]
        return {
            "snapshot_id": r[0],
            "timestamp": r[1],
            "module": r[2],
            "symbol": r[3],
            "operation_id": r[4],
            "correlation_id": r[5],
            "indicators": json.loads(r[6]),
            "orderbook": json.loads(r[7]) if r[7] else {},
            "market_data": json.loads(r[8]) if r[8] else {},
            "signals": json.loads(r[9]) if r[9] else {},
            "decisions": json.loads(r[10]) if r[10] else {},
            "risk_metrics": json.loads(r[11]) if r[11] else {},
            "engine_state": json.loads(r[12]) if r[12] else {},
            "context": json.loads(r[13]) if r[13] else {},
        }

    def count_process_snapshots(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) FROM audit_process_snapshots").fetchone()
            return int(row[0]) if row else 0

    # ══════════════════════════════════════════════════════════════════════════
    # ERRORS
    # ══════════════════════════════════════════════════════════════════════════

    def persist_error(self, entry: ErrorAuditEntry) -> str:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO audit_errors
                    (error_id, timestamp, module, severity, error_type,
                     message, stack_trace, context, correlation_id,
                     resolved, resolved_at, resolved_by, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                entry.to_row(),
            )
        logger.debug("audit_error.persisted id=%s module=%s", entry.error_id, entry.module)
        return entry.error_id

    def list_errors(
        self,
        *,
        module: str | None = None,
        severity: str | None = None,
        resolved: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if module:
            clauses.append("module = ?")
            params.append(module)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if resolved is not None:
            clauses.append("resolved = ?")
            params.append(resolved)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 1000))
        params.append(limit)

        with self._connect() as con:
            rows = con.execute(
                f"""  # nosec B608
                SELECT error_id, timestamp, module, severity, error_type,
                       message, stack_trace, context, correlation_id,
                       resolved, resolved_at, resolved_by, notes
                FROM audit_errors
                {where}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [
            {
                "error_id": r[0],
                "timestamp": r[1],
                "module": r[2],
                "severity": r[3],
                "error_type": r[4],
                "message": r[5],
                "stack_trace": r[6],
                "context": json.loads(r[7]) if r[7] else {},
                "correlation_id": r[8],
                "resolved": bool(r[9]),
                "resolved_at": r[10],
                "resolved_by": r[11],
                "notes": r[12],
            }
            for r in rows
        ]

    def get_error(self, error_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT error_id, timestamp, module, severity, error_type,
                       message, stack_trace, context, correlation_id,
                       resolved, resolved_at, resolved_by, notes
                FROM audit_errors
                WHERE error_id = ?
                """,
                (error_id,),
            ).fetchall()
        if not rows:
            return None
        r = rows[0]
        return {
            "error_id": r[0],
            "timestamp": r[1],
            "module": r[2],
            "severity": r[3],
            "error_type": r[4],
            "message": r[5],
            "stack_trace": r[6],
            "context": json.loads(r[7]) if r[7] else {},
            "correlation_id": r[8],
            "resolved": bool(r[9]),
            "resolved_at": r[10],
            "resolved_by": r[11],
            "notes": r[12],
        }

    def resolve_error(self, error_id: str, resolved_by: str = "", notes: str = "") -> bool:
        now = datetime.now(UTC).isoformat()
        with self._connect() as con:
            exists = con.execute(
                "SELECT COUNT(*) FROM audit_errors WHERE error_id = ?",
                (error_id,),
            ).fetchone()[0]
            if not exists:
                return False
            con.execute(
                """
                UPDATE audit_errors
                SET resolved = TRUE, resolved_at = ?, resolved_by = ?, notes = ?
                WHERE error_id = ?
                """,
                (now, resolved_by, notes, error_id),
            )
        return True

    def get_error_stats_by_module(self) -> dict[str, dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT
                    module,
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS critical,
                    SUM(CASE WHEN severity='error'    THEN 1 ELSE 0 END) AS errors,
                    SUM(CASE WHEN severity='warning'  THEN 1 ELSE 0 END) AS warnings,
                    SUM(CASE WHEN resolved THEN 1 ELSE 0 END)          AS resolved_count,
                    MIN(timestamp)                                    AS first_error,
                    MAX(timestamp)                                    AS last_error
                FROM audit_errors
                GROUP BY module
                ORDER BY total DESC
                """
            ).fetchall()

        return {
            r[0]: {
                "module": r[0],
                "total": r[1],
                "critical": r[2],
                "errors": r[3],
                "warnings": r[4],
                "resolved": r[5],
                "unresolved": r[1] - r[5],
                "resolution_rate_pct": round(r[5] / r[1] * 100, 2) if r[1] > 0 else 0.0,
                "first_error": r[6],
                "last_error": r[7],
            }
            for r in rows
        }

    def count_errors(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) FROM audit_errors").fetchone()
            return int(row[0]) if row else 0

    # ══════════════════════════════════════════════════════════════════════════
    # LOGS
    # ══════════════════════════════════════════════════════════════════════════

    def persist_log(self, entry: LogAuditEntry) -> str:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO audit_logs
                    (log_id, timestamp, level, module, logger_name,
                     message, correlation_id, context_data, stack_trace, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                entry.to_row(),
            )
        return entry.log_id

    def persist_logs_batch(self, entries: list[LogAuditEntry]) -> int:
        if not entries:
            return 0
        rows = [e.to_row() for e in entries]
        with self._connect() as con:
            con.executemany(
                """
                INSERT OR REPLACE INTO audit_logs
                    (log_id, timestamp, level, module, logger_name,
                     message, correlation_id, context_data, stack_trace, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
        return len(rows)

    def search_logs(
        self,
        *,
        query: str | None = None,
        module: str | None = None,
        level: str | None = None,
        correlation_id: str | None = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if query:
            clauses.append("message ILIKE ?")
            params.append(f"%{query}%")
        if module:
            clauses.append("module = ?")
            params.append(module)
        if level:
            clauses.append("level = ?")
            params.append(level)
        if correlation_id:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        if tag:
            clauses.append("tags ILIKE ?")
            params.append(f"%{tag}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 2000))
        params.append(limit)

        with self._connect() as con:
            rows = con.execute(
                f"""  # nosec B608
                SELECT log_id, timestamp, level, module, logger_name,
                       message, correlation_id, context_data, stack_trace, tags
                FROM audit_logs
                {where}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [
            {
                "log_id": r[0],
                "timestamp": r[1],
                "level": r[2],
                "module": r[3],
                "logger_name": r[4],
                "message": r[5],
                "correlation_id": r[6],
                "context_data": json.loads(r[7]) if r[7] else {},
                "stack_trace": r[8],
                "tags": json.loads(r[9]) if r[9] else [],
            }
            for r in rows
        ]

    def get_logs_by_correlation_id(self, correlation_id: str) -> list[dict[str, Any]]:
        """Return all log entries sharing the same correlation_id, ordered by time."""
        return self.search_logs(correlation_id=correlation_id, limit=500)

    def get_log_stats(self) -> dict[str, Any]:
        """Aggregate log statistics by level and module."""
        with self._connect() as con:
            level_rows = con.execute(
                """
                SELECT level, COUNT(*) AS cnt
                FROM audit_logs
                GROUP BY level
                ORDER BY cnt DESC
                """
            ).fetchall()

            module_rows = con.execute(
                """
                SELECT module, COUNT(*) AS cnt,
                       SUM(CASE WHEN level IN ('ERROR','CRITICAL') THEN 1 ELSE 0 END) AS error_count
                FROM audit_logs
                GROUP BY module
                ORDER BY cnt DESC
                """
            ).fetchall()

        return {
            "by_level": {r[0]: r[1] for r in level_rows},
            "by_module": {r[0]: {"total": r[1], "errors": r[2]} for r in module_rows},
            "total_logs": sum(r[1] for r in level_rows),
        }

    def count_logs(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) FROM audit_logs").fetchone()
            return int(row[0]) if row else 0

    # ══════════════════════════════════════════════════════════════════════════
    # TRADE RESULTS
    # ══════════════════════════════════════════════════════════════════════════

    def persist_trade_result(self, entry: TradeResultAuditEntry) -> str:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO audit_trade_results
                    (trade_id, timestamp, module, symbol, operation_id,
                     correlation_id, pnl_pct, pnl_usd, exit_reason, context)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                entry.to_row(),
            )
        return entry.trade_id

    def list_trade_results(
        self,
        *,
        module: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if module:
            clauses.append("module = ?")
            params.append(module)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 2000))
        params.append(limit)

        with self._connect() as con:
            rows = con.execute(
                f"""  # nosec B608
                SELECT trade_id, timestamp, module, symbol, operation_id,
                       correlation_id, pnl_pct, pnl_usd, exit_reason, context
                FROM audit_trade_results
                {where}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [
            {
                "trade_id": r[0],
                "timestamp": r[1],
                "module": r[2],
                "symbol": r[3],
                "operation_id": r[4],
                "correlation_id": r[5],
                "pnl_pct": r[6],
                "pnl_usd": r[7],
                "exit_reason": r[8],
                "context": json.loads(r[9]) if r[9] else {},
            }
            for r in rows
        ]

    # ══════════════════════════════════════════════════════════════════════════
    # AGENTIC TRADE DECISIONS
    # ══════════════════════════════════════════════════════════════════════════

    def persist_agentic_decision(self, entry: AgenticDecisionAuditEntry) -> str:
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO audit_agentic_trade_decisions
                    (event_id, timestamp, module, symbol, contract_symbol,
                     correlation_id, final_decision, quant_default_used, payload)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                entry.to_row(),
            )
        return entry.event_id

    def get_agentic_decision(self, event_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT event_id, timestamp, module, symbol, contract_symbol,
                       correlation_id, final_decision, quant_default_used, payload
                FROM audit_agentic_trade_decisions
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "event_id": row[0],
            "timestamp": row[1],
            "module": row[2],
            "symbol": row[3],
            "contract_symbol": row[4],
            "correlation_id": row[5],
            "final_decision": row[6],
            "quant_default_used": bool(row[7]),
            "payload": json.loads(row[8]) if row[8] else {},
        }

    # ══════════════════════════════════════════════════════════════════════════
    # CROSS-TABLE QUERIES
    # ══════════════════════════════════════════════════════════════════════════

    def get_audit_health(self) -> dict[str, Any]:
        """Return overall audit subsystem health and table sizes."""
        return {
            "db_path": self._db_path,
            "persistent": self.is_persistent,
            "tables": {
                "audit_api_calls": self.count_api_calls(),
                "audit_process_snapshots": self.count_process_snapshots(),
                "audit_errors": self.count_errors(),
                "audit_logs": self.count_logs(),
            },
        }

    def get_module_summary(self) -> dict[str, dict[str, Any]]:
        """Per-module summary combining API calls, errors and snapshots."""
        api_stats = self.get_api_call_stats_by_module()
        error_stats = self.get_error_stats_by_module()

        all_modules = sorted(set(api_stats) | set(error_stats))
        result: dict[str, dict[str, Any]] = {}
        for mod in all_modules:
            api = api_stats.get(mod, {})
            err = error_stats.get(mod, {})
            result[mod] = {
                "api_calls": api.get("total_calls", 0),
                "api_cost_usd": api.get("total_cost_usd", 0.0),
                "api_error_rate_pct": api.get("error_rate_pct", 0.0),
                "errors_total": err.get("total", 0),
                "errors_critical": err.get("critical", 0),
                "errors_unresolved": err.get("unresolved", 0),
            }
        return result


__all__ = [
    "ApiCallAuditEntry",
    "AuditComplexStore",
    "ErrorAuditEntry",
    "LogAuditEntry",
    "ProcessSnapshotEntry",
    "TradeResultAuditEntry",
]
