from __future__ import annotations
from typing import Any
"""Trade Journal Service — Exhaustive Audit Trail for Executed Trades.

Implements the 'Caja Negra' (Black Box) for Forward Testing:
- Captures execution state at the exact moment of trade fill
- Stores complete institutional research snapshot (3 desks)
- Persists in DuckDB trade_journal table + optional JSONL backup

This module is injected into bingx_bot_service.execute_risk_decisions() after
a trade is successfully placed and recorded by the risk desk.

PHASE 1 Constraints:
- Backend-only (no UI/routes touched)
- Synchronous/async agnostic (adapts to caller)
- DuckDB as primary, JSONL as fallback/audit trail
"""


import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


# ── Trade Journal Schema ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class TradeJournalEntry:
    """Single trade execution audit record.

    Fields:
        execution_timestamp: ISO 8601 when trade was filled
        symbol: BingX venue symbol (e.g. "AAPL-USDT")
        side: "BUY" or "SELL"
        quantity: executed quantity
        notional_usdt: trade size in USDT
        entry_price: fill price from BingX

        decision_score: total engine score [0, 1]
        reason_codes: list of decision codes (e.g. "speed_instability_size_down")

        venue_order_id: BingX internal order ID
        realized_pnl: P&L from BingX after fill

        institutional_research_snapshot: complete JSON of 3 desk states
        engine_decision_payload: complete BingXDecision.to_dict()

        dry_run: bool indicating if this was dry-run mode
        cycle_id: cycle identifier for linking to broader audit
    """

    execution_timestamp: str  # ISO 8601
    symbol: str
    side: str
    quantity: float
    notional_usdt: float
    entry_price: float

    decision_score: float
    reason_codes: list[str]

    venue_order_id: str | None
    realized_pnl: float

    institutional_research_snapshot: dict[str, Any]
    engine_decision_payload: dict[str, Any]

    dry_run: bool
    cycle_id: str

    # Internal use
    _created_at: str = field(default_factory=lambda: _utc_iso_now())

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-safe dict."""
        return {
            "execution_timestamp": self.execution_timestamp,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "notional_usdt": self.notional_usdt,
            "entry_price": self.entry_price,
            "decision_score": self.decision_score,
            "reason_codes": self.reason_codes,
            "venue_order_id": self.venue_order_id,
            "realized_pnl": self.realized_pnl,
            "institutional_research_snapshot": self.institutional_research_snapshot,
            "engine_decision_payload": self.engine_decision_payload,
            "dry_run": self.dry_run,
            "cycle_id": self.cycle_id,
            "_created_at": self._created_at,
        }


# ── Helper functions ─────────────────────────────────────────────────────────


def _utc_iso_now() -> str:
    """Return current UTC time in ISO 8601 format with microseconds."""
    return datetime.now(UTC).isoformat()


def _validate_trade_entry(entry: TradeJournalEntry) -> bool:
    """Basic validation of required fields."""
    checks = [
        bool(entry.execution_timestamp),
        bool(entry.symbol),
        entry.side in ("BUY", "SELL"),
        entry.quantity > 0,
        entry.notional_usdt > 0,
        entry.entry_price > 0,
        0.0 <= entry.decision_score <= 1.0,
        isinstance(entry.reason_codes, list),
        isinstance(entry.institutional_research_snapshot, dict),
        isinstance(entry.engine_decision_payload, dict),
        bool(entry.cycle_id),
    ]
    return all(checks)


# ── DuckDB Persistence ───────────────────────────────────────────────────────


def init_trade_journal_table(db_path: str | Path) -> None:
    """Create trade_journal table in DuckDB if not exists.

    Schema: Single denormalized table with JSON columns for nested data
    (institutional_research_snapshot, engine_decision_payload).

    Rationale:
    - Execution timestamp + symbol is unique (single fill per symbol per cycle)
    - JSON columns preserve complete snapshot without requiring joins
    - DuckDB uses implicit rowid for auto-incrementing primary key
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path), read_only=False)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_journal (
                execution_timestamp VARCHAR,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                quantity DOUBLE NOT NULL,
                notional_usdt DOUBLE NOT NULL,
                entry_price DOUBLE NOT NULL,

                decision_score DOUBLE,
                reason_codes JSON,

                venue_order_id VARCHAR,
                realized_pnl DOUBLE,

                institutional_research_snapshot JSON NOT NULL,
                engine_decision_payload JSON NOT NULL,

                dry_run BOOLEAN,
                cycle_id VARCHAR,

                _created_at VARCHAR,

                UNIQUE(execution_timestamp, symbol, cycle_id)
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_cycle_logs (
                cycle_id VARCHAR PRIMARY KEY,
                timestamp TIMESTAMP,
                total_equity DOUBLE,
                available_margin DOUBLE,
                open_positions INTEGER,
                serialized_metrics JSON,
                actions_taken JSON,
                summary JSON
            )
        """
        )
        conn.commit()
        logger.info("trade_journal.init_table db_path=%s", db_path)
    except Exception as e:
        logger.error("trade_journal.init_table failed path=%s error=%s", db_path, e)
        raise
    finally:
        conn.close()


# Compatibility alias
init_table = init_trade_journal_table


def persist_trade_execution(
    entry: TradeJournalEntry,
    db_path: str | Path,
) -> bool:
    """Persist single trade execution to DuckDB.

    Returns True on success, False on validation/persistence failure.
    Logs detailed errors for audit trail.
    """
    if not _validate_trade_entry(entry):
        logger.warning(
            "trade_journal.persist validation_failed symbol=%s cycle_id=%s",
            entry.symbol,
            entry.cycle_id,
        )
        return False

    db_path = Path(db_path)
    try:
        conn = duckdb.connect(str(db_path), read_only=False)
        try:
            conn.execute(
                """
                INSERT INTO trade_journal (
                    execution_timestamp,
                    symbol,
                    side,
                    quantity,
                    notional_usdt,
                    entry_price,
                    decision_score,
                    reason_codes,
                    venue_order_id,
                    realized_pnl,
                    institutional_research_snapshot,
                    engine_decision_payload,
                    dry_run,
                    cycle_id,
                    _created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    entry.execution_timestamp,
                    entry.symbol,
                    entry.side,
                    entry.quantity,
                    entry.notional_usdt,
                    entry.entry_price,
                    entry.decision_score,
                    json.dumps(entry.reason_codes),
                    entry.venue_order_id,
                    entry.realized_pnl,
                    json.dumps(entry.institutional_research_snapshot),
                    json.dumps(entry.engine_decision_payload),
                    entry.dry_run,
                    entry.cycle_id,
                    entry._created_at,
                ],
            )
            conn.commit()
            logger.info(
                "trade_journal.persist symbol=%s pnl=%.4f cycle_id=%s venue_order_id=%s",
                entry.symbol,
                entry.realized_pnl,
                entry.cycle_id,
                entry.venue_order_id,
            )
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.error(
            "trade_journal.persist_failed symbol=%s db_path=%s error=%s",
            entry.symbol,
            db_path,
            e,
        )
        return False


# ── JSONL Backup (Optional) ──────────────────────────────────────────────────


def persist_trade_execution_jsonl(
    entry: TradeJournalEntry,
    jsonl_dir: str | Path,
) -> bool:
    """Append trade execution to JSONL file for audit trail.

    Uses naming convention: trades_{YYYY-MM-DD}.jsonl
    Each line is a complete TradeJournalEntry.to_dict() JSON object.

    Useful for:
    - Backup when DuckDB unavailable
    - Direct inspection in logs folder
    - Compliance/audit retention
    """
    jsonl_dir = Path(jsonl_dir)
    jsonl_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    jsonl_path = jsonl_dir / f"trades_{today}.jsonl"

    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")
        logger.info(
            "trade_journal.persist_jsonl symbol=%s path=%s",
            entry.symbol,
            jsonl_path,
        )
        return True
    except Exception as e:
        logger.error(
            "trade_journal.persist_jsonl_failed symbol=%s path=%s error=%s",
            entry.symbol,
            jsonl_path,
            e,
        )
        return False


# ── Query Interface ──────────────────────────────────────────────────────────


def list_trades(
    db_path: str | Path,
    limit: int = 100,
    symbol: str | None = None,
    cycle_id: str | None = None,
) -> list[dict[str, Any]]:
    """Query trade journal with optional filters.

    Returns list of dicts (not TradeJournalEntry for flexibility).
    Each dict includes all fields from trade_journal table.
    """
    db_path = Path(db_path)

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            query = "SELECT * FROM trade_journal WHERE 1=1"
            params: list[Any] = []

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)

            if cycle_id:
                query += " AND cycle_id = ?"
                params.append(cycle_id)

            query += " ORDER BY execution_timestamp DESC LIMIT ?"
            params.append(limit)

            result = conn.execute(query, params)
            rows = result.fetchall()
            cols = [d[0] for d in result.description] if result.description else []

            return [dict(zip(cols, row, strict=False)) for row in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error("trade_journal.list_trades_failed db_path=%s error=%s", db_path, e)
        return []


def get_cycle_summary(db_path: str | Path, cycle_id: str) -> dict[str, Any]:
    """Get aggregate stats for a complete cycle.

    Returns: {
        "cycle_id": str,
        "trade_count": int,
        "total_notional": float,
        "total_pnl": float,
        "symbols": list[str],
        "timestamp_range": (start, end),
    }
    """
    db_path = Path(db_path)

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            result = conn.execute(
                """
                SELECT
                    cycle_id,
                    COUNT(*) as trade_count,
                    SUM(notional_usdt) as total_notional,
                    SUM(realized_pnl) as total_pnl,
                    ARRAY_AGG(DISTINCT symbol) as symbols,
                    MIN(execution_timestamp) as min_timestamp,
                    MAX(execution_timestamp) as max_timestamp
                FROM trade_journal
                WHERE cycle_id = ?
                GROUP BY cycle_id
            """,
                [cycle_id],
            )

            row = result.fetchone()
            if not row:
                return {}

            cols = [d[0] for d in result.description]
            return dict(zip(cols, row, strict=False))
        finally:
            conn.close()
    except Exception as e:
        logger.error("trade_journal.get_cycle_summary_failed cycle_id=%s error=%s", cycle_id, e)
        return {}


async def record_bot_cycle(
    self_or_data: Any,
    cycle_data: dict | None = None,
    db_path: str | Path = "data/quantum_analyzer.duckdb",
) -> bool:
    """Async record of bot cycle logs in DuckDB.

    Supports both record_bot_cycle(cycle_data) and record_bot_cycle(self, cycle_data).
    """
    if isinstance(self_or_data, dict) and cycle_data is None:
        data = self_or_data
    else:
        data = cycle_data or {}

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = duckdb.connect(str(db_path), read_only=False)
        try:
            conn.execute(
                """
                INSERT INTO bot_cycle_logs (
                    cycle_id,
                    timestamp,
                    total_equity,
                    available_margin,
                    open_positions,
                    serialized_metrics,
                    actions_taken,
                    summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    data.get("cycle_id"),
                    data.get("timestamp"),
                    data.get("total_equity"),
                    data.get("available_margin"),
                    data.get("open_positions"),
                    json.dumps(data.get("serialized_metrics") or {}),
                    json.dumps(data.get("actions_taken") or {}),
                    json.dumps(data.get("summary") or {}),
                ],
            )
            conn.commit()

            cycle_id = data.get("cycle_id")
            open_positions = data.get("open_positions")
            logger.info(
                "INFO | trade_journal.cycle_recorded | id=%s open_positions=%s database=duckdb",
                cycle_id,
                open_positions,
            )
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.error("trade_journal.record_bot_cycle failed db_path=%s error=%s", db_path, e)
        return False
