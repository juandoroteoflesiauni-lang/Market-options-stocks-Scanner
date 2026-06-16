from __future__ import annotations
from typing import Any
"""Factor history store for scanner conviction attribution.

Persists factor snapshots to SQLite for historical percentile calculation.
One snapshot per symbol per calendar day to avoid inflation.
"""


import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import MarketScannerRow

logger = get_logger(__name__)

DB_PATH = os.getenv(
    "SCANNER_FACTOR_HISTORY_DB",
    "backend/data/scanner_factor_history.db",
)
MIN_SAMPLES = int(os.getenv("SCANNER_FACTOR_HISTORY_MIN_SAMPLES", "20"))
LOOKBACK_DAYS = int(os.getenv("SCANNER_FACTOR_HISTORY_LOOKBACK_DAYS", "1095"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS factor_snapshots (
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,
    factor_key TEXT NOT NULL,
    loading REAL NOT NULL,
    scanner_score REAL NOT NULL,
    conviction_score REAL,
    PRIMARY KEY (symbol, as_of, factor_key)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_symbol_factor_asof
    ON factor_snapshots (symbol, factor_key, as_of);
"""


def _ensure_db() -> None:
    """Create DB and schema if missing."""
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _db_context() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


@contextmanager
def _db_context():

    """Managed SQLite connection."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def append_snapshot(
    row: MarketScannerRow,
    *,
    factor_loadings: dict[str, float],
    conviction_score: float | None = None,
) -> None:
    """Persist one snapshot for this symbol (idempotent by calendar day).

    Args:
        row: Scanner row with symbol and scanner_score
        factor_loadings: Factor key → loading dict (from attribution)
        conviction_score: Optional conviction score
    """
    _ensure_db()
    as_of_date = datetime.now(UTC).date().isoformat()
    rows_to_insert: list[tuple[str, str, str, float, float, float | None]] = []
    for factor_key, loading in factor_loadings.items():
        rows_to_insert.append(
            (
                row.symbol,
                as_of_date,
                factor_key,
                loading,
                row.scanner_score,
                conviction_score,
            )
        )
    if not rows_to_insert:
        return

    with _db_context() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO factor_snapshots
                (symbol, as_of, factor_key, loading, scanner_score, conviction_score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        conn.commit()
    logger.info(
        "Persisted %d factor snapshots for %s (date=%s)",
        len(rows_to_insert),
        row.symbol,
        as_of_date,
    )


def append_snapshots_batch(
    rows: list[MarketScannerRow],
    *,
    factor_loadings_by_symbol: dict[str, dict[str, float]],
) -> None:
    """Batch persist snapshots for multiple rows."""
    for row in rows:
        loadings = factor_loadings_by_symbol.get(row.symbol, {})
        if loadings:
            append_snapshot(
                row,
                factor_loadings=loadings,
                conviction_score=row.conviction_score,
            )


def get_percentiles(
    symbol: str,
    factor_keys: list[str],
    *,
    lookback_days: int = LOOKBACK_DAYS,
    min_samples: int = MIN_SAMPLES,
) -> dict[str, float | None]:
    """Calculate historical percentiles for current loadings vs history.

    Args:
        symbol: Symbol to query
        factor_keys: Factors to compute percentiles for
        lookback_days: Historical window
        min_samples: Minimum samples required (else None)

    Returns:
        Dict mapping factor_key to percentile (0-100) or None if insufficient data
    """
    _ensure_db()
    cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).date().isoformat()
    result: dict[str, float | None] = {}

    with _db_context() as conn:
        for factor_key in factor_keys:
            cursor = conn.execute(
                """
                SELECT loading FROM factor_snapshots
                WHERE symbol = ? AND factor_key = ? AND as_of >= ?
                ORDER BY as_of ASC
                """,
                (symbol, factor_key, cutoff),
            )
            historical = [row["loading"] for row in cursor.fetchall()]
            if len(historical) < min_samples:
                result[factor_key] = None
            else:
                current_loading = historical[-1] if historical else 0.0
                below = sum(1 for val in historical if val < current_loading)
                percentile = (below / len(historical)) * 100.0
                result[factor_key] = round(percentile, 2)

    return result


def get_history_stats(symbol: str) -> dict[str, Any]:
    """Diagnostic stats for one symbol's history."""
    _ensure_db()
    with _db_context() as conn:
        cursor = conn.execute(
            """
            SELECT
                COUNT(DISTINCT as_of) as snapshot_days,
                COUNT(DISTINCT factor_key) as factor_count,
                MIN(as_of) as earliest,
                MAX(as_of) as latest
            FROM factor_snapshots
            WHERE symbol = ?
            """,
            (symbol,),
        )
        row = cursor.fetchone()
        return {
            "snapshot_days": row["snapshot_days"] if row else 0,
            "factor_count": row["factor_count"] if row else 0,
            "earliest": row["earliest"] if row else None,
            "latest": row["latest"] if row else None,
        }
