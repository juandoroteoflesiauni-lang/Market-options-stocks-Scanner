"""Alpaca bot audit store — persistencia DuckDB por ciclo. # [PD-3][TH]"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from backend.config.logger_setup import get_logger
from backend.services.alpaca_event_journal import AlpacaEventJournal

logger = get_logger(__name__)

_TABLE = "alpaca_audit_cycles"
_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    cycle_id    VARCHAR PRIMARY KEY,
    started_at  VARCHAR NOT NULL,
    finished_at VARCHAR NOT NULL,
    dry_run     BOOLEAN NOT NULL,
    universe    VARCHAR NOT NULL,
    payload     VARCHAR NOT NULL,
    created_at  VARCHAR NOT NULL
)
"""


class AlpacaAuditStore:
    """Persiste cada ciclo Alpaca como JSON completo."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_DDL)

    @contextmanager
    def _connect(self):
        conn = duckdb.connect(self._path)
        try:
            yield conn
        finally:
            conn.close()

    def persist_cycle(self, result: Any) -> str:
        """Guarda ``EquityCycleResult`` con análisis, decisiones y ejecuciones."""
        cycle_id = uuid.uuid4().hex
        payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        row = {
            "cycle_id": cycle_id,
            "started_at": str(payload.get("started_at", "")),
            "finished_at": str(payload.get("finished_at", "")),
            "dry_run": bool(payload.get("dry_run", True)),
            "universe": json.dumps(list(payload.get("universe", ()))),
            "payload": json.dumps(payload, default=str),
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {_TABLE}
                (cycle_id, started_at, finished_at, dry_run, universe, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row["cycle_id"],
                    row["started_at"],
                    row["finished_at"],
                    row["dry_run"],
                    row["universe"],
                    row["payload"],
                    row["created_at"],
                ],
            )
        logger.info(
            "alpaca_audit.persisted cycle_id=%s analyses=%d decisions=%d executions=%d",
            cycle_id,
            len(payload.get("analyses", [])),
            len(payload.get("decisions", [])),
            len(payload.get("executions", [])),
        )
        return cycle_id

    def export_glass_box(
        self,
        cycle_id: str | None = None,
        *,
        lineage_tag: str = "alpaca_dual_route",
    ) -> dict[str, Any]:
        """Export glass-box audit: cycle payload + event journal trail."""
        journal = AlpacaEventJournal.instance()
        events = journal.export_glass_box(cycle_id)
        cycle_payload: dict[str, Any] | None = None
        if cycle_id is not None:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT payload FROM {_TABLE} WHERE cycle_id = ?",
                    [cycle_id],
                ).fetchone()
            if row:
                cycle_payload = json.loads(row[0])
        return {
            "lineage": lineage_tag,
            "cycle_id": cycle_id,
            "exported_at": datetime.now(UTC).isoformat(),
            "state_hash": journal.state_hash,
            "event_count": len(events),
            "events": events,
            "cycle_payload": cycle_payload,
        }


__all__ = ["AlpacaAuditStore"]
