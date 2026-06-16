from __future__ import annotations
from typing import Any
"""Forward persistence for canonical Options/GEX live snapshots."""


import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.config.sqlite_db_paths import OPTIONS_GEX_SNAPSHOTS_DB
from backend.infrastructure.sqlite_health import (
    apply_sqlite_pragmas,
    ensure_healthy_or_quarantine,
)

DEFAULT_OPTIONS_GEX_SNAPSHOT_DB = OPTIONS_GEX_SNAPSHOTS_DB


@dataclass(frozen=True)
class OptionsGexSnapshotPersistResult:
    snapshot_id: str | None
    inserted: bool
    reason: str = "ok"


class OptionsGexSnapshotStore:
    """Persist canonical Options/GEX snapshots in the existing predictions DB."""

    def __init__(self, db_path: Path | str = DEFAULT_OPTIONS_GEX_SNAPSHOT_DB) -> None:
        self.db_path = Path(db_path)
        ensure_healthy_or_quarantine(self.db_path)

    def persist(self, snapshot: object) -> OptionsGexSnapshotPersistResult:
        payload = _as_dict(snapshot)
        features = _as_dict(payload.get("options_gex_features"))
        if not features:
            return OptionsGexSnapshotPersistResult(None, False, "missing_options_gex_features")

        symbol = str(payload.get("ticker") or payload.get("symbol") or "").upper().strip()
        if not symbol:
            return OptionsGexSnapshotPersistResult(None, False, "missing_symbol")
        as_of = str(payload.get("as_of") or "").strip()
        if not as_of:
            return OptionsGexSnapshotPersistResult(None, False, "missing_as_of")

        source_tier = str(features.get("source_tier") or "unknown")
        data_quality = _float_or_none(features.get("data_quality_score"))
        provider = str(
            features.get("provider")
            or _as_dict(payload.get("chain_quality")).get("provider")
            or "unknown"
        )
        snapshot_id = _snapshot_id(symbol, as_of, source_tier)
        now = datetime.now(tz=UTC).isoformat()

        self._init_db()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO options_gex_snapshots (
                    snapshot_id, symbol, as_of, source_tier, data_quality_score,
                    provider, features_json, snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    symbol,
                    as_of,
                    source_tier,
                    data_quality,
                    provider,
                    json.dumps(features, sort_keys=True),
                    json.dumps(payload, sort_keys=True, default=str),
                    now,
                ),
            )
            conn.commit()
            inserted = cur.rowcount > 0

        return OptionsGexSnapshotPersistResult(
            snapshot_id,
            inserted,
            "inserted" if inserted else "already_exists",
        )

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            apply_sqlite_pragmas(conn)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS options_gex_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    source_tier TEXT NOT NULL,
                    data_quality_score REAL,
                    provider TEXT,
                    features_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_options_gex_snapshots_symbol_asof
                    ON options_gex_snapshots(symbol, as_of);
                CREATE INDEX IF NOT EXISTS idx_options_gex_snapshots_tier
                    ON options_gex_snapshots(source_tier);
                """
            )


def _snapshot_id(symbol: str, as_of: str, source_tier: str) -> str:
    raw = f"options_gex_snapshot:{symbol}:{as_of}:{source_tier}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _as_dict(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")

        return dumped if isinstance(dumped, dict) else {}
    return {}


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
