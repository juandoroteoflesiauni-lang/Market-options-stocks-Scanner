"""Utilidades DuckDB para audit bots: compactación, retención y conexión segura. # [PD-3][TH]"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb

_AUDIT_COMPACT_ENV = "AUDIT_COMPACT_PAYLOAD"
_AUDIT_RETAIN_MAX_ENV = "AUDIT_RETAIN_MAX_CYCLES"
_DEFAULT_RETAIN_MAX = 1500

# Claves voluminosas que no aportan al ledger de operaciones.
_OMIT_KEYS: frozenset[str] = frozenset(
    {
        "klines",
        "candles",
        "ohlcv",
        "bars",
        "intraday_bars",
        "footprint",
        "footprint_payload",
        "structure_payload",
        "volume_payload",
        "hmm_payload",
        "parsed_bids",
        "parsed_asks",
        "bids",
        "asks",
        "depth",
        "institutional_research",
        "institutional_research_snapshot",
        "engine_decision_payload",
        "technical_terminal",
        "technical_payload",
        "lob_analysis",
        "market_data",
        "orderbook",
        "indicators",
        "raw",
    }
)

_ANALYSIS_SUMMARY_KEYS: tuple[str, ...] = (
    "venue_symbol",
    "underlying_symbol",
    "market_type",
    "readiness_score",
    "symbol",
)


def audit_compact_payload_enabled() -> bool:
    """True por defecto: guardar resumen, no blobs completos por ciclo."""
    return os.getenv(_AUDIT_COMPACT_ENV, "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def audit_retain_max_cycles() -> int:
    raw = os.getenv(_AUDIT_RETAIN_MAX_ENV, str(_DEFAULT_RETAIN_MAX)).strip()
    try:
        return max(100, int(raw))
    except ValueError:
        return _DEFAULT_RETAIN_MAX


def connect_audit_duckdb(
    db_path: str | Path, *, read_only: bool = False
) -> duckdb.DuckDBPyConnection:
    """Abre DuckDB de audit; lectores usan ``read_only`` para evitar lock con el daemon."""
    path = str(db_path)
    if path == ":memory:":
        return duckdb.connect(path)
    return duckdb.connect(path, read_only=read_only)


def _omit_marker(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {"_omitted": True, "count": len(value)}
    if isinstance(value, dict):
        return {"_omitted": True, "keys": list(value.keys())[:12]}
    return {"_omitted": True}


def _slim_candidate_analysis(row: dict[str, Any]) -> dict[str, Any]:
    slim = {k: row[k] for k in _ANALYSIS_SUMMARY_KEYS if k in row}
    for block in ("predictive", "technical", "options", "venue", "underlying", "l2"):
        block_val = row.get(block)
        if isinstance(block_val, dict):
            slim[block] = {
                k: block_val[k]
                for k in ("status", "source", "reason", "trend_direction", "directional_bias")
                if k in block_val
            }
    return slim or {"venue_symbol": row.get("venue_symbol"), "_slim": True}


def _compact_nested(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _OMIT_KEYS:
                out[key] = _omit_marker(item)
                continue
            out[key] = _compact_nested(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        if len(value) > 200:
            return {"_omitted": True, "count": len(value)}
        return [_compact_nested(item, depth=depth + 1) for item in value]
    return value


def compact_bot_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce payload de ciclo para auditoría sin perder ledger de ejecución."""
    if not audit_compact_payload_enabled():
        return payload

    out = dict(payload)
    for block in ("candidate_analyses", "analyses"):
        rows = out.get(block)
        if isinstance(rows, list):
            out[block] = [
                _slim_candidate_analysis(row) if isinstance(row, dict) else row for row in rows
            ]

    if isinstance(out.get("l2_snapshots"), dict):
        symbols = list(out["l2_snapshots"].keys())
        out["l2_snapshots"] = {"_omitted": True, "symbols": symbols[:50], "count": len(symbols)}

    if isinstance(out.get("snapshots"), list) and len(out["snapshots"]) > 0:
        out["snapshots"] = _compact_nested(out["snapshots"])

    compacted = _compact_nested(out)
    if isinstance(compacted, dict):
        compacted["_audit_compact"] = True
        return compacted
    return payload


def payload_json_for_audit(payload: dict[str, Any]) -> str:
    """Serializa payload compactado para columna VARCHAR."""
    return json.dumps(compact_bot_audit_payload(payload), default=str, separators=(",", ":"))


def prune_audit_cycles(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    *,
    retain_max: int | None = None,
) -> int:
    """Elimina ciclos antiguos conservando los ``retain_max`` más recientes."""
    keep = retain_max if retain_max is not None else audit_retain_max_cycles()
    before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    count_before = int(before[0]) if before else 0
    if count_before <= keep:
        return 0

    conn.execute(
        f"""
        DELETE FROM {table}
        WHERE cycle_id NOT IN (
            SELECT cycle_id FROM {table}
            ORDER BY started_at DESC
            LIMIT ?
        )
        """,
        [keep],
    )
    after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    count_after = int(after[0]) if after else count_before
    deleted = max(0, count_before - count_after)
    if deleted > 0:
        conn.execute("CHECKPOINT")
    return deleted


def checkpoint_audit_db(db_path: str | Path) -> None:
    """Fuerza checkpoint en archivo DuckDB (reclama espacio tras DELETE)."""
    path = str(db_path)
    if path == ":memory:" or not Path(path).exists():
        return
    conn = connect_audit_duckdb(path, read_only=False)
    try:
        conn.execute("CHECKPOINT")
    finally:
        conn.close()


__all__ = [
    "audit_compact_payload_enabled",
    "audit_retain_max_cycles",
    "checkpoint_audit_db",
    "compact_bot_audit_payload",
    "connect_audit_duckdb",
    "payload_json_for_audit",
    "prune_audit_cycles",
]
