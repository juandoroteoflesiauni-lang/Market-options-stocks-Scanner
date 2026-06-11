"""
backend/layer_3_specialists/ia_probabilistico/engines/fear_greed_storage.py
════════════════════════════════════════════════════════════════════════════════
Fear & Greed Storage — Historical tracking for backtesting and analysis.

Stores Fear & Greed scores over time to enable:
- Historical analysis and trends
- Backtesting against CNN Fear & Greed
- Correlation studies
- Performance attribution
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FearGreedHistoryEntry:
    """Historical Fear & Greed record."""

    timestamp: datetime
    symbol: str
    score: float
    label: str
    data_quality: str
    factors: dict[str, float]
    event_risk_score: float | None


class FearGreedStorage:
    """
    SQLite-based storage for Fear & Greed historical data.
    """

    def __init__(self, db_path: str = "fear_greed_history.db"):
        """
        Initialize storage with database path.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Main history table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS fg_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                score REAL NOT NULL,
                label TEXT NOT NULL,
                data_quality TEXT NOT NULL,
                event_risk_score REAL,
                momentum REAL,
                strength REAL,
                volatility REAL,
                put_call REAL,
                credit REAL,
                safe_haven REAL,
                event_risk_factor REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Index for time-series queries
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fg_symbol_time
            ON fg_history(symbol, timestamp)
        """
        )

        # Index for score analysis
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fg_score
            ON fg_history(score)
        """
        )

        conn.commit()
        conn.close()
        logger.info(f"Fear & Greed storage initialized: {self.db_path}")

    def save(
        self,
        symbol: str,
        score: float,
        label: str,
        data_quality: str,
        factors: dict[str, float],
        event_risk_score: float | None = None,
        timestamp: datetime | None = None,
    ) -> int:
        """
        Save a Fear & Greed reading to history.

        Args:
            symbol: Symbol analyzed (e.g., SPY, AAPL)
            score: Composite FG score [0, 100]
            label: Human-readable label
            data_quality: Quality indicator
            factors: Dict of factor scores
            event_risk_score: NLP event risk score
            timestamp: Reading timestamp (default: now)

        Returns:
            Row ID of inserted record
        """
        if timestamp is None:
            timestamp = datetime.now()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO fg_history (
                timestamp, symbol, score, label, data_quality,
                event_risk_score,
                momentum, strength, volatility, put_call,
                credit, safe_haven, event_risk_factor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                timestamp.isoformat(),
                symbol.upper(),
                score,
                label,
                data_quality,
                event_risk_score,
                factors.get("momentum"),
                factors.get("strength"),
                factors.get("volatility"),
                factors.get("put_call"),
                factors.get("credit"),
                factors.get("safe_haven"),
                factors.get("event_risk"),
            ),
        )

        row_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.debug(f"Saved FG reading: {symbol}={score:.1f} ({label})")
        from typing import cast

        return cast(int, row_id)

    def get_history(
        self,
        symbol: str,
        days: int = 30,
        limit: int | None = None,
    ) -> list[FearGreedHistoryEntry]:
        """
        Retrieve historical Fear & Greed readings.

        Args:
            symbol: Symbol to query
            days: Number of days of history
            limit: Maximum number of records

        Returns:
            List of historical entries
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        query = """
            SELECT * FROM fg_history
            WHERE symbol = ?
            AND timestamp >= datetime('now', ?)
            ORDER BY timestamp DESC
        """

        params = [symbol.upper(), f"-{days} days"]

        if limit:
            query += f" LIMIT {int(limit)}"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_entry(row) for row in rows]

    def get_statistics(
        self,
        symbol: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """
        Get statistical summary of Fear & Greed readings.

        Args:
            symbol: Symbol to analyze
            days: Number of days of history

        Returns:
            Dict with statistics (mean, std, min, max, count)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                AVG(score) as mean_score,
                MIN(score) as min_score,
                MAX(score) as max_score,
                COUNT(*) as reading_count,
                AVG(momentum) as avg_momentum,
                AVG(volatility) as avg_volatility,
                AVG(event_risk_factor) as avg_event_risk
            FROM fg_history
            WHERE symbol = ?
            AND timestamp >= datetime('now', ?)
        """,
            (symbol.upper(), f"-{days} days"),
        )

        row = cursor.fetchone()
        conn.close()

        return {
            "mean_score": row[0] if row[0] else 0,
            "min_score": row[1] if row[1] else 0,
            "max_score": row[2] if row[2] else 0,
            "reading_count": row[3] if row[3] else 0,
            "avg_momentum": row[4] if row[4] else 0,
            "avg_volatility": row[5] if row[5] else 0,
            "avg_event_risk": row[6] if row[6] else 0,
        }

    def compare_with_cnn(
        self,
        cnn_data: list[dict[str, Any]],
        days: int = 30,
    ) -> dict[str, Any]:
        """
        Compare our FG scores with CNN Fear & Greed.

        Args:
            cnn_data: List of {date, score} dicts from CNN
            days: Days to analyze

        Returns:
            Correlation and error metrics
        """
        # Get our historical data
        history = self.get_history("SPY", days=days)

        if len(history) < 5 or len(cnn_data) < 5:
            return {
                "correlation": None,
                "mean_error": None,
                "message": "Insufficient data for comparison",
            }

        # Align by date (simplified - would need date matching logic)
        our_scores = [h.score for h in history[: len(cnn_data)]]
        cnn_scores = [d["score"] for d in cnn_data[: len(history)]]

        if len(our_scores) != len(cnn_scores):
            min_len = min(len(our_scores), len(cnn_scores))
            our_scores = our_scores[:min_len]
            cnn_scores = cnn_scores[:min_len]

        # Calculate correlation
        import numpy as np

        correlation = np.corrcoef(our_scores, cnn_scores)[0, 1]

        # Calculate mean absolute error
        mean_error = sum(abs(o - c) for o, c in zip(our_scores, cnn_scores, strict=False)) / len(our_scores)

        return {
            "correlation": float(correlation),
            "mean_error": float(mean_error),
            "our_mean": sum(our_scores) / len(our_scores),
            "cnn_mean": sum(cnn_scores) / len(cnn_scores),
            "sample_size": len(our_scores),
        }

    def _row_to_entry(self, row: tuple[Any, ...]) -> FearGreedHistoryEntry:
        """Convert database row to FearGreedHistoryEntry."""
        return FearGreedHistoryEntry(
            timestamp=datetime.fromisoformat(row[1]),
            symbol=row[2],
            score=row[3],
            label=row[4],
            data_quality=row[5],
            factors={
                "momentum": row[7],
                "strength": row[8],
                "volatility": row[9],
                "put_call": row[10],
                "credit": row[11],
                "safe_haven": row[12],
                "event_risk": row[13],
            },
            event_risk_score=row[6],
        )

    def clear_history(self, symbol: str | None = None) -> None:
        """
        Clear historical data.

        Args:
            symbol: If provided, clear only for this symbol
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if symbol:
            cursor.execute("DELETE FROM fg_history WHERE symbol = ?", (symbol.upper(),))
            logger.info(f"Cleared FG history for {symbol}")
        else:
            cursor.execute("DELETE FROM fg_history")
            logger.info("Cleared all FG history")

        conn.commit()
        conn.close()


# Global instance for shared use
_fg_storage: FearGreedStorage | None = None


def get_fg_storage(db_path: str = "fear_greed_history.db") -> FearGreedStorage:
    """Get or create global FearGreedStorage instance."""
    global _fg_storage
    if _fg_storage is None:
        _fg_storage = FearGreedStorage(db_path)
    return _fg_storage
