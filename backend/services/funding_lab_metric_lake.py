from __future__ import annotations
from typing import Any
"""Auditable metric lake for Funding Lab quantitative evidence."""


import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

METRIC_HORIZONS = ("1h", "4h", "eod")
DEFAULT_METRIC_MODULES = (
    "predictive",
    "technical",
    "options_gex",
    "crypto_microstructure",
    "risk",
)
_OUTCOME_BY_HORIZON = {
    "1h": "outcome_return_1h",
    "4h": "outcome_return_4h",
    "eod": "outcome_return_eod",
}
_TECHNICAL_PREFIXES = (
    "technical__",
    "price__",
    "vsa_forecast__",
    "squeeze__",
)
_OPTIONS_PREFIXES = (
    "options_gex__",
    "options__",
    "gamma_flip__",
    "tail_risk__",
    "dealer_flow__",
    "shadow_delta__",
)
_CRYPTO_PREFIXES = ("crypto__",)


def init_funding_lab_metric_tables(db_path: Path | str) -> None:
    """Create Funding Lab metric lake tables."""
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS funding_lab_metric_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                canonical_symbol TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                prediction_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                horizon TEXT NOT NULL,
                module TEXT NOT NULL,
                metric_family TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                source_tier TEXT NOT NULL,
                quality_score REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_funding_lab_metric_symbol
                ON funding_lab_metric_snapshots(canonical_symbol, module, horizon, timestamp);
            CREATE TABLE IF NOT EXISTS funding_lab_metric_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                symbols_json TEXT NOT NULL,
                inserted_rows INTEGER NOT NULL,
                source TEXT NOT NULL,
                stats_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def build_funding_lab_metric_snapshots(
    db_path: Path | str,
    *,
    symbols: list[str] | tuple[str, ...] | None = None,
    alias_groups: dict[str, list[str]] | None = None,
    run_id: str | None = None,
    limit_per_symbol: int = 50_000,
) -> dict[str, Any]:
    """Materialize module/horizon metric snapshots from predictions and features."""
    db = Path(db_path)
    if not db.exists():
        return {
            "ok": False,
            "run_id": run_id,
            "inserted": 0,
            "reason": "predictions_db_missing",
        }
    init_funding_lab_metric_tables(db)
    canonical_symbols = _canonical_symbols(symbols, alias_groups)
    alias_to_canonical = _alias_to_canonical(canonical_symbols, alias_groups)
    selected_aliases = sorted(alias_to_canonical)
    if not selected_aliases:
        return {"ok": True, "run_id": run_id, "inserted": 0, "reason": "no_symbols"}

    run = run_id or f"funding-lab-metrics-{datetime.now(tz=UTC).isoformat()}"
    rows = _load_metric_source_rows(
        db,
        aliases=selected_aliases,
        limit_per_symbol=limit_per_symbol,
    )
    now = datetime.now(tz=UTC).isoformat()
    snapshots: list[tuple[Any, ...]] = []
    stats: dict[str, dict[str, int]] = {}
    for row in rows:
        raw_symbol = str(row["symbol"]).upper().strip()
        canonical = alias_to_canonical.get(raw_symbol, raw_symbol)
        provider_symbol = raw_symbol
        features = _json_dict(row["features_json"])
        quality = _json_dict(row["source_quality"])
        for horizon, outcome_column in _OUTCOME_BY_HORIZON.items():
            outcome = _float_or_none(row[outcome_column])
            if outcome is None:
                continue
            for module, metrics, source_tier, quality_score in _module_metric_payloads(
                row=row,
                features=features,
                source_quality=quality,
                horizon=horizon,
                outcome=outcome,
            ):
                if canonical == "BTC/USDT" and module == "options_gex":
                    continue
                snapshot_id = f"{run}:{canonical}:{row['prediction_id']}:{horizon}:{module}"
                snapshots.append(
                    (
                        snapshot_id,
                        run,
                        raw_symbol,
                        canonical,
                        provider_symbol,
                        str(row["prediction_id"]),
                        str(row["timestamp"]),
                        horizon,
                        module,
                        module,
                        json.dumps(metrics, sort_keys=True),
                        source_tier,
                        quality_score,
                        now,
                    )
                )
                module_stats = stats.setdefault(canonical, {})
                key = f"{module}:{horizon}"
                module_stats[key] = module_stats.get(key, 0) + 1

    with sqlite3.connect(db) as con:
        before = con.total_changes
        con.executemany(
            """
            INSERT OR IGNORE INTO funding_lab_metric_snapshots (
                snapshot_id, run_id, symbol, canonical_symbol, provider_symbol,
                prediction_id, timestamp, horizon, module, metric_family,
                metrics_json, source_tier, quality_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            snapshots,
        )
        inserted = int(con.total_changes - before)
        con.execute(
            """
            INSERT OR REPLACE INTO funding_lab_metric_runs (
                run_id, status, symbols_json, inserted_rows, source, stats_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run,
                "available",
                json.dumps(canonical_symbols, sort_keys=True),
                inserted,
                str(db),
                json.dumps(stats, sort_keys=True),
                now,
            ),
        )
    logger.info(
        "funding_lab.metric_snapshots run_id=%s symbols=%s inserted=%d",
        run,
        ",".join(canonical_symbols),
        inserted,
    )
    return {
        "ok": True,
        "run_id": run,
        "inserted": inserted,
        "source": str(db),
        "symbols": canonical_symbols,
    }


def load_funding_lab_metric_status(
    db_path: Path | str,
    *,
    symbols: list[str] | tuple[str, ...],
    modules_by_symbol: dict[str, tuple[str, ...] | list[str]] | None = None,
) -> dict[str, Any]:
    """Load metric coverage and quality summaries for Funding Lab status."""
    canonical_symbols = [str(symbol).upper().strip() for symbol in symbols]
    modules_map = {
        symbol: (
            tuple(modules_by_symbol.get(symbol, DEFAULT_METRIC_MODULES))
            if modules_by_symbol
            else DEFAULT_METRIC_MODULES
        )
        for symbol in canonical_symbols
    }
    empty = _empty_metric_status(canonical_symbols, modules_map, db_path)
    db = Path(db_path)
    if not db.exists():
        return empty
    with sqlite3.connect(db) as con:
        tables = _sqlite_tables(con)
        if "funding_lab_metric_snapshots" not in tables:
            return empty
        latest_run = _latest_metric_run(con, str(db))
        latest_run_id = latest_run.get("run_id")
        placeholders = ",".join("?" for _ in canonical_symbols)
        run_clause = "AND run_id = ?" if latest_run_id else ""
        params: list[Any] = [*canonical_symbols]
        if latest_run_id:
            params.append(str(latest_run_id))
        rows = con.execute(
            f"""
            SELECT canonical_symbol, module, horizon, COUNT(*), AVG(quality_score)
            FROM funding_lab_metric_snapshots
            WHERE UPPER(canonical_symbol) IN ({placeholders})
              {run_clause}
            GROUP BY canonical_symbol, module, horizon
            """,
            params,
        ).fetchall()

    coverage = empty["metric_coverage"]
    quality_values: dict[str, list[float]] = {symbol: [] for symbol in canonical_symbols}
    observed_modules: dict[str, set[str]] = {symbol: set() for symbol in canonical_symbols}
    for symbol, module, horizon, count, quality in rows:
        sym = str(symbol).upper().strip()
        mod = str(module)
        hor = str(horizon)
        if sym not in coverage:
            continue
        observed_modules.setdefault(sym, set()).add(mod)
        coverage.setdefault(sym, {}).setdefault(mod, {h: 0 for h in METRIC_HORIZONS})[hor] = int(
            count
        )
        numeric_quality = _float_or_none(quality)
        if numeric_quality is not None:
            quality_values.setdefault(sym, []).append(numeric_quality)

    if modules_by_symbol is None:
        modules_map = {
            symbol: tuple(sorted(observed_modules.get(symbol) or DEFAULT_METRIC_MODULES))
            for symbol in canonical_symbols
        }
    missing = {
        symbol: _missing_metric_families(coverage[symbol], modules_map[symbol])
        for symbol in canonical_symbols
    }
    quality_by_symbol = {
        symbol: round(sum(values) / len(values), 4) if values else None
        for symbol, values in quality_values.items()
    }
    return {
        "metric_coverage": coverage,
        "latest_metric_run": latest_run,
        "missing_metric_families": missing,
        "quality_by_symbol": quality_by_symbol,
    }


def _empty_metric_status(
    symbols: list[str],
    modules_map: dict[str, tuple[str, ...]],
    db_path: Path | str,
) -> dict[str, Any]:
    coverage = {
        symbol: {
            module: {horizon: 0 for horizon in METRIC_HORIZONS} for module in modules_map[symbol]
        }
        for symbol in symbols
    }
    return {
        "metric_coverage": coverage,
        "latest_metric_run": {
            "status": "missing",
            "run_id": None,
            "updated_at": None,
            "source": str(db_path),
            "inserted_rows": 0,
        },
        "missing_metric_families": {symbol: sorted(modules_map[symbol]) for symbol in symbols},
        "quality_by_symbol": {symbol: None for symbol in symbols},
    }


def _latest_metric_run(con: sqlite3.Connection, source: str) -> dict[str, Any]:
    tables = _sqlite_tables(con)
    if "funding_lab_metric_runs" in tables:
        row = con.execute(
            """
            SELECT run_id, status, inserted_rows, created_at
            FROM funding_lab_metric_runs
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            return {
                "status": str(row[1]),
                "run_id": str(row[0]),
                "updated_at": str(row[3]),
                "source": source,
                "inserted_rows": int(row[2]),
            }
    row = con.execute(
        "SELECT MAX(created_at), COUNT(*) FROM funding_lab_metric_snapshots"
    ).fetchone()
    if row and row[0]:
        return {
            "status": "available",
            "run_id": None,
            "updated_at": str(row[0]),
            "source": source,
            "inserted_rows": int(row[1] or 0),
        }
    return {
        "status": "missing",
        "run_id": None,
        "updated_at": None,
        "source": source,
        "inserted_rows": 0,
    }


def _load_metric_source_rows(
    db_path: Path,
    *,
    aliases: list[str],
    limit_per_symbol: int,
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in aliases)
    limit = max(1, min(int(limit_per_symbol), 250_000))
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        tables = _sqlite_tables(con)
        if not {"predictions", "feature_snapshots"}.issubset(tables):
            return []
        columns = {str(row[1]) for row in con.execute("PRAGMA table_info(predictions)").fetchall()}
        if not set(_OUTCOME_BY_HORIZON.values()).intersection(columns):
            return []
        return list(
            con.execute(
                f"""
                SELECT p.prediction_id, p.symbol, p.timestamp, p.direction,
                       p.signal, p.confidence, p.should_trade, p.conflict_score,
                       p.price_t0, p.outcome_return_1h, p.outcome_return_4h,
                       p.outcome_return_eod, p.outcome_return_same_day,
                       p.sharpe_intraday_1h, p.sharpe_intraday_4h,
                       p.sharpe_intraday_eod, p.profit_factor_eod,
                       p.max_drawdown_eod, fs.features_json, fs.source_quality
                FROM predictions p
                LEFT JOIN feature_snapshots fs ON fs.prediction_id = p.prediction_id
                WHERE UPPER(p.symbol) IN ({placeholders})
                ORDER BY p.timestamp ASC
                LIMIT ?
                """,
                [*aliases, limit],
            )
        )


def _module_metric_payloads(
    *,
    row: sqlite3.Row,
    features: dict[str, Any],
    source_quality: dict[str, Any],
    horizon: str,
    outcome: float,
) -> list[tuple[str, dict[str, Any], str, float]]:
    payloads: list[tuple[str, dict[str, Any], str, float]] = []
    confidence = _float_or_none(row["confidence"])
    predictive_metrics = {
        "signal": _float_or_none(row["signal"]),
        "confidence": confidence,
        "direction": row["direction"],
        "should_trade": bool(row["should_trade"]) if row["should_trade"] is not None else None,
        "conflict_score": _float_or_none(row["conflict_score"]),
        "outcome_return": outcome,
        "confidence_margin": _confidence_margin(confidence),
        "entropy_proxy": _entropy_proxy(confidence),
    }
    payloads.append(
        (
            "predictive",
            predictive_metrics,
            "prediction_backfill",
            _bounded_quality(confidence),
        )
    )

    technical = _prefixed_metrics(features, _TECHNICAL_PREFIXES)
    if technical:
        payloads.append(
            (
                "technical",
                technical,
                "technical_features",
                _coverage_quality(technical, expected=12),
            )
        )

    options = _prefixed_metrics(features, _OPTIONS_PREFIXES)
    if options:
        payloads.append(
            (
                "options_gex",
                options,
                _options_source_tier(features, source_quality),
                _bounded_quality(
                    _float_or_none(features.get("options_gex__data_quality_score"))
                    or _float_or_none(source_quality.get("options_gex_data_quality_score"))
                ),
            )
        )

    crypto = _prefixed_metrics(features, _CRYPTO_PREFIXES)
    if crypto:
        payloads.append(
            (
                "crypto_microstructure",
                crypto,
                _crypto_source_tier(features, source_quality),
                _bounded_quality(
                    _float_or_none(features.get("crypto__data_quality_score"))
                    or _float_or_none(source_quality.get("crypto_derivatives_data_quality_score"))
                ),
            )
        )

    risk_metrics = {
        "outcome_return": outcome,
        "outcome_return_same_day": _float_or_none(row["outcome_return_same_day"]),
        "sharpe_intraday_1h": _float_or_none(row["sharpe_intraday_1h"]),
        "sharpe_intraday_4h": _float_or_none(row["sharpe_intraday_4h"]),
        "sharpe_intraday_eod": _float_or_none(row["sharpe_intraday_eod"]),
        "profit_factor_eod": _float_or_none(row["profit_factor_eod"]),
        "max_drawdown_eod": _float_or_none(row["max_drawdown_eod"]),
    }
    payloads.append(("risk", risk_metrics, "intraday_outcome", 1.0))
    return payloads


def _prefixed_metrics(features: dict[str, Any], prefixes: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in features.items():
        if key.startswith(prefixes):
            out[key] = value
    return out


def _options_source_tier(features: dict[str, Any], source_quality: dict[str, Any]) -> str:
    raw = str(source_quality.get("options_gex_source_tier") or "").strip()
    if raw in {"light_proxy", "snapshot_chain", "full_chain_gex"}:
        return raw
    score = _float_or_none(features.get("options_gex__source_tier_score"))
    if score is not None and score >= 0.95:
        return "full_chain_gex"
    if score is not None and score >= 0.55:
        return "snapshot_chain"
    return "light_proxy"


def _crypto_source_tier(features: dict[str, Any], source_quality: dict[str, Any]) -> str:
    raw = str(source_quality.get("crypto_derivatives_source_tier") or "").strip()
    if raw:
        return raw
    available = _float_or_none(features.get("crypto__derivatives_available")) or 0.0
    return "binance_public_derivatives" if available > 0 else "unvalidated_crypto_derivatives"


def _missing_metric_families(
    coverage: dict[str, dict[str, int]],
    required_modules: tuple[str, ...] | list[str],
) -> list[str]:
    missing: list[str] = []
    for module in required_modules:
        module_coverage = coverage.get(module, {})
        if any(int(module_coverage.get(horizon, 0)) <= 0 for horizon in METRIC_HORIZONS):
            missing.append(str(module))
    return sorted(missing)


def _canonical_symbols(
    symbols: list[str] | tuple[str, ...] | None,
    alias_groups: dict[str, list[str]] | None,
) -> list[str]:
    if alias_groups:
        return sorted(str(symbol).upper().strip() for symbol in alias_groups)
    return sorted(str(symbol).upper().strip() for symbol in (symbols or []))


def _alias_to_canonical(
    canonical_symbols: list[str],
    alias_groups: dict[str, list[str]] | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    if alias_groups:
        for canonical, aliases in alias_groups.items():
            canonical_key = str(canonical).upper().strip()
            for alias in aliases:
                text = str(alias).upper().strip()
                if text:
                    out[text] = canonical_key
            out[canonical_key] = canonical_key
        return out
    for symbol in canonical_symbols:
        out[symbol] = symbol
    return out


def _sqlite_tables(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _json_dict(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded_quality(value: float | None) -> float:
    if value is None:
        return 0.0
    return round(max(0.0, min(1.0, float(value))), 4)


def _coverage_quality(metrics: dict[str, Any], *, expected: int) -> float:
    return round(max(0.0, min(1.0, len(metrics) / max(1, expected))), 4)


def _confidence_margin(confidence: float | None) -> float | None:
    if confidence is None:
        return None
    return round(abs(confidence - 0.5) * 2.0, 4)


def _entropy_proxy(confidence: float | None) -> float | None:
    if confidence is None:
        return None
    p = max(1e-9, min(1.0 - 1e-9, confidence))
    entropy = -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))
    return round(entropy, 4)
