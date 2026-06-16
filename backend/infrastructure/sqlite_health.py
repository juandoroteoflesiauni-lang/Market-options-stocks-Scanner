"""Utilidades de salud, cuarentena y checkpoint para SQLite. # [PD-6][TH]"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BUSY_TIMEOUT_MS = 5000


def apply_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    """PRAGMAs estándar para writers append-only con WAL."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")


def quick_check(db_path: Path) -> bool:
    """Devuelve True si ``PRAGMA quick_check`` pasa."""
    if not db_path.exists():
        return True
    try:
        with sqlite3.connect(db_path, timeout=5.0) as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row and str(row[0]).lower() == "ok")
    except sqlite3.DatabaseError as exc:
        logger.warning("sqlite_health.quick_check_failed path=%s error=%s", db_path, exc)
        return False


def quarantine_corrupt_db(db_path: Path) -> Path | None:
    """Renombra DB dañada a ``*.corrupt.<ts>`` y devuelve la ruta cuarentenada."""
    if not db_path.exists():
        return None
    ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    quarantined = db_path.with_name(f"{db_path.stem}.corrupt.{ts}{db_path.suffix}")
    db_path.rename(quarantined)
    logger.error("sqlite_health.quarantined src=%s dst=%s", db_path, quarantined)
    return quarantined


def ensure_healthy_or_quarantine(db_path: Path) -> bool:
    """Si quick_check falla, cuarentena y deja listo para recrear esquema vacío."""
    if quick_check(db_path):
        return True
    quarantine_corrupt_db(db_path)
    return False


def wal_checkpoint_truncate(db_path: Path) -> None:
    """Checkpoint WAL en shutdown limpio."""
    if not db_path.exists():
        return
    try:
        with sqlite3.connect(db_path, timeout=5.0) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError as exc:
        logger.warning("sqlite_health.checkpoint_failed path=%s error=%s", db_path, exc)


def vacuum_backup(db_path: Path, backup_dir: Path) -> Path | None:
    """Copia consistente vía ``VACUUM INTO`` (requiere checkpoint previo)."""
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d")
    target = backup_dir / f"{db_path.stem}_{stamp}{db_path.suffix}"
    wal_checkpoint_truncate(db_path)
    try:
        with sqlite3.connect(db_path, timeout=30.0) as conn:
            conn.execute(f"VACUUM INTO '{target.as_posix()}'")
        logger.info("sqlite_health.backup_ok src=%s dst=%s", db_path, target)
        return target
    except sqlite3.DatabaseError as exc:
        logger.warning("sqlite_health.backup_failed path=%s error=%s", db_path, exc)
        return None


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Escritura atómica genérica (joblib/json)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_joblib_dump(obj: Any, path: Path) -> None:
    """Evita truncado parcial de artefactos joblib."""
    import joblib

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    joblib.dump(obj, tmp)
    os.replace(tmp, path)


__all__ = [
    "apply_sqlite_pragmas",
    "atomic_joblib_dump",
    "atomic_write_bytes",
    "ensure_healthy_or_quarantine",
    "quick_check",
    "quarantine_corrupt_db",
    "vacuum_backup",
    "wal_checkpoint_truncate",
]
