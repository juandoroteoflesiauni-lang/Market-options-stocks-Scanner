"""Alpaca bot audit store — persistencia DuckDB por ciclo. # [PD-3][TH]"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger
from backend.services.alpaca_event_journal import AlpacaEventJournal
from backend.services.audit_duckdb_utils import (
    connect_audit_duckdb,
    payload_json_for_audit,
    prune_audit_cycles,
)

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
        with self._connect(read_only=False) as conn:
            conn.execute(_DDL)

    @contextmanager
    def _connect(self, *, read_only: bool = False):
        conn = connect_audit_duckdb(self._path, read_only=read_only)
        try:
            yield conn
        finally:
            conn.close()

    def persist_cycle(self, result: Any) -> str:
        """Guarda ``EquityCycleResult`` con análisis, decisiones y ejecuciones."""
        cycle_id = uuid.uuid4().hex
        payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        payload_json = payload_json_for_audit(payload)
        row = {
            "cycle_id": cycle_id,
            "started_at": str(payload.get("started_at", "")),
            "finished_at": str(payload.get("finished_at", "")),
            "dry_run": bool(payload.get("dry_run", True)),
            "universe": json.dumps(list(payload.get("universe", ()))),
            "payload": payload_json,
            "created_at": datetime.now(UTC).isoformat(),
        }
        deleted = 0
        with self._connect(read_only=False) as conn:
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
            deleted = prune_audit_cycles(conn, _TABLE)

        if deleted > 0:
            logger.info("alpaca_audit.pruned table=%s deleted=%d", _TABLE, deleted)

        logger.info(
            "alpaca_audit.persisted cycle_id=%s analyses=%d decisions=%d executions=%d "
            "payload_bytes=%d",
            cycle_id,
            len(payload.get("analyses", [])),
            len(payload.get("decisions", [])),
            len(payload.get("executions", [])),
            len(payload_json),
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
            with self._connect(read_only=True) as conn:
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

    def list_operations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Ledger plano: una fila por ejecución o decisión Alpaca con route."""
        limit = max(1, min(int(limit), 500))
        with self._connect(read_only=True) as conn:
            rows = conn.execute(
                f"""
                SELECT payload FROM {_TABLE}
                ORDER BY started_at DESC
                LIMIT 500
                """
            ).fetchall()
        operations: list[dict[str, Any]] = []
        for (raw,) in rows:
            payload = json.loads(raw)
            operations.extend(_alpaca_operations_from_payload(payload))
            if len(operations) >= limit:
                return operations[:limit]
        return operations[:limit]


def _alpaca_operations_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae filas de operación desde ``EquityCycleResult`` serializado."""
    executions = list(payload.get("executions") or [])
    intents = {i.get("symbol"): i for i in (payload.get("order_intents") or [])}
    decisions = {d.get("symbol"): d for d in (payload.get("decisions") or [])}
    if executions:
        rows: list[dict[str, Any]] = []
        for _idx, ex in enumerate(executions):
            sym = str(ex.get("symbol") or "")
            intent = intents.get(sym, {})
            decision = decisions.get(sym, {})
            route = intent.get("route") or decision.get("route") or "scan"
            notional = float(intent.get("notional_usd") or 0.0)
            rows.append(
                {
                    "event_type": "execution",
                    "symbol": sym,
                    "route": route,
                    "notional_usd": notional,
                    "realized_pnl_usd": float(ex.get("realized_pnl") or 0.0),
                    "execution_ok": ex.get("ok"),
                    "started_at": payload.get("started_at"),
                }
            )
        return rows
    rows = []
    for _idx, decision in enumerate(payload.get("decisions") or []):
        if decision.get("decision") not in {"ALLOW", "SIZE_DOWN"}:
            continue
        sym = str(decision.get("symbol") or "")
        intent = intents.get(sym, {})
        rows.append(
            {
                "event_type": "decision",
                "symbol": sym,
                "route": decision.get("route") or intent.get("route") or "scan",
                "notional_usd": float(intent.get("notional_usd") or 0.0),
                "realized_pnl_usd": 0.0,
                "decision": decision.get("decision"),
                "started_at": payload.get("started_at"),
            }
        )
    return rows


__all__ = ["AlpacaAuditStore"]
