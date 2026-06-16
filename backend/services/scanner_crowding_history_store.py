from __future__ import annotations
"""SQLite persistence for Fase 3 crowding snapshots (validation / audit)."""


import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import FactorCrowdingIndex, MarketScannerRow

logger = get_logger(__name__)

DB_PATH = os.getenv(
    "SCANNER_CROWDING_HISTORY_DB",
    "backend/data/scanner_crowding_history.db",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS universe_crowding_snapshots (
    scan_date TEXT NOT NULL,
    factor_key TEXT NOT NULL,
    crowding_percentile REAL,
    components_json TEXT NOT NULL,
    PRIMARY KEY (scan_date, factor_key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS row_crowding_snapshots (
    scan_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    crowded_factors_json TEXT NOT NULL,
    penalty REAL NOT NULL,
    conviction_before REAL,
    conviction_after REAL,
    PRIMARY KEY (scan_id, symbol)
) WITHOUT ROWID;
"""


def _ensure_db() -> None:
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _db_context() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


@contextmanager
def _db_context():

    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def append_universe_snapshots(indices: list[FactorCrowdingIndex]) -> None:
    if not indices:
        return
    _ensure_db()
    scan_date = datetime.now(UTC).date().isoformat()
    rows: list[tuple[str, str, float | None, str]] = []
    for idx in indices:
        comp = json.dumps(
            {
                "concentration_score": idx.concentration_score,
                "loading_dispersion": idx.loading_dispersion,
                "pairwise_corr_mean": idx.pairwise_corr_mean,
                "data_tier": idx.data_tier,
            }
        )
        rows.append((scan_date, idx.factor_key, idx.crowding_percentile, comp))
    with _db_context() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO universe_crowding_snapshots
                (scan_date, factor_key, crowding_percentile, components_json)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def append_row_snapshots(scan_id: str, rows: list[MarketScannerRow]) -> None:
    if not scan_id or not rows:
        return
    _ensure_db()
    to_insert: list[tuple[str, str, str, float, float | None, float | None]] = []
    for row in rows:
        cb = row.crowding_breakdown
        if cb is None:
            continue
        crowded = [
            {"factor_key": f.factor_key, "crowding_percentile": f.crowding_percentile}
            for f in cb.crowded_factors
        ]
        conv = row.conviction_breakdown
        before = conv.conviction_score_raw if conv else row.conviction_score
        after = row.conviction_score
        to_insert.append(
            (
                scan_id,
                row.symbol,
                json.dumps(crowded),
                cb.crowding_penalty,
                before,
                after,
            )
        )
    if not to_insert:
        return
    with _db_context() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO row_crowding_snapshots
                (scan_id, symbol, crowded_factors_json, penalty,
                 conviction_before, conviction_after)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            to_insert,
        )
        conn.commit()
