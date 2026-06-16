"""Persistencia de ejecuciones broker Options Strategy. # [PD-3][TH]"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.sqlite_db_paths import OPTIONS_STRATEGY_AUDIT_DB
from backend.infrastructure.sqlite_health import apply_sqlite_pragmas, ensure_healthy_or_quarantine
from backend.models.options_strategy import OptionsExecutionResult

logger = get_logger(__name__)

_TABLE = "options_strategy_executions"


@dataclass(frozen=True)
class ExecutionPersistResult:
    execution_id: str
    audit_id: str
    inserted: bool


class OptionsStrategyExecutionStore:
    """Registra respuestas del broker vinculadas a ``audit_id``."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or OPTIONS_STRATEGY_AUDIT_DB)
        ensure_healthy_or_quarantine(self.db_path)

    def persist(
        self,
        audit_id: str,
        result: OptionsExecutionResult,
    ) -> ExecutionPersistResult:
        execution_id = f"exec-{uuid.uuid4().hex}"
        now = datetime.now(tz=UTC).isoformat()
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"""
                INSERT INTO {_TABLE} (
                    execution_id, audit_id, client_order_id, underlying,
                    ok, dry_run, venue_order_id, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    audit_id,
                    result.client_order_id,
                    result.underlying,
                    int(result.ok),
                    int(result.dry_run),
                    result.venue_order_id,
                    json.dumps(result.model_dump(mode="json"), default=str),
                    now,
                ),
            )
            conn.commit()
            inserted = cur.rowcount > 0
        logger.info(
            "options_strategy_execution.persisted audit_id=%s ok=%s dry_run=%s",
            audit_id,
            result.ok,
            result.dry_run,
        )
        return ExecutionPersistResult(
            execution_id=execution_id,
            audit_id=audit_id,
            inserted=inserted,
        )

    def list_by_audit(self, audit_id: str) -> list[dict[str, Any]]:
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT execution_id, client_order_id, ok, dry_run, venue_order_id, created_at
                FROM {_TABLE}
                WHERE audit_id = ?
                ORDER BY created_at DESC
                """,
                (audit_id,),
            ).fetchall()
        return [
            {
                "execution_id": row[0],
                "client_order_id": row[1],
                "ok": bool(row[2]),
                "dry_run": bool(row[3]),
                "venue_order_id": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            apply_sqlite_pragmas(conn)
            conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    execution_id TEXT PRIMARY KEY,
                    audit_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    underlying TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    dry_run INTEGER NOT NULL,
                    venue_order_id TEXT,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_options_strategy_executions_audit
                    ON {_TABLE}(audit_id, created_at);
                """
            )


__all__ = ["ExecutionPersistResult", "OptionsStrategyExecutionStore"]
