"""Rutas canónicas de bases SQLite del backend. # [PD-8][TH]"""

from __future__ import annotations

from pathlib import Path

_BACKEND_DATA = Path(__file__).resolve().parent.parent / "data"

PREDICTIONS_DB: Path = _BACKEND_DATA / "predictions.db"
OPTIONS_GEX_SNAPSHOTS_DB: Path = _BACKEND_DATA / "options_gex_snapshots.sqlite3"
OPTIONS_STRATEGY_AUDIT_DB: Path = _BACKEND_DATA / "options_strategy_audit.sqlite3"
CORRUPT_BACKUP_DIR: Path = _BACKEND_DATA / "_corrupt_backup"
SQLITE_BACKUPS_DIR: Path = _BACKEND_DATA / "backups"

__all__ = [
    "CORRUPT_BACKUP_DIR",
    "OPTIONS_STRATEGY_AUDIT_DB",
    "OPTIONS_GEX_SNAPSHOTS_DB",
    "PREDICTIONS_DB",
    "SQLITE_BACKUPS_DIR",
]
