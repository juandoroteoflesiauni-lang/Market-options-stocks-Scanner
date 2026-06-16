import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from backend.models.trade_record import TradeRecord


class TradeHistoryRepository:
    """
    Repository for persisting and querying TradeRecords.
    Uses SQLite with an index on setup_type.
    """

    def __init__(self, db_path: str = "data/funding_trade_history.sqlite") -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Idempotent schema initialization."""
        query = """
        CREATE TABLE IF NOT EXISTS trade_history (
            trade_id TEXT PRIMARY KEY,
            setup_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price TEXT NOT NULL,
            exit_price TEXT,
            quantity TEXT NOT NULL,
            risk_r TEXT NOT NULL,
            realized_r TEXT,
            pnl TEXT,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            equity_after TEXT,
            mode TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trade_history_setup_type ON trade_history(setup_type);
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(query)

    def _row_to_record(self, row: tuple) -> TradeRecord:  # type: ignore
        return TradeRecord(
            trade_id=row[0],
            setup_type=row[1],
            symbol=row[2],
            direction=row[3],
            entry_price=Decimal(row[4]),
            exit_price=Decimal(row[5]) if row[5] else None,
            quantity=Decimal(row[6]),
            risk_r=Decimal(row[7]),
            realized_r=Decimal(row[8]) if row[8] else None,
            pnl=Decimal(row[9]) if row[9] else None,
            opened_at=datetime.fromisoformat(row[10]).replace(tzinfo=UTC),
            closed_at=datetime.fromisoformat(row[11]).replace(tzinfo=UTC) if row[11] else None,
            equity_after=Decimal(row[12]) if row[12] else None,
            mode=row[13],
        )

    def save(self, record: TradeRecord) -> None:
        """Save a TradeRecord."""
        query = """
        INSERT OR REPLACE INTO trade_history (
            trade_id, setup_type, symbol, direction, entry_price, exit_price,
            quantity, risk_r, realized_r, pnl, opened_at, closed_at,
            equity_after, mode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            record.trade_id,
            record.setup_type,
            record.symbol,
            record.direction,
            str(record.entry_price),
            str(record.exit_price) if record.exit_price is not None else None,
            str(record.quantity),
            str(record.risk_r),
            str(record.realized_r) if record.realized_r is not None else None,
            str(record.pnl) if record.pnl is not None else None,
            record.opened_at.isoformat(),
            record.closed_at.isoformat() if record.closed_at else None,
            str(record.equity_after) if record.equity_after is not None else None,
            record.mode,
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(query, params)

    def get_all(self, mode: str | None = None) -> Sequence[TradeRecord]:
        """Get all trade records, optionally filtered by mode."""
        query = "SELECT * FROM trade_history"
        params: list[str] = []
        if mode:
            query += " WHERE mode = ?"
            params.append(mode)
        query += " ORDER BY opened_at ASC"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def get_recent(self, window: int, mode: str | None = None) -> Sequence[TradeRecord]:
        """Get the N most recent trade records."""
        query = "SELECT * FROM trade_history"
        params: list[str | int] = []
        if mode:
            query += " WHERE mode = ?"
            params.append(mode)
        query += " ORDER BY opened_at DESC LIMIT ?"
        params.append(window)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            # Re-sort to chronological order after fetching the recent N
            rows = cursor.fetchall()
            return [self._row_to_record(row) for row in reversed(rows)]
