from __future__ import annotations
from typing import Any
"""Provider registry and audit storage for FTMO Funding Lab CFD fidelity."""


import math
import os
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

CFD_PRIMARY = "cfd_primary"
BROKER_VALIDATED = "broker_validated"
MARKET_DATA_PRIMARY = "market_data_primary"
BINANCE_SPOT_PRIMARY = "binance_spot_primary"
BINANCE_DERIVATIVES_PRIMARY = "binance_derivatives_primary"
BINGX_MARKET_PRIMARY = "bingx_market_primary"
PROXY_ONLY = "proxy_only"
UNAVAILABLE = "unavailable"
LOW_FIDELITY = "low_fidelity"
FIDELITY_MIN_SCORE = 0.85
BINGX_PRIMARY_SYMBOLS = frozenset({"BTC/USDT", "US100.CASH", "XAUUSD", "XAGUSD"})
PRIMARY_USAGE_ROLE = "primary"
VALIDATION_USAGE_ROLE = "validation"
CONTEXT_USAGE_ROLE = "context"
COMPARISON_USAGE_ROLE = "comparison"


@dataclass(frozen=True)
class FtmoProviderSpec:
    name: str
    role: str
    source_tier: str
    env_keys: tuple[str, ...] = ()
    supports_bid_ask: bool = False
    supports_realtime: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["env_keys"] = list(self.env_keys)
        return payload


@dataclass(frozen=True)
class FtmoProviderBar:
    provider: str
    canonical_symbol: str
    provider_symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None
    source_tier: str = CFD_PRIMARY
    usage_role: str = PRIMARY_USAGE_ROLE

    @property
    def snapshot_id(self) -> str:
        stamp = self.timestamp.astimezone(UTC).isoformat()
        return f"{self.provider}:{self.canonical_symbol}:{self.provider_symbol}:{self.timeframe}:{stamp}"


def provider_registry() -> dict[str, FtmoProviderSpec]:
    return {
        "fmp_massive_polygon": FtmoProviderSpec(
            name="fmp_massive_polygon",
            role="market_data_primary",
            source_tier=MARKET_DATA_PRIMARY,
            supports_bid_ask=False,
            supports_realtime=False,
            notes="Primary non-CFD market data feed for equities, metals proxies and index proxies.",
        ),
        "bingx_market": FtmoProviderSpec(
            name="bingx_market",
            role="bingx_market_data",
            source_tier=BINGX_MARKET_PRIMARY,
            supports_bid_ask=True,
            supports_realtime=True,
            notes="BingX public market data used by FTMO Challenge only as data provider.",
        ),
        "binance_spot": FtmoProviderSpec(
            name="binance_spot",
            role="crypto_spot_primary",
            source_tier=BINANCE_SPOT_PRIMARY,
            supports_bid_ask=True,
            supports_realtime=True,
            notes="Primary BTC/USDT spot feed from Binance.",
        ),
        "binance_usdm": FtmoProviderSpec(
            name="binance_usdm",
            role="crypto_derivatives_primary",
            source_tier=BINANCE_DERIVATIVES_PRIMARY,
            supports_bid_ask=False,
            supports_realtime=True,
            notes="BTCUSDT USD-M derivatives microstructure from Binance.",
        ),
        "tradermade_cfd": FtmoProviderSpec(
            name="tradermade_cfd",
            role="primary_cfd",
            source_tier=CFD_PRIMARY,
            env_keys=("TRADERMADE_API_KEY",),
            supports_bid_ask=True,
            supports_realtime=True,
            notes="Primary CFD candidate for equities, metals and index CFDs.",
        ),
        "metaapi_mt5": FtmoProviderSpec(
            name="metaapi_mt5",
            role="broker_validation",
            source_tier=BROKER_VALIDATED,
            env_keys=("METAAPI_TOKEN", "METAAPI_ACCOUNT_ID"),
            supports_bid_ask=False,
            supports_realtime=True,
            notes="Broker-like MT5 validation feed when an account is configured.",
        ),
        "infoway_candles": FtmoProviderSpec(
            name="infoway_candles",
            role="alternate_candles",
            source_tier=CFD_PRIMARY,
            env_keys=("INFOWAY_API_KEY",),
            supports_bid_ask=False,
            supports_realtime=False,
            notes="Commercial alternate candles if primary CFD coverage is incomplete.",
        ),
        "existing_proxy": FtmoProviderSpec(
            name="existing_proxy",
            role="fallback_proxy",
            source_tier=PROXY_ONLY,
            env_keys=(),
            supports_bid_ask=False,
            supports_realtime=False,
            notes="Existing OHLCV proxy aliases; never sufficient to authorize FTMO risk.",
        ),
    }


def _ensure_ftmo_fidelity_columns(con: sqlite3.Connection) -> None:
    columns = {
        str(row[1]) for row in con.execute("PRAGMA table_info(ftmo_fidelity_audit)").fetchall()
    }
    additions = {
        "median_abs_divergence_pct": "REAL",
        "p95_abs_divergence_pct": "REAL",
        "outlier_count_2pct": "INTEGER",
        "outlier_count_5pct": "INTEGER",
    }
    for column, column_type in additions.items():
        if column not in columns:
            con.execute(f"ALTER TABLE ftmo_fidelity_audit ADD COLUMN {column} {column_type}")


def _ensure_ftmo_provider_snapshot_columns(con: sqlite3.Connection) -> None:
    columns = {
        str(row[1]) for row in con.execute("PRAGMA table_info(ftmo_provider_snapshots)").fetchall()
    }
    if "usage_role" not in columns:
        con.execute(
            "ALTER TABLE ftmo_provider_snapshots "
            f"ADD COLUMN usage_role TEXT NOT NULL DEFAULT '{PRIMARY_USAGE_ROLE}'"
        )


def init_ftmo_provider_tables(db_path: Path | str) -> None:
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS ftmo_provider_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                canonical_symbol TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL,
                bid REAL,
                ask REAL,
                spread REAL,
                source_tier TEXT NOT NULL,
                usage_role TEXT NOT NULL DEFAULT 'primary',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ftmo_provider_snapshots_symbol
                ON ftmo_provider_snapshots(canonical_symbol, provider, timeframe, timestamp);
            CREATE TABLE IF NOT EXISTS ftmo_provider_health (
                provider TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                ok INTEGER NOT NULL,
                latency_ms REAL,
                error TEXT,
                rate_limit_remaining INTEGER,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ftmo_fidelity_audit (
                audit_id TEXT PRIMARY KEY,
                canonical_symbol TEXT NOT NULL,
                primary_provider TEXT NOT NULL,
                comparison_provider TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                rows_compared INTEGER NOT NULL,
                fidelity_score REAL,
                tracking_error_pct REAL,
                avg_spread_pct REAL,
                correlation REAL,
                max_abs_divergence_pct REAL,
                median_abs_divergence_pct REAL,
                p95_abs_divergence_pct REAL,
                outlier_count_2pct INTEGER,
                outlier_count_5pct INTEGER,
                source_tier TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ftmo_fidelity_symbol
                ON ftmo_fidelity_audit(canonical_symbol, created_at);
            """
        )
        _ensure_ftmo_provider_snapshot_columns(con)
        _ensure_ftmo_fidelity_columns(con)


def insert_ftmo_provider_snapshots(db_path: Path | str, bars: list[FtmoProviderBar]) -> int:
    if not bars:
        return 0
    init_ftmo_provider_tables(db_path)
    now = datetime.now(tz=UTC).isoformat()
    rows = [
        (
            bar.snapshot_id,
            bar.provider,
            bar.canonical_symbol,
            bar.provider_symbol,
            bar.timeframe,
            bar.timestamp.astimezone(UTC).isoformat(),
            float(bar.open),
            float(bar.high),
            float(bar.low),
            float(bar.close),
            bar.volume,
            bar.bid,
            bar.ask,
            bar.spread,
            bar.source_tier,
            bar.usage_role,
            now,
        )
        for bar in bars
    ]
    with sqlite3.connect(db_path) as con:
        before = con.total_changes
        con.executemany(
            """
            INSERT OR IGNORE INTO ftmo_provider_snapshots (
                snapshot_id, provider, canonical_symbol, provider_symbol, timeframe,
                timestamp, open, high, low, close, volume, bid, ask, spread,
                source_tier, usage_role, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return int(con.total_changes - before)


def insert_ftmo_provider_health(
    db_path: Path | str,
    *,
    provider: str,
    status: str,
    ok: bool,
    latency_ms: float | None,
    error: str | None,
    rate_limit_remaining: int | None,
) -> None:
    init_ftmo_provider_tables(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            INSERT INTO ftmo_provider_health (
                provider, status, ok, latency_ms, error, rate_limit_remaining, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                status = excluded.status,
                ok = excluded.ok,
                latency_ms = excluded.latency_ms,
                error = excluded.error,
                rate_limit_remaining = excluded.rate_limit_remaining,
                updated_at = excluded.updated_at
            """,
            (
                provider,
                status,
                int(ok),
                latency_ms,
                error,
                rate_limit_remaining,
                datetime.now(tz=UTC).isoformat(),
            ),
        )


def insert_ftmo_fidelity_audit(
    db_path: Path | str,
    *,
    canonical_symbol: str,
    primary_provider: str,
    comparison_provider: str,
    timeframe: str,
    audit: dict[str, Any],
) -> None:
    init_ftmo_provider_tables(db_path)
    created_at = datetime.now(tz=UTC).isoformat()
    audit_id = (
        f"{canonical_symbol}:{primary_provider}:{comparison_provider}:{timeframe}:{created_at}"
    )
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            INSERT OR REPLACE INTO ftmo_fidelity_audit (
                audit_id, canonical_symbol, primary_provider, comparison_provider,
                timeframe, rows_compared, fidelity_score, tracking_error_pct,
                avg_spread_pct, correlation, max_abs_divergence_pct,
                median_abs_divergence_pct, p95_abs_divergence_pct,
                outlier_count_2pct, outlier_count_5pct, source_tier, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                canonical_symbol,
                primary_provider,
                comparison_provider,
                timeframe,
                int(audit.get("rows_compared") or 0),
                audit.get("fidelity_score"),
                audit.get("tracking_error_pct"),
                audit.get("avg_spread_pct"),
                audit.get("correlation"),
                audit.get("max_abs_divergence_pct"),
                audit.get("median_abs_divergence_pct"),
                audit.get("p95_abs_divergence_pct"),
                audit.get("outlier_count_2pct"),
                audit.get("outlier_count_5pct"),
                audit.get("source_tier") or UNAVAILABLE,
                created_at,
            ),
        )


def fetch_tradermade_cfd_bars(
    db_path: Path | str,
    *,
    canonical_symbol: str,
    provider_symbol: str,
    days: int,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> list[FtmoProviderBar]:
    api_key = _env_value(env, "TRADERMADE_API_KEY")
    if not api_key:
        _record_provider_failure(
            db_path,
            provider="tradermade_cfd",
            status="missing_credentials",
            error="TRADERMADE_API_KEY missing",
        )
        return []

    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=max(1, int(days)) + 7)
    params = {
        "currency": provider_symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "api_key": api_key,
    }
    return _fetch_provider_bars(
        db_path,
        provider="tradermade_cfd",
        request=lambda http: http.get(
            "https://marketdata.tradermade.com/api/v1/timeseries",
            params=params,
        ),
        normalize=lambda rows: normalize_tradermade_timeseries(
            provider="tradermade_cfd",
            canonical_symbol=canonical_symbol,
            provider_symbol=provider_symbol,
            rows=rows,
        ),
        client=client,
    )


def fetch_metaapi_mt5_bars(
    db_path: Path | str,
    *,
    canonical_symbol: str,
    provider_symbol: str,
    days: int,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> list[FtmoProviderBar]:
    token = _env_value(env, "METAAPI_TOKEN")
    account_id = _env_value(env, "METAAPI_ACCOUNT_ID")
    region = _env_value(env, "METAAPI_REGION") or "new-york"
    if not token or not account_id:
        _record_provider_failure(
            db_path,
            provider="metaapi_mt5",
            status="missing_credentials",
            error="METAAPI_TOKEN or METAAPI_ACCOUNT_ID missing",
        )
        return []

    base = f"https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai"
    url = (
        f"{base}/users/current/accounts/{account_id}/historical-market-data/"
        f"symbols/{provider_symbol}/timeframes/1d/candles"
    )
    params = {
        "startTime": datetime.now(tz=UTC).isoformat(),
        "limit": min(1000, max(1, int(days) + 7)),
    }
    headers = {"auth-token": token, "Accept": "application/json"}
    return _fetch_provider_bars(
        db_path,
        provider="metaapi_mt5",
        request=lambda http: http.get(url, params=params, headers=headers),
        normalize=lambda rows: normalize_metaapi_candles(
            provider="metaapi_mt5",
            canonical_symbol=canonical_symbol,
            provider_symbol=provider_symbol,
            rows=rows,
        ),
        client=client,
    )


def fetch_infoway_candles(
    db_path: Path | str,
    *,
    canonical_symbol: str,
    provider_symbol: str,
    days: int,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> list[FtmoProviderBar]:
    api_key = _env_value(env, "INFOWAY_API_KEY")
    if not api_key:
        _record_provider_failure(
            db_path,
            provider="infoway_candles",
            status="missing_credentials",
            error="INFOWAY_API_KEY missing",
        )
        return []

    params = {"apiKey": api_key, "symbol": provider_symbol, "period": "1d", "limit": int(days)}
    return _fetch_provider_bars(
        db_path,
        provider="infoway_candles",
        request=lambda http: http.get("https://data.infoway.io/stock/batch_kline", params=params),
        normalize=lambda rows: normalize_infoway_candles(
            provider="infoway_candles",
            canonical_symbol=canonical_symbol,
            provider_symbol=provider_symbol,
            rows=rows,
        ),
        client=client,
    )


def load_ftmo_provider_bars(
    db_path: Path | str, *, canonical_symbol: str, provider: str
) -> list[FtmoProviderBar]:
    db = Path(db_path)
    if not db.exists():
        return []
    with sqlite3.connect(db) as con:
        tables = _sqlite_tables(con)
        if "ftmo_provider_snapshots" not in tables:
            return []
        columns = {str(row[1]) for row in con.execute("PRAGMA table_info(ftmo_provider_snapshots)")}
        usage_expr = "usage_role" if "usage_role" in columns else f"'{PRIMARY_USAGE_ROLE}'"
        rows = con.execute(
            f"""
            SELECT provider, canonical_symbol, provider_symbol, timeframe, timestamp,
                   open, high, low, close, volume, bid, ask, spread, source_tier,
                   {usage_expr}
            FROM ftmo_provider_snapshots
            WHERE canonical_symbol = ? AND provider = ?
            ORDER BY timestamp ASC
            """,
            (canonical_symbol, provider),
        ).fetchall()
    return [
        FtmoProviderBar(
            provider=str(row[0]),
            canonical_symbol=str(row[1]),
            provider_symbol=str(row[2]),
            timeframe=str(row[3]),
            timestamp=_parse_timestamp(row[4]),
            open=float(row[5]),
            high=float(row[6]),
            low=float(row[7]),
            close=float(row[8]),
            volume=_float_or_none(row[9]),
            bid=_float_or_none(row[10]),
            ask=_float_or_none(row[11]),
            spread=_float_or_none(row[12]),
            source_tier=str(row[13]),
            usage_role=str(row[14] or PRIMARY_USAGE_ROLE),
        )
        for row in rows
    ]


def load_provider_readiness(db_path: Path | str, canonical_symbol: str) -> dict[str, Any]:
    registry = provider_registry()
    default_health = _default_health()
    db = Path(db_path)
    if not db.exists():
        return _empty_readiness(default_health)
    con = sqlite3.connect(db)
    try:
        tables = _sqlite_tables(con)
        health = _load_health(con, registry) if "ftmo_provider_health" in tables else default_health
        if "ftmo_provider_snapshots" not in tables:
            return _empty_readiness(health)
        count_rows = con.execute(
            """
            SELECT provider, COUNT(*)
            FROM ftmo_provider_snapshots
            WHERE canonical_symbol = ?
            GROUP BY provider
            """,
            (canonical_symbol,),
        ).fetchall()
        latest_rows = con.execute(
            """
            SELECT provider, provider_symbol, timestamp, source_tier, usage_role, created_at
            FROM ftmo_provider_snapshots
            WHERE canonical_symbol = ?
            ORDER BY timestamp DESC, created_at DESC
            """,
            (canonical_symbol,),
        ).fetchall()
        latest_snapshot_by_provider: dict[str, dict[str, Any]] = {}
        for row in latest_rows:
            provider = str(row[0])
            if provider in latest_snapshot_by_provider:
                continue
            latest_snapshot_by_provider[provider] = {
                "provider_symbol": row[1],
                "timestamp": row[2],
                "source_tier": str(row[3]),
                "usage_role": str(row[4] or PRIMARY_USAGE_ROLE),
                "created_at": row[5],
            }
        counts = {str(row[0]): int(row[1]) for row in count_rows}
        latest_by_provider = {
            provider: snapshot["timestamp"]
            for provider, snapshot in latest_snapshot_by_provider.items()
        }
        latest_provider_symbol_by_provider = {
            provider: snapshot["provider_symbol"]
            for provider, snapshot in latest_snapshot_by_provider.items()
        }
        tier_by_provider = {
            provider: snapshot["source_tier"]
            for provider, snapshot in latest_snapshot_by_provider.items()
        }
        latest_usage_role_by_provider = {
            provider: snapshot["usage_role"]
            for provider, snapshot in latest_snapshot_by_provider.items()
        }
        latest_created_by_provider = {
            provider: snapshot["created_at"]
            for provider, snapshot in latest_snapshot_by_provider.items()
        }
        primary = _select_primary_provider(canonical_symbol, counts)
        snapshot_rows = counts.get(primary, 0) if primary else 0
        fidelity = (
            _latest_fidelity(con, canonical_symbol) if "ftmo_fidelity_audit" in tables else {}
        )
        tier = tier_by_provider.get(primary or "", UNAVAILABLE)
        if not primary:
            tier = UNAVAILABLE
        elif fidelity.get("source_tier") == LOW_FIDELITY:
            tier = LOW_FIDELITY
        elif primary == "metaapi_mt5":
            tier = BROKER_VALIDATED
        elif primary == "binance_spot":
            tier = BINANCE_SPOT_PRIMARY
        elif primary == "binance_usdm":
            tier = BINANCE_DERIVATIVES_PRIMARY
        elif primary == "bingx_market":
            tier = BINGX_MARKET_PRIMARY
        elif primary == "fmp_massive_polygon":
            tier = MARKET_DATA_PRIMARY
        return {
            "primary_provider": primary or "tradermade_cfd",
            "broker_validation_status": _broker_status(counts, health),
            "source_tier": tier,
            "fidelity_score": fidelity.get("fidelity_score"),
            "proxy_fallback_reason": (
                _fallback_reason(counts, health) if tier in {PROXY_ONLY, UNAVAILABLE} else None
            ),
            "snapshot_rows": snapshot_rows,
            "latest_timestamp": latest_by_provider.get(primary or ""),
            "latest_by_provider": latest_by_provider,
            "latest_provider_symbol_by_provider": latest_provider_symbol_by_provider,
            "latest_usage_role_by_provider": latest_usage_role_by_provider,
            "latest_created_by_provider": latest_created_by_provider,
            "provider_counts": counts,
            "provider_health": health,
            "fidelity_audit": fidelity or None,
        }
    except sqlite3.Error as exc:
        logger.info("ftmo_provider_readiness_failed symbol=%s error=%s", canonical_symbol, exc)
        return _empty_readiness(default_health)
    finally:
        con.close()


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / 2.0


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] * (upper - position) + values[upper] * (position - lower)


def compute_fidelity_score(
    primary: list[FtmoProviderBar], comparison: list[FtmoProviderBar]
) -> dict[str, Any]:
    primary_by_day = {_date_key(bar.timestamp): bar for bar in primary}
    comparison_by_day = {_date_key(bar.timestamp): bar for bar in comparison}
    days = sorted(set(primary_by_day).intersection(comparison_by_day))
    if not days:
        return {
            "rows_compared": 0,
            "fidelity_score": None,
            "tracking_error_pct": None,
            "avg_spread_pct": None,
            "correlation": None,
            "max_abs_divergence_pct": None,
            "source_tier": UNAVAILABLE,
        }
    divergences = [
        (primary_by_day[day].close / comparison_by_day[day].close - 1.0) * 100.0
        for day in days
        if comparison_by_day[day].close
    ]
    abs_divergences = sorted(abs(value) for value in divergences)
    spreads = [
        ((bar.spread or 0.0) / bar.close) * 100.0
        for bar in (primary_by_day[day] for day in days)
        if bar.close and bar.spread is not None
    ]
    tracking_error = _std(divergences) if len(divergences) > 1 else abs(divergences[0])
    max_abs = max(abs(value) for value in divergences) if divergences else None
    median_abs = _median(abs_divergences)
    p95_abs = _percentile(abs_divergences, 0.95)
    outlier_count_2pct = sum(1 for value in abs_divergences if value > 2.0)
    outlier_count_5pct = sum(1 for value in abs_divergences if value > 5.0)
    avg_spread = sum(spreads) / len(spreads) if spreads else None
    corr = _correlation(
        [primary_by_day[day].close for day in days],
        [comparison_by_day[day].close for day in days],
    )
    robust_validated = (
        len(days) >= 45
        and corr is not None
        and corr >= 0.98
        and median_abs is not None
        and median_abs <= 0.50
        and p95_abs is not None
        and p95_abs <= 1.25
        and outlier_count_5pct <= 1
    )
    robust_penalty = (tracking_error or 0.0) / 12.0 + (p95_abs or 0.0) / 12.5
    if corr is not None:
        robust_penalty += max(0.0, 0.985 - corr)
    max_penalty = 0.0 if robust_validated else (max_abs or 0.0) / 25.0
    penalty = robust_penalty + max_penalty
    if corr is not None:
        penalty += max(0.0, 0.98 - corr)
    score = max(0.0, min(1.0, 1.0 - penalty))
    return {
        "rows_compared": len(days),
        "fidelity_score": round(score, 4),
        "tracking_error_pct": round(tracking_error, 4) if tracking_error is not None else None,
        "avg_spread_pct": round(avg_spread, 4) if avg_spread is not None else None,
        "correlation": round(corr, 4) if corr is not None else None,
        "max_abs_divergence_pct": round(max_abs, 4) if max_abs is not None else None,
        "median_abs_divergence_pct": round(median_abs, 4) if median_abs is not None else None,
        "p95_abs_divergence_pct": round(p95_abs, 4) if p95_abs is not None else None,
        "outlier_count_2pct": outlier_count_2pct,
        "outlier_count_5pct": outlier_count_5pct,
        "source_tier": (
            BROKER_VALIDATED if score >= FIDELITY_MIN_SCORE or robust_validated else LOW_FIDELITY
        ),
    }


def normalize_tradermade_timeseries(
    *,
    provider: str,
    canonical_symbol: str,
    provider_symbol: str,
    rows: list[dict[str, Any]],
) -> list[FtmoProviderBar]:
    return [
        FtmoProviderBar(
            provider=provider,
            canonical_symbol=canonical_symbol,
            provider_symbol=provider_symbol,
            timeframe="1d",
            timestamp=_parse_timestamp(row.get("date") or row.get("timestamp") or row.get("time")),
            open=_float_required(row.get("open")),
            high=_float_required(row.get("high")),
            low=_float_required(row.get("low")),
            close=_float_required(row.get("close")),
            volume=_float_or_none(row.get("volume")),
            bid=_float_or_none(row.get("bid")),
            ask=_float_or_none(row.get("ask")),
            spread=_spread(row),
            source_tier=CFD_PRIMARY,
        )
        for row in rows
    ]


def normalize_metaapi_candles(
    *,
    provider: str,
    canonical_symbol: str,
    provider_symbol: str,
    rows: list[dict[str, Any]],
) -> list[FtmoProviderBar]:
    return [
        FtmoProviderBar(
            provider=provider,
            canonical_symbol=canonical_symbol,
            provider_symbol=provider_symbol,
            timeframe="1d",
            timestamp=_parse_timestamp(row.get("time") or row.get("brokerTime")),
            open=_float_required(row.get("open")),
            high=_float_required(row.get("high")),
            low=_float_required(row.get("low")),
            close=_float_required(row.get("close")),
            volume=_float_or_none(row.get("tickVolume") or row.get("volume")),
            spread=_float_or_none(row.get("spread")),
            source_tier=BROKER_VALIDATED,
        )
        for row in rows
    ]


def normalize_infoway_candles(
    *,
    provider: str,
    canonical_symbol: str,
    provider_symbol: str,
    rows: list[dict[str, Any]],
) -> list[FtmoProviderBar]:
    return [
        FtmoProviderBar(
            provider=provider,
            canonical_symbol=canonical_symbol,
            provider_symbol=provider_symbol,
            timeframe="1d",
            timestamp=_parse_timestamp(row.get("t") or row.get("time") or row.get("timestamp")),
            open=_float_required(row.get("o") or row.get("open")),
            high=_float_required(row.get("h") or row.get("high")),
            low=_float_required(row.get("l") or row.get("low")),
            close=_float_required(row.get("c") or row.get("close")),
            volume=_float_or_none(row.get("v") or row.get("volume")),
            source_tier=CFD_PRIMARY,
        )
        for row in rows
    ]


def normalize_bingx_klines(
    *,
    provider: str,
    canonical_symbol: str,
    provider_symbol: str,
    rows: list[Any],
) -> list[FtmoProviderBar]:
    """Normalize BingX client kline objects or dictionaries to provider bars."""
    bars: list[FtmoProviderBar] = []
    for row in rows:
        if isinstance(row, Mapping):
            open_time = (
                row.get("open_time_ms") or row.get("openTime") or row.get("time") or row.get("t")
            )
            open_value = row.get("open") or row.get("o")
            high_value = row.get("high") or row.get("h")
            low_value = row.get("low") or row.get("l")
            close_value = row.get("close") or row.get("c")
            volume_value = row.get("volume") or row.get("v")
        else:
            open_time = getattr(row, "open_time_ms", None)
            open_value = getattr(row, "open", None)
            high_value = getattr(row, "high", None)
            low_value = getattr(row, "low", None)
            close_value = getattr(row, "close", None)
            volume_value = getattr(row, "volume", None)
        bars.append(
            FtmoProviderBar(
                provider=provider,
                canonical_symbol=canonical_symbol,
                provider_symbol=provider_symbol,
                timeframe="1d",
                timestamp=_parse_timestamp(open_time),
                open=_float_required(open_value),
                high=_float_required(high_value),
                low=_float_required(low_value),
                close=_float_required(close_value),
                volume=_float_or_none(volume_value),
                source_tier=BINGX_MARKET_PRIMARY,
            )
        )
    return bars


def _fetch_provider_bars(
    db_path: Path | str,
    *,
    provider: str,
    request: Any,
    normalize: Any,
    client: httpx.Client | None,
) -> list[FtmoProviderBar]:
    owns_client = client is None
    http = client or httpx.Client(timeout=45.0)
    started = time.perf_counter()
    try:
        response = request(http)
        response.raise_for_status()
        rows = _extract_provider_rows(response.json())
        if not rows:
            insert_ftmo_provider_health(
                db_path,
                provider=provider,
                status="empty_response",
                ok=False,
                latency_ms=_elapsed_ms(started),
                error="provider returned no candle rows",
                rate_limit_remaining=_rate_limit_remaining(response),
            )
            return []
        bars = normalize(rows)
        inserted = insert_ftmo_provider_snapshots(db_path, bars)
        insert_ftmo_provider_health(
            db_path,
            provider=provider,
            status="ok",
            ok=True,
            latency_ms=_elapsed_ms(started),
            error=None,
            rate_limit_remaining=_rate_limit_remaining(response),
        )
        logger.info(
            "ftmo_provider_fetch provider=%s bars=%d inserted=%d", provider, len(bars), inserted
        )
        return bars
    except httpx.HTTPStatusError as exc:
        status = _status_from_http_code(exc.response.status_code)
        _record_provider_failure(
            db_path,
            provider=provider,
            status=status,
            error=str(exc),
            latency_ms=_elapsed_ms(started),
        )
    except httpx.TimeoutException as exc:
        _record_provider_failure(
            db_path,
            provider=provider,
            status="timeout",
            error=str(exc),
            latency_ms=_elapsed_ms(started),
        )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        _record_provider_failure(
            db_path,
            provider=provider,
            status="error",
            error=str(exc),
            latency_ms=_elapsed_ms(started),
        )
    finally:
        if owns_client:
            http.close()
    return []


def _extract_provider_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("quotes", "data", "results", "candles", "ohlc"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    nested = payload.get("result")
    if isinstance(nested, dict):
        return _extract_provider_rows(nested)
    return []


def _record_provider_failure(
    db_path: Path | str,
    *,
    provider: str,
    status: str,
    error: str,
    latency_ms: float | None = None,
) -> None:
    insert_ftmo_provider_health(
        db_path,
        provider=provider,
        status=status,
        ok=False,
        latency_ms=latency_ms,
        error=error,
        rate_limit_remaining=None,
    )


def _status_from_http_code(status_code: int) -> str:
    if status_code in {401, 403}:
        return "unauthorized"
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "rate_limited"
    return "error"


def _rate_limit_remaining(response: Any) -> int | None:
    headers = getattr(response, "headers", {}) or {}
    for key in ("x-ratelimit-remaining", "X-RateLimit-Remaining", "ratelimit-remaining"):
        value = headers.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _env_value(env: Mapping[str, str] | None, key: str) -> str | None:
    source = os.environ if env is None else env
    value = source.get(key)
    return str(value).strip() if value else None


def _select_primary_provider(canonical_symbol: str, counts: dict[str, int]) -> str | None:
    if canonical_symbol in BINGX_PRIMARY_SYMBOLS:
        order = (
            "bingx_market",
            "binance_spot",
            "binance_usdm",
            "fmp_massive_polygon",
            "tradermade_cfd",
            "metaapi_mt5",
            "infoway_candles",
            "existing_proxy",
        )
    else:
        order = (
            "fmp_massive_polygon",
            "bingx_market",
            "tradermade_cfd",
            "metaapi_mt5",
            "infoway_candles",
            "binance_spot",
            "binance_usdm",
            "existing_proxy",
        )
    for provider in order:
        if counts.get(provider, 0) > 0:
            return provider
    return None


def _empty_readiness(provider_health: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "primary_provider": "fmp_massive_polygon",
        "broker_validation_status": "missing",
        "source_tier": UNAVAILABLE,
        "fidelity_score": None,
        "proxy_fallback_reason": "no_provider_snapshots",
        "snapshot_rows": 0,
        "latest_timestamp": None,
        "latest_by_provider": {},
        "latest_provider_symbol_by_provider": {},
        "latest_usage_role_by_provider": {},
        "latest_created_by_provider": {},
        "provider_counts": {},
        "provider_health": provider_health,
        "fidelity_audit": None,
    }


def _default_health() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "status": "missing",
            "ok": False,
            "latency_ms": None,
            "error": "no health probe recorded",
            "rate_limit_remaining": None,
            "updated_at": None,
        }
        for name in provider_registry()
    }


def _load_health(
    con: sqlite3.Connection, registry: dict[str, FtmoProviderSpec]
) -> dict[str, dict[str, Any]]:
    health = _default_health()
    rows = con.execute(
        """
        SELECT provider, status, ok, latency_ms, error, rate_limit_remaining, updated_at
        FROM ftmo_provider_health
        """
    ).fetchall()
    for provider, status, ok, latency_ms, error, rate_limit_remaining, updated_at in rows:
        if provider not in registry:
            continue
        health[str(provider)] = {
            "status": status,
            "ok": bool(ok),
            "latency_ms": latency_ms,
            "error": error,
            "rate_limit_remaining": rate_limit_remaining,
            "updated_at": updated_at,
        }
    return health


def _latest_fidelity(con: sqlite3.Connection, canonical_symbol: str) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT primary_provider, comparison_provider, rows_compared, fidelity_score,
               tracking_error_pct, avg_spread_pct, correlation,
               max_abs_divergence_pct, median_abs_divergence_pct,
               p95_abs_divergence_pct, outlier_count_2pct, outlier_count_5pct,
               source_tier, created_at
        FROM ftmo_fidelity_audit
        WHERE canonical_symbol = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (canonical_symbol,),
    ).fetchone()
    if not row:
        return {}
    return {
        "primary_provider": row[0],
        "comparison_provider": row[1],
        "rows_compared": row[2],
        "fidelity_score": row[3],
        "tracking_error_pct": row[4],
        "avg_spread_pct": row[5],
        "correlation": row[6],
        "max_abs_divergence_pct": row[7],
        "median_abs_divergence_pct": row[8],
        "p95_abs_divergence_pct": row[9],
        "outlier_count_2pct": row[10],
        "outlier_count_5pct": row[11],
        "source_tier": row[12],
        "created_at": row[13],
    }


def _broker_status(counts: dict[str, int], health: dict[str, dict[str, Any]]) -> str:
    if counts.get("bingx_market", 0) > 0:
        return "bingx_market_available"
    if counts.get("binance_usdm", 0) > 0:
        return "binance_derivatives_available"
    if counts.get("metaapi_mt5", 0) > 0:
        return "available"
    meta_health = health.get("metaapi_mt5") or {}
    if meta_health.get("status") != "missing":
        return str(meta_health.get("status"))
    return "missing"


def _fallback_reason(counts: dict[str, int], health: dict[str, dict[str, Any]]) -> str:
    if (
        counts.get("fmp_massive_polygon", 0) > 0
        or counts.get("binance_spot", 0) > 0
        or counts.get("bingx_market", 0) > 0
    ):
        return ""
    if counts.get("existing_proxy", 0) > 0:
        return "primary_cfd_unavailable_using_existing_proxy"
    tm = health.get("tradermade_cfd") or {}
    if tm.get("status") == "missing_credentials":
        return "tradermade_credentials_missing"
    return "primary_cfd_unavailable"


def _sqlite_tables(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _date_key(value: datetime) -> str:
    return value.astimezone(UTC).date().isoformat()


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, int | float):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=UTC)
    text = str(value or "").strip()
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=UTC)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _spread(row: dict[str, Any]) -> float | None:
    explicit = _float_or_none(row.get("spread"))
    if explicit is not None:
        return explicit
    bid = _float_or_none(row.get("bid"))
    ask = _float_or_none(row.get("ask"))
    if bid is None or ask is None:
        return None
    return round(max(0.0, ask - bid), 10)


def _float_required(value: object) -> float:
    parsed = _float_or_none(value)
    if parsed is None:
        raise ValueError(f"required numeric value missing: {value!r}")
    return parsed


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _std(values: list[float]) -> float | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    if len(values) == 1:
        return 0.0
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    denom = math.sqrt(left_var * right_var)
    if denom == 0.0:
        return None
    return numerator / denom
