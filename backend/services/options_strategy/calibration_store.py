"""Persistencia de reportes de calibración Options Strategy. # [PD-3][TH]"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.sqlite_db_paths import OPTIONS_STRATEGY_AUDIT_DB
from backend.infrastructure.sqlite_health import apply_sqlite_pragmas, ensure_healthy_or_quarantine
from backend.models.options_strategy import OptionsStrategyCalibrationReport

logger = get_logger(__name__)

_TABLE = "options_strategy_calibrations"


@dataclass(frozen=True)
class CalibrationPersistResult:
    calibration_id: str
    inserted: bool


class OptionsStrategyCalibrationStore:
    """Almacena reportes de calibración offline."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or OPTIONS_STRATEGY_AUDIT_DB)
        ensure_healthy_or_quarantine(self.db_path)

    def persist(self, report: OptionsStrategyCalibrationReport) -> CalibrationPersistResult:
        now = datetime.now(tz=UTC).isoformat()
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"""
                INSERT OR REPLACE INTO {_TABLE} (
                    calibration_id, observation_count, execute_rate,
                    report_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    report.calibration_id,
                    report.observation_count,
                    report.execute_rate,
                    json.dumps(report.model_dump(mode="json"), default=str),
                    now,
                ),
            )
            conn.commit()
            inserted = cur.rowcount > 0
        logger.info(
            "options_strategy_calibration.persisted id=%s observations=%s",
            report.calibration_id,
            report.observation_count,
        )
        return CalibrationPersistResult(
            calibration_id=report.calibration_id,
            inserted=inserted,
        )

    def latest(self) -> dict[str, Any] | None:
        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"""
                SELECT report_json FROM {_TABLE}
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            apply_sqlite_pragmas(conn)
            conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    calibration_id TEXT PRIMARY KEY,
                    observation_count INTEGER NOT NULL,
                    execute_rate REAL NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_options_strategy_calibrations_created
                    ON {_TABLE}(created_at);
                """
            )


__all__ = ["CalibrationPersistResult", "OptionsStrategyCalibrationStore"]
