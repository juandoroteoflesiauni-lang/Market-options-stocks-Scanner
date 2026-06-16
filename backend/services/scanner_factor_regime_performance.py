from __future__ import annotations
from typing import Any
"""Fase 2: factor × regime forward-return performance store.

Persists, on each scan, one open outcome row per ``(symbol, factor_key, scan_id)``
with the price at scan and the active desk regime. On the *next* scan for a
symbol, the immediately-prior scan's open rows are closed by stamping the
realized forward return (price at scan N vs N+1).

Outcomes are snapshot-based only — this module never reads ``predictions.db`` and
never fabricates returns. Rolling statistics per ``(factor_key × regime)`` gate
on a minimum sample count before they are considered actionable evidence.

Idempotency: ``CREATE TABLE IF NOT EXISTS`` + ``INSERT OR IGNORE`` on the
composite primary key. Re-running the same scan never duplicates rows and never
deletes history.
"""


import math
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import DeskRegimeLabel, FactorRegimeStat

logger = get_logger(__name__)

DB_PATH = os.getenv(
    "SCANNER_REGIME_HISTORY_DB",
    "backend/data/scanner_regime_history.db",
)
LOOKBACK_DAYS = int(os.getenv("SCANNER_FACTOR_REGIME_LOOKBACK_DAYS", "365"))
MIN_SAMPLES = int(os.getenv("SCANNER_FACTOR_REGIME_MIN_SAMPLES", "30"))

# Trading days per year for simple Sharpe annualization (scan cadence proxy).
_TRADING_DAYS = 252.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS factor_regime_outcomes (
    symbol TEXT NOT NULL,
    factor_key TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    desk_regime TEXT NOT NULL,
    loading REAL NOT NULL,
    contribution_pct REAL NOT NULL,
    price_at_scan REAL,
    forward_return REAL,
    forward_filled_at TEXT,
    PRIMARY KEY (symbol, factor_key, scan_id)
);

CREATE INDEX IF NOT EXISTS idx_regime_factor_ts
    ON factor_regime_outcomes (factor_key, desk_regime, ts);

CREATE INDEX IF NOT EXISTS idx_symbol_open
    ON factor_regime_outcomes (symbol, forward_return);
"""


@contextmanager
def _db_context() -> Iterator[sqlite3.Connection]:
    """Managed SQLite connection (schema ensured on entry)."""
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
    finally:
        conn.close()


def persist_regime_outcomes(
    *,
    scan_id: str,
    desk_regime: str,
    factor_rows: list[dict[str, Any]],
) -> int:
    """Persist open outcome rows and backfill the prior scan's forward returns.

    Args:
        scan_id: Unique id for this scan run (one per scan).
        desk_regime: Active desk regime label for this scan.
        factor_rows: One dict per (symbol, factor_key) with keys
            ``symbol``, ``factor_key``, ``loading``, ``contribution_pct``,
            ``price_at_scan`` (price may be None).

    Returns:
        Number of open rows inserted this scan.
    """
    if not factor_rows:
        return 0

    now = datetime.now(UTC)
    ts = now.isoformat()
    as_of_date = now.date().isoformat()

    inserted = 0
    backfilled = 0
    with _db_context() as conn:
        # 1) Backfill the immediately-prior open scan for each symbol present now.
        prices_by_symbol: dict[str, float] = {}
        for fr in factor_rows:
            price = fr.get("price_at_scan")
            symbol = str(fr.get("symbol") or "")
            if symbol and isinstance(price, int | float) and math.isfinite(float(price)):
                prices_by_symbol[symbol] = float(price)

        for symbol, current_price in prices_by_symbol.items():
            if current_price <= 0:
                continue
            prior = conn.execute(
                """
                SELECT scan_id FROM factor_regime_outcomes
                WHERE symbol = ? AND forward_return IS NULL
                    AND price_at_scan IS NOT NULL AND scan_id != ?
                ORDER BY ts DESC LIMIT 1
                """,
                (symbol, scan_id),
            ).fetchone()
            if prior is None:
                continue
            prior_scan_id = prior["scan_id"]
            rows = conn.execute(
                """
                SELECT factor_key, price_at_scan FROM factor_regime_outcomes
                WHERE symbol = ? AND scan_id = ? AND forward_return IS NULL
                """,
                (symbol, prior_scan_id),
            ).fetchall()
            for r in rows:
                prior_price = r["price_at_scan"]
                if not isinstance(prior_price, int | float) or float(prior_price) <= 0:
                    continue
                fwd = (current_price - float(prior_price)) / float(prior_price)
                conn.execute(
                    """
                    UPDATE factor_regime_outcomes
                    SET forward_return = ?, forward_filled_at = ?
                    WHERE symbol = ? AND factor_key = ? AND scan_id = ?
                    """,
                    (round(fwd, 8), ts, symbol, r["factor_key"], prior_scan_id),
                )
                backfilled += 1

        # 2) Insert this scan's open rows (idempotent on PK).
        to_insert: list[tuple[Any, ...]] = []
        for fr in factor_rows:
            symbol = str(fr.get("symbol") or "")
            factor_key = str(fr.get("factor_key") or "")
            if not symbol or not factor_key:
                continue
            price = fr.get("price_at_scan")
            price_val = (
                float(price)
                if isinstance(price, int | float) and math.isfinite(float(price))
                else None
            )
            to_insert.append(
                (
                    symbol,
                    factor_key,
                    scan_id,
                    ts,
                    as_of_date,
                    desk_regime,
                    float(fr.get("loading", 0.0) or 0.0),
                    float(fr.get("contribution_pct", 0.0) or 0.0),
                    price_val,
                )
            )
        if to_insert:
            cur = conn.executemany(
                """
                INSERT OR IGNORE INTO factor_regime_outcomes
                    (symbol, factor_key, scan_id, ts, as_of_date, desk_regime,
                     loading, contribution_pct, price_at_scan)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                to_insert,
            )
            inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(to_insert)
        conn.commit()

    logger.info(
        "regime_outcomes.persisted scan_id=%s regime=%s inserted=%d backfilled=%d",
        scan_id,
        desk_regime,
        inserted,
        backfilled,
    )
    return inserted


def _sharpe_annualized(returns: list[float]) -> float:
    """Simple annualized Sharpe of directional returns (0 std → 0)."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std <= 1e-12:
        return 0.0
    return float((mean / std) * math.sqrt(_TRADING_DAYS))


def get_factor_regime_stat(
    factor_key: str,
    regime: str,
    *,
    lookback_days: int = LOOKBACK_DAYS,
    min_samples: int = MIN_SAMPLES,
) -> FactorRegimeStat:
    """Rolling forward-return stats for one (factor_key × regime) pair.

    Returns a stat with ``sufficient=False`` when fewer than ``min_samples``
    closed outcomes exist (caller should treat as insufficient history).
    Directional return = ``sign(loading) * forward_return`` so a factor that
    loads negative and price falls counts as a win for that factor's tilt.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()
    directional: list[float] = []
    with _db_context() as conn:
        rows = conn.execute(
            """
            SELECT loading, forward_return FROM factor_regime_outcomes
            WHERE factor_key = ? AND desk_regime = ?
                AND forward_return IS NOT NULL AND ts >= ?
            """,
            (factor_key, regime, cutoff),
        ).fetchall()
        for r in rows:
            loading = r["loading"]
            fwd = r["forward_return"]
            if not isinstance(fwd, int | float):
                continue
            sign = 1.0 if (loading is None or float(loading) >= 0) else -1.0
            directional.append(sign * float(fwd))

    sample_count = len(directional)
    regime_label: DeskRegimeLabel = regime  # type: ignore[assignment]
    if sample_count == 0:
        return FactorRegimeStat(
            factor_key=factor_key,
            regime=regime_label,
            sample_count=0,
            win_rate=0.0,
            avg_forward_return=0.0,
            sharpe_annualized=0.0,
            lookback_days=lookback_days,
            sufficient=False,
        )

    wins = sum(1 for d in directional if d > 0)
    win_rate = wins / sample_count
    avg_return = sum(directional) / sample_count
    sharpe = _sharpe_annualized(directional)
    return FactorRegimeStat(
        factor_key=factor_key,
        regime=regime_label,
        sample_count=sample_count,
        win_rate=round(win_rate, 4),
        avg_forward_return=round(avg_return, 6),
        sharpe_annualized=round(sharpe, 4),
        lookback_days=lookback_days,
        sufficient=sample_count >= min_samples,
    )


def get_stats_for_factors(
    factor_keys: list[str],
    regime: str,
    *,
    lookback_days: int = LOOKBACK_DAYS,
    min_samples: int = MIN_SAMPLES,
) -> dict[str, FactorRegimeStat]:
    """Batch helper: stat per factor key for one regime."""
    return {
        key: get_factor_regime_stat(
            key, regime, lookback_days=lookback_days, min_samples=min_samples
        )
        for key in factor_keys
    }
