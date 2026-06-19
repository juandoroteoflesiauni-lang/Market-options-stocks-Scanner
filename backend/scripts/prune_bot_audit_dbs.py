"""Poda manual de DuckDB audits del bot y checkpoint (reclama disco). # [PD-3][TH]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config.logger_setup import get_logger
from backend.services.audit_duckdb_utils import (
    audit_retain_max_cycles,
    checkpoint_audit_db,
    connect_audit_duckdb,
    prune_audit_cycles,
)

logger = get_logger(__name__)

_TABLES = {
    "bingx": ("data/bingx_bot_audit.duckdb", "bingx_audit_cycles"),
    "alpaca": ("data/alpaca_bot_audit.duckdb", "alpaca_audit_cycles"),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune bot audit DuckDB files.")
    parser.add_argument(
        "--target",
        choices=("bingx", "alpaca", "both"),
        default="both",
        help="Which audit database to prune",
    )
    parser.add_argument(
        "--retain",
        type=int,
        default=None,
        help="Max cycles to keep (default: AUDIT_RETAIN_MAX_CYCLES env)",
    )
    parser.add_argument(
        "--checkpoint-only",
        action="store_true",
        help="Run CHECKPOINT without deleting rows",
    )
    return parser.parse_args()


def _prune_one(db_path: Path, table: str, *, retain: int, checkpoint_only: bool) -> dict[str, int]:
    if not db_path.exists():
        logger.warning("prune_audit.missing path=%s", db_path)
        return {"before": 0, "deleted": 0, "after": 0}

    conn = connect_audit_duckdb(db_path, read_only=False)
    try:
        before_row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        before = int(before_row[0]) if before_row else 0
        deleted = 0
        if not checkpoint_only:
            deleted = prune_audit_cycles(conn, table, retain_max=retain)
        else:
            conn.execute("CHECKPOINT")
        after_row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        after = int(after_row[0]) if after_row else before
    finally:
        conn.close()

    checkpoint_audit_db(db_path)
    size_mb = db_path.stat().st_size / (1024 * 1024)
    logger.info(
        "prune_audit.done path=%s table=%s before=%d deleted=%d after=%d size_mb=%.1f",
        db_path,
        table,
        before,
        deleted,
        after,
        size_mb,
    )
    return {"before": before, "deleted": deleted, "after": after}


def main() -> int:
    args = _parse_args()
    retain = args.retain if args.retain is not None else audit_retain_max_cycles()
    targets = ("bingx", "alpaca") if args.target == "both" else (args.target,)
    for name in targets:
        rel_path, table = _TABLES[name]
        _prune_one(Path(rel_path), table, retain=retain, checkpoint_only=args.checkpoint_only)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
