from __future__ import annotations
from typing import Any
"""Read-only FTMO GEX validation for Funding Lab.

The validator only reads persisted Options/GEX evidence. It never calls option
providers, market-data APIs, broker APIs, or the BingX Bot.
"""


import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DIRECT_GEX_REQUIRED_SYMBOLS = frozenset({"AAPL", "GOOGL", "TSLA"})
PROXY_GEX_CONTEXT_BY_SYMBOL = {
    "XAUUSD": "GLD",
    "XAGUSD": "SLV",
    "US100.CASH": "QQQ",
}
DEFAULT_GEX_FRESHNESS_HOURS = 24
DEFAULT_MIN_GEX_QUALITY = 0.65


def load_ftmo_gex_validation(
    db_path: Path | str,
    canonical_symbol: str,
    *,
    now: datetime | None = None,
    freshness_hours: int = DEFAULT_GEX_FRESHNESS_HOURS,
    min_quality: float = DEFAULT_MIN_GEX_QUALITY,
) -> dict[str, Any]:
    """Return JSON-safe GEX validation state for a Funding Lab symbol."""
    symbol = _normalize_symbol(canonical_symbol)
    direct_required = symbol in DIRECT_GEX_REQUIRED_SYMBOLS
    proxy_symbol = PROXY_GEX_CONTEXT_BY_SYMBOL.get(symbol)
    context_required = proxy_symbol is not None
    gex_symbol = proxy_symbol or symbol
    current_time = _as_utc(now or datetime.now(tz=UTC))
    snapshot = _latest_snapshot(Path(db_path), gex_symbol)

    blockers: list[str] = []
    source_tier = snapshot.get("source_tier") if snapshot else None
    provider = snapshot.get("provider") if snapshot else None
    quality = _float_or_none(snapshot.get("data_quality_score")) if snapshot else None
    last_snapshot_at = snapshot.get("as_of") if snapshot else None

    if not direct_required and not context_required:
        return _payload(
            symbol=symbol,
            gex_symbol=None,
            direct_required=False,
            context_required=False,
            gex_validated=False,
            gex_context_ready=False,
            source_tier=None,
            provider=None,
            quality=None,
            last_snapshot_at=None,
            blockers=[],
        )

    if snapshot is None:
        blockers.append(
            "gex_full_chain_missing" if direct_required else "gex_proxy_context_missing"
        )
    else:
        if source_tier != "full_chain_gex":
            blockers.append(
                "gex_full_chain_missing" if direct_required else "gex_proxy_context_missing"
            )
            blockers.append("gex_source_not_validated")
        if _is_stale(last_snapshot_at, now=current_time, freshness_hours=freshness_hours):
            blockers.append("gex_snapshot_stale" if direct_required else "gex_proxy_context_stale")
        if quality is None or quality < float(min_quality):
            blockers.append(
                "gex_quality_low" if direct_required else "gex_proxy_context_low_quality"
            )

    blockers = _dedupe(blockers)
    gex_validated = direct_required and not blockers
    context_ready = context_required and not blockers
    return _payload(
        symbol=symbol,
        gex_symbol=gex_symbol,
        direct_required=direct_required,
        context_required=context_required,
        gex_validated=gex_validated,
        gex_context_ready=context_ready,
        source_tier=source_tier,
        provider=provider,
        quality=quality,
        last_snapshot_at=last_snapshot_at,
        blockers=blockers,
    )


def _payload(
    *,
    symbol: str,
    gex_symbol: str | None,
    direct_required: bool,
    context_required: bool,
    gex_validated: bool,
    gex_context_ready: bool,
    source_tier: str | None,
    provider: str | None,
    quality: float | None,
    last_snapshot_at: str | None,
    blockers: list[str],
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "gex_symbol": gex_symbol,
        "gex_required": direct_required,
        "gex_context_required": context_required,
        "gex_validated": gex_validated,
        "gex_context_ready": gex_context_ready,
        "gex_source_tier": source_tier,
        "gex_provider": provider,
        "gex_data_quality_score": quality,
        "gex_last_snapshot_at": last_snapshot_at,
        "gex_blockers": blockers,
    }


def _latest_snapshot(db_path: Path, symbol: str) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "options_gex_snapshots" in tables:
            row = con.execute(
                """
                SELECT symbol, as_of, source_tier, data_quality_score, provider, features_json
                FROM options_gex_snapshots
                WHERE UPPER(symbol) = ?
                ORDER BY CASE WHEN source_tier = 'full_chain_gex' THEN 0 ELSE 1 END,
                         as_of DESC, created_at DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()
            if row is not None:
                return _snapshot_from_options_row(row)
        if "feature_snapshots" in tables:
            columns = _table_columns(con, "feature_snapshots")
            if "symbol" not in columns:
                return None
            provider_select = "provider" if "provider" in columns else "NULL AS provider"
            source_tier_select = (
                "source_tier" if "source_tier" in columns else "NULL AS source_tier"
            )
            row = con.execute(
                f"""
                SELECT symbol, timestamp, features_json, source_quality,
                       {provider_select}, {source_tier_select}
                FROM feature_snapshots
                WHERE UPPER(symbol) = ?
                ORDER BY timestamp DESC, created_at DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()
            if row is not None:
                return _snapshot_from_feature_row(row)
    return None


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _snapshot_from_options_row(row: sqlite3.Row) -> dict[str, Any]:
    features = _json_dict(row["features_json"])
    return {
        "symbol": row["symbol"],
        "as_of": row["as_of"],
        "source_tier": str(row["source_tier"] or features.get("source_tier") or ""),
        "data_quality_score": _first_float(
            row["data_quality_score"], features.get("data_quality_score")
        ),
        "provider": str(row["provider"] or features.get("provider") or "unknown"),
    }


def _snapshot_from_feature_row(row: sqlite3.Row) -> dict[str, Any]:
    features = _json_dict(row["features_json"])
    quality = _json_dict(row["source_quality"])
    source_tier = (
        quality.get("options_gex_source_tier")
        or row["source_tier"]
        or _source_tier_from_score(features.get("options_gex__source_tier_score"))
    )
    data_quality = _first_float(
        quality.get("options_gex_data_quality_score"),
        features.get("options_gex__data_quality_score"),
    )
    return {
        "symbol": row["symbol"],
        "as_of": row["timestamp"],
        "source_tier": str(source_tier or ""),
        "data_quality_score": data_quality,
        "provider": str(row["provider"] or quality.get("provider") or "feature_snapshots"),
    }


def _source_tier_from_score(value: object) -> str:
    score = _float_or_none(value)
    if score is None:
        return ""
    if score >= 0.95:
        return "full_chain_gex"
    if score >= 0.55:
        return "snapshot_chain"
    return "light_proxy"


def _is_stale(value: object, *, now: datetime, freshness_hours: int) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return True
    return (now - parsed).total_seconds() > max(1, int(freshness_hours)) * 3600


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_float(*values: object) -> float | None:
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _normalize_symbol(value: object) -> str:
    return str(value or "").upper().strip()


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
