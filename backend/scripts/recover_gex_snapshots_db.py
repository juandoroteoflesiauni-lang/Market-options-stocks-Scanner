"""F0–F2: backup forense, recuperación parcial y DBs separadas.

Uso:
    python -m backend.scripts.recover_gex_snapshots_db
    python -m backend.scripts.recover_gex_snapshots_db --skip-backup  # si ya hay backup
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.config.sqlite_db_paths import (
    CORRUPT_BACKUP_DIR,
    OPTIONS_GEX_SNAPSHOTS_DB,
    PREDICTIONS_DB,
)
from backend.infrastructure.sqlite_health import apply_sqlite_pragmas, quick_check
from backend.services.builder_state_store import BuilderStateStore
from backend.services.options_gex_snapshot_store import OptionsGexSnapshotStore
from backend.services.prediction_logger import PredictionLogger

logger = logging.getLogger(__name__)

_GEX_COLUMNS = (
    "snapshot_id",
    "symbol",
    "as_of",
    "source_tier",
    "data_quality_score",
    "provider",
    "features_json",
    "snapshot_json",
    "created_at",
)


def _find_sqlite3_cli() -> str | None:
    candidates = [
        "sqlite3",
        r"C:\Program Files\SQLite\sqlite3.exe",
        r"C:\sqlite\sqlite3.exe",
    ]
    for cmd in candidates:
        try:
            proc = subprocess.run(
                [cmd, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0:
                return cmd
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
    return None


def _forensic_backup(source: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{source.name}.{ts}.bak"
    shutil.copy2(source, dest)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{source}{suffix}")
        if sidecar.exists():
            shutil.copy2(sidecar, backup_dir / f"{sidecar.name}.{ts}.bak")
    logger.info("backup_ok src=%s dst=%s", source, dest)
    return dest


def _recover_via_sqlite3_cli(sqlite3_bin: str, corrupt: Path, recovered: Path) -> bool:
    """``sqlite3 corrupt.db \".recover\" | sqlite3 recovered.db``"""
    recovered.parent.mkdir(parents=True, exist_ok=True)
    if recovered.exists():
        recovered.unlink()
    try:
        recover = subprocess.run(
            [sqlite3_bin, str(corrupt), ".recover"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if recover.returncode != 0 or not recover.stdout.strip():
            logger.warning("sqlite3_recover_empty_or_failed rc=%s", recover.returncode)
            return False
        import_proc = subprocess.run(
            [sqlite3_bin, str(recovered)],
            input=recover.stdout,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if import_proc.returncode != 0:
            logger.warning("sqlite3_import_failed stderr=%s", import_proc.stderr[:200])
            return False
        return quick_check(recovered)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("sqlite3_recover_exc error=%s", exc)
        return False


def _recover_gex_rows_best_effort(corrupt: Path) -> list[tuple[Any, ...]]:
    """Intenta leer filas de options_gex_snapshots saltando páginas dañadas."""
    rows: list[tuple[Any, ...]] = []
    cols = ", ".join(_GEX_COLUMNS)
    try:
        uri = f"file:{corrupt.as_posix()}?mode=ro&immutable=1"
        con = sqlite3.connect(uri, uri=True, timeout=10.0)
    except sqlite3.DatabaseError as exc:
        logger.warning("best_effort_open_failed error=%s", exc)
        return rows

    try:
        try:
            max_rowid = con.execute(
                "SELECT MAX(rowid) FROM options_gex_snapshots"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            logger.warning("best_effort_max_rowid_failed error=%s", exc)
            return rows

        upper = int(max_rowid[0] or 0) if max_rowid else 0
        if upper <= 0:
            return rows
        step = 50
        for start in range(1, upper + 1, step):
            end = min(start + step - 1, upper)
            try:
                chunk = con.execute(
                    f"SELECT {cols} FROM options_gex_snapshots "
                    "WHERE rowid BETWEEN ? AND ?",
                    (start, end),
                ).fetchall()
                rows.extend(chunk)
            except sqlite3.DatabaseError as exc:
                logger.debug("chunk_skip rowid=%s-%s error=%s", start, end, exc)
    finally:
        con.close()
    return rows


def _import_gex_rows(rows: list[tuple[Any, ...]], target_db: Path) -> int:
    if not rows:
        return 0
    store = OptionsGexSnapshotStore(db_path=target_db)
    store._init_db()
    inserted = 0
    placeholders = ", ".join("?" for _ in _GEX_COLUMNS)
    col_list = ", ".join(_GEX_COLUMNS)
    sql = (
        f"INSERT OR IGNORE INTO options_gex_snapshots ({col_list}) "
        f"VALUES ({placeholders})"
    )
    with sqlite3.connect(target_db) as conn:
        apply_sqlite_pragmas(conn)
        for row in rows:
            cur = conn.execute(sql, row)
            if cur.rowcount > 0:
                inserted += 1
        conn.commit()
    return inserted


def _copy_gex_from_recovered(recovered: Path, target_db: Path) -> int:
    if not recovered.exists():
        return 0
    cols = ", ".join(_GEX_COLUMNS)
    uri = f"file:{recovered.as_posix()}?mode=ro"
    try:
        src = sqlite3.connect(uri, uri=True, timeout=10.0)
    except sqlite3.DatabaseError:
        return 0
    try:
        tables = {
            r[0]
            for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "options_gex_snapshots" not in tables:
            return 0
        rows = src.execute(f"SELECT {cols} FROM options_gex_snapshots").fetchall()
    finally:
        src.close()
    return _import_gex_rows(list(rows), target_db)


def _init_fresh_predictions_db(path: Path) -> None:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    PredictionLogger(db_path=path)
    BuilderStateStore(predictions_db=path).ensure_schema()
    with sqlite3.connect(path) as conn:
        apply_sqlite_pragmas(conn)
        conn.commit()


def _init_fresh_gex_db(path: Path) -> None:
    if path.exists():
        path.unlink()
    OptionsGexSnapshotStore(db_path=path)._init_db()


def run_recovery(*, skip_backup: bool = False, corrupt_path: Path | None = None) -> dict[str, Any]:
    corrupt = corrupt_path or PREDICTIONS_DB
    report: dict[str, Any] = {
        "timestamp_utc": datetime.now(tz=UTC).isoformat(),
        "corrupt_source": str(corrupt),
        "backup_path": None,
        "recovery_method": None,
        "gex_rows_recovered": 0,
        "gex_rows_imported": 0,
        "options_gex_db": str(OPTIONS_GEX_SNAPSHOTS_DB),
        "predictions_db": str(PREDICTIONS_DB),
        "integrity": {},
    }

    if not corrupt.exists():
        logger.warning("corrupt_db_missing path=%s — solo crea DBs frescas", corrupt)
    elif not skip_backup:
        report["backup_path"] = str(_forensic_backup(corrupt, CORRUPT_BACKUP_DIR))

    rows_to_import: list[tuple[Any, ...]] = []
    recovered_tmp = CORRUPT_BACKUP_DIR / "recovered_tmp.sqlite3"
    sqlite3_bin = _find_sqlite3_cli()

    if corrupt.exists():
        if sqlite3_bin and _recover_via_sqlite3_cli(sqlite3_bin, corrupt, recovered_tmp):
            report["recovery_method"] = "sqlite3_cli_recover"
            cols = ", ".join(_GEX_COLUMNS)
            uri = f"file:{recovered_tmp.as_posix()}?mode=ro"
            try:
                src = sqlite3.connect(uri, uri=True, timeout=10.0)
                try:
                    rows_to_import = list(
                        src.execute(f"SELECT {cols} FROM options_gex_snapshots").fetchall()
                    )
                finally:
                    src.close()
            except sqlite3.DatabaseError:
                rows_to_import = _recover_gex_rows_best_effort(corrupt)
                report["recovery_method"] = "sqlite3_cli_read_failed_best_effort"
        else:
            report["recovery_method"] = (
                "python_best_effort" if not sqlite3_bin else "sqlite3_cli_failed_best_effort"
            )
            rows_to_import = _recover_gex_rows_best_effort(corrupt)
        report["gex_rows_recovered"] = len(rows_to_import)
    else:
        report["recovery_method"] = "fresh_only"

    _init_fresh_gex_db(OPTIONS_GEX_SNAPSHOTS_DB)
    _init_fresh_predictions_db(PREDICTIONS_DB)
    report["gex_rows_imported"] = _import_gex_rows(rows_to_import, OPTIONS_GEX_SNAPSHOTS_DB)

    report["integrity"] = {
        "options_gex_snapshots": quick_check(OPTIONS_GEX_SNAPSHOTS_DB),
        "predictions": quick_check(PREDICTIONS_DB),
    }
    with sqlite3.connect(OPTIONS_GEX_SNAPSHOTS_DB) as conn:
        report["gex_row_count"] = conn.execute(
            "SELECT COUNT(*) FROM options_gex_snapshots"
        ).fetchone()[0]

    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(description="Recuperación DB GEX + separación")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--corrupt", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    report = run_recovery(skip_backup=args.skip_backup, corrupt_path=args.corrupt)
    out = args.out or (
        _ROOT / "reports" / f"db_recovery_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nReporte: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
