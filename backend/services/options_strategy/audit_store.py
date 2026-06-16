"""Persistencia SQLite de auditoría Options Strategy. # [PD-3][TH]"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.sqlite_db_paths import OPTIONS_STRATEGY_AUDIT_DB
from backend.infrastructure.sqlite_health import apply_sqlite_pragmas, ensure_healthy_or_quarantine
from backend.models.options_strategy import OptionsStrategyAuditLog

logger = get_logger(__name__)

_TABLE = "options_strategy_audit"


@dataclass(frozen=True)
class AuditPersistResult:
    audit_id: str
    inserted: bool
    reason: str = "ok"


class OptionsStrategyAuditStore:
    """Almacena registros ``OptionsStrategyAuditLog`` en SQLite."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or OPTIONS_STRATEGY_AUDIT_DB)
        ensure_healthy_or_quarantine(self.db_path)

    def persist(self, log: OptionsStrategyAuditLog) -> AuditPersistResult:
        payload = log.model_dump(mode="json")
        decision = log.playbook_decision.decision.value
        now = datetime.now(tz=UTC).isoformat()
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"""
                INSERT OR REPLACE INTO {_TABLE} (
                    audit_id, symbol, as_of, decision, pipeline_phase,
                    config_version, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log.audit_id,
                    log.input.symbol,
                    log.input.as_of.isoformat(),
                    decision,
                    log.pipeline_phase,
                    log.config_version,
                    json.dumps(payload, default=str),
                    now,
                ),
            )
            conn.commit()
            inserted = cur.rowcount > 0
        logger.info(
            "options_strategy_audit.persisted audit_id=%s symbol=%s decision=%s",
            log.audit_id,
            log.input.symbol,
            decision,
        )
        return AuditPersistResult(
            audit_id=log.audit_id,
            inserted=inserted,
            reason="inserted" if inserted else "replaced",
        )

    def get(self, audit_id: str) -> dict[str, Any] | None:
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT payload_json FROM {_TABLE} WHERE audit_id = ?",
                (audit_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def list_recent(
        self,
        *,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self._init_db()
        query = f"SELECT audit_id, symbol, as_of, decision, pipeline_phase, created_at FROM {_TABLE}"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol.upper())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "audit_id": row[0],
                "symbol": row[1],
                "as_of": row[2],
                "decision": row[3],
                "pipeline_phase": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]

    def load_recent_logs(self, *, limit: int = 500) -> list[OptionsStrategyAuditLog]:
        """Carga registros completos de auditoría para calibración offline."""
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json FROM {_TABLE}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        logs: list[OptionsStrategyAuditLog] = []
        for row in rows:
            try:
                payload = json.loads(row[0])
                logs.append(OptionsStrategyAuditLog.model_validate(payload))
            except Exception as exc:
                logger.warning("options_strategy_audit.load_skip error=%s", exc)
        return logs

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            apply_sqlite_pragmas(conn)
            conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    audit_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    pipeline_phase TEXT NOT NULL,
                    config_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_options_strategy_audit_symbol
                    ON {_TABLE}(symbol, created_at);
                CREATE INDEX IF NOT EXISTS idx_options_strategy_audit_decision
                    ON {_TABLE}(decision, created_at);
                """
            )


__all__ = ["AuditPersistResult", "OptionsStrategyAuditStore"]
