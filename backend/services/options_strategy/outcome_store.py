"""Persistencia de outcomes/PnL realizados de Options Strategy. # [PD-2][PD-3][TH]"""

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
from backend.models.options_strategy import OptionsTradeOutcome

logger = get_logger(__name__)

_TABLE = "options_strategy_outcomes"


@dataclass(frozen=True)
class OutcomePersistResult:
    audit_id: str
    inserted: bool
    reason: str = "ok"


class OptionsStrategyOutcomeStore:
    """Registra el PnL realizado de cada trade ligado a su ``audit_id``."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or OPTIONS_STRATEGY_AUDIT_DB)
        ensure_healthy_or_quarantine(self.db_path)

    def persist(self, outcome: OptionsTradeOutcome) -> OutcomePersistResult:
        """Inserta o reemplaza el outcome de un ``audit_id`` (idempotente)."""
        now = datetime.now(tz=UTC).isoformat()
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"""
                INSERT OR REPLACE INTO {_TABLE} (
                    audit_id, symbol, structure, status, realized_pnl_usd,
                    is_win, return_pct, closed_at, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome.audit_id,
                    outcome.symbol,
                    outcome.structure.value,
                    outcome.status,
                    str(outcome.realized_pnl_usd),
                    int(outcome.is_win()),
                    outcome.return_pct,
                    outcome.closed_at.isoformat(),
                    json.dumps(outcome.model_dump(mode="json"), default=str),
                    now,
                ),
            )
            conn.commit()
            inserted = cur.rowcount > 0
        logger.info(
            "options_strategy_outcome.persisted audit_id=%s symbol=%s status=%s win=%s",
            outcome.audit_id,
            outcome.symbol,
            outcome.status,
            outcome.is_win(),
        )
        return OutcomePersistResult(
            audit_id=outcome.audit_id,
            inserted=inserted,
            reason="inserted" if inserted else "replaced",
        )

    def get(self, audit_id: str) -> OptionsTradeOutcome | None:
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT payload_json FROM {_TABLE} WHERE audit_id = ?",
                (audit_id,),
            ).fetchone()
        if row is None:
            return None
        return OptionsTradeOutcome.model_validate(json.loads(row[0]))

    def load_win_map(self, *, limit: int = 1000) -> dict[str, bool]:
        """Mapa ``audit_id -> is_win`` para alimentar la calibración con PnL real."""
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT audit_id, is_win FROM {_TABLE}
                WHERE status != 'open'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {row[0]: bool(row[1]) for row in rows}

    def list_recent(
        self,
        *,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self._init_db()
        query = (
            f"SELECT audit_id, symbol, structure, status, realized_pnl_usd, "
            f"is_win, return_pct, closed_at FROM {_TABLE}"
        )
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
                "structure": row[2],
                "status": row[3],
                "realized_pnl_usd": row[4],
                "is_win": bool(row[5]),
                "return_pct": row[6],
                "closed_at": row[7],
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
                    audit_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    structure TEXT NOT NULL,
                    status TEXT NOT NULL,
                    realized_pnl_usd TEXT NOT NULL,
                    is_win INTEGER NOT NULL,
                    return_pct REAL NOT NULL,
                    closed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_options_strategy_outcomes_symbol
                    ON {_TABLE}(symbol, created_at);
                CREATE INDEX IF NOT EXISTS idx_options_strategy_outcomes_status
                    ON {_TABLE}(status, created_at);
                """
            )


__all__ = ["OptionsStrategyOutcomeStore", "OutcomePersistResult"]
