from __future__ import annotations
from typing import Any
"""Public Binance USD-M derivatives snapshots for Funding Lab BTC checks."""


import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

BINANCE_USDM_BASE_URL = "https://fapi.binance.com"
BINANCE_DATA_BASE_URL = "https://fapi.binance.com"
CRYPTO_DERIVATIVES_SOURCE_TIER = "binance_public_derivatives"


@dataclass(frozen=True)
class CryptoDerivativeSnapshot:
    symbol: str
    timestamp: datetime
    funding_rate: float | None = None
    open_interest: float | None = None
    basis: float | None = None
    taker_buy_sell_ratio: float | None = None
    source: str = "binance_usdm_public"

    @property
    def snapshot_id(self) -> str:
        stamp = self.timestamp.astimezone(UTC).isoformat()
        return f"{self.symbol.upper()}:{stamp}"


def init_crypto_derivatives_db(db_path: Path | str) -> None:
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS crypto_derivatives_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                funding_rate REAL,
                open_interest REAL,
                basis REAL,
                taker_buy_sell_ratio REAL,
                source TEXT NOT NULL,
                source_tier TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_crypto_derivatives_symbol_ts
                ON crypto_derivatives_snapshots(symbol, timestamp);
            """
        )


def insert_crypto_derivative_snapshots(
    db_path: Path | str, snapshots: list[CryptoDerivativeSnapshot]
) -> int:
    if not snapshots:
        return 0
    init_crypto_derivatives_db(db_path)
    now = datetime.now(tz=UTC).isoformat()
    rows = [
        (
            snapshot.snapshot_id,
            snapshot.symbol.upper(),
            snapshot.timestamp.astimezone(UTC).isoformat(),
            snapshot.funding_rate,
            snapshot.open_interest,
            snapshot.basis,
            snapshot.taker_buy_sell_ratio,
            snapshot.source,
            CRYPTO_DERIVATIVES_SOURCE_TIER,
            now,
        )
        for snapshot in snapshots
    ]
    with sqlite3.connect(db_path) as con:
        before = con.total_changes
        con.executemany(
            """
            INSERT OR IGNORE INTO crypto_derivatives_snapshots (
                snapshot_id, symbol, timestamp, funding_rate, open_interest,
                basis, taker_buy_sell_ratio, source, source_tier, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return int(con.total_changes - before)


def crypto_derivatives_coverage(db_path: Path | str, symbol: str) -> dict[str, Any]:
    db = Path(db_path)
    if not db.exists():
        return {
            "rows": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "source_tier": "missing",
        }
    con = sqlite3.connect(db)
    try:
        tables = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "crypto_derivatives_snapshots" not in tables:
            return {
                "rows": 0,
                "first_timestamp": None,
                "last_timestamp": None,
                "source_tier": "missing",
            }
        row = con.execute(
            """
            SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
            FROM crypto_derivatives_snapshots
            WHERE UPPER(symbol) = ?
            """,
            (symbol.upper(),),
        ).fetchone()
        rows = int(row[0] or 0)
        return {
            "rows": rows,
            "first_timestamp": row[1],
            "last_timestamp": row[2],
            "source_tier": CRYPTO_DERIVATIVES_SOURCE_TIER if rows else "missing",
        }
    finally:
        con.close()


def rehydrate_crypto_microstructure_snapshots(
    db_path: Path | str,
    *,
    derivatives_symbol: str = "BTCUSDT",
    prediction_symbols: tuple[str, ...] = ("BTC-USD", "BTCUSDT", "BTC/USDT", "BTCUSD"),
    dry_run: bool = False,
) -> dict[str, int]:
    """Merge daily Binance derivatives features into existing BTC feature snapshots."""
    db = Path(db_path)
    if not db.exists():
        return {"matched": 0, "updated": 0, "skipped": 0}
    con = sqlite3.connect(db)
    try:
        tables = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if not {"crypto_derivatives_snapshots", "predictions", "feature_snapshots"}.issubset(
            tables
        ):
            return {"matched": 0, "updated": 0, "skipped": 0}
        derivatives = _daily_derivative_features(con, derivatives_symbol)
        if not derivatives:
            return {"matched": 0, "updated": 0, "skipped": 0}
        placeholders = ",".join("?" for _ in prediction_symbols)
        rows = con.execute(
            f"""
            SELECT fs.snapshot_id, fs.timestamp, fs.features_json, fs.source_quality
            FROM feature_snapshots fs
            JOIN predictions p ON p.prediction_id = fs.prediction_id
            WHERE UPPER(p.symbol) IN ({placeholders})
            """,
            [symbol.upper() for symbol in prediction_symbols],
        ).fetchall()
        matched = 0
        updated = 0
        skipped = 0
        for snapshot_id, timestamp, features_raw, quality_raw in rows:
            features_for_day = derivatives.get(str(timestamp)[:10])
            if not features_for_day:
                skipped += 1
                continue
            matched += 1
            features = _json_dict(features_raw)
            quality = _json_dict(quality_raw)
            features.update(features_for_day["features"])
            quality.update(features_for_day["quality"])
            if dry_run:
                updated += 1
                continue
            con.execute(
                """
                UPDATE feature_snapshots
                SET features_json = ?, source_quality = ?
                WHERE snapshot_id = ?
                """,
                (
                    json.dumps(features, sort_keys=True),
                    json.dumps(quality, sort_keys=True),
                    snapshot_id,
                ),
            )
            updated += 1
        if not dry_run:
            con.commit()
        return {"matched": matched, "updated": updated, "skipped": skipped}
    finally:
        con.close()


def fetch_binance_btcusdt_derivatives(
    *,
    symbol: str = "BTCUSDT",
    days: int = 300,
    client: httpx.Client | None = None,
) -> list[CryptoDerivativeSnapshot]:
    """Fetch Binance public derivatives context for BTCUSDT.

    The endpoint set is intentionally public/read-only. Missing components are
    tolerated by returning partial snapshots; the Funding Lab gate still blocks
    BTC unless enough validated rows exist.
    """
    owns_client = client is None
    http = client or httpx.Client(timeout=30)
    try:
        start = datetime.now(tz=UTC) - timedelta(days=max(1, int(days)))
        start_ms = int(start.timestamp() * 1000)
        funding = _fetch_json(
            http,
            f"{BINANCE_USDM_BASE_URL}/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": start_ms, "limit": 1000},
        )
        oi = _fetch_json(
            http,
            f"{BINANCE_DATA_BASE_URL}/futures/data/openInterestHist",
            {"symbol": symbol, "period": "1d", "limit": 500},
        )
        basis = _fetch_json(
            http,
            f"{BINANCE_DATA_BASE_URL}/futures/data/basis",
            {"pair": symbol, "contractType": "PERPETUAL", "period": "1d", "limit": 500},
        )
        taker = _fetch_json(
            http,
            f"{BINANCE_DATA_BASE_URL}/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": "1d", "limit": 500},
        )
        return _merge_binance_snapshots(symbol, funding, oi, basis, taker, start=start)
    finally:
        if owns_client:
            http.close()


def _fetch_json(http: httpx.Client, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        response = http.get(url, params=params)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("binance_derivatives_fetch_failed url=%s error=%s", url, exc)
        return []
    data = response.json()
    return data if isinstance(data, list) else []


def _merge_binance_snapshots(
    symbol: str,
    funding: list[dict[str, Any]],
    oi: list[dict[str, Any]],
    basis: list[dict[str, Any]],
    taker: list[dict[str, Any]],
    *,
    start: datetime | None = None,
) -> list[CryptoDerivativeSnapshot]:
    by_day: dict[str, dict[str, Any]] = {}
    for item in funding:
        ts = _timestamp_from_ms(item.get("fundingTime"))
        if ts:
            by_day.setdefault(ts.date().isoformat(), {"timestamp": ts})["funding_rate"] = _float(
                item.get("fundingRate")
            )
    for item in oi:
        ts = _timestamp_from_ms(item.get("timestamp"))
        if ts:
            by_day.setdefault(ts.date().isoformat(), {"timestamp": ts})["open_interest"] = _float(
                item.get("sumOpenInterest")
            )
    for item in basis:
        ts = _timestamp_from_ms(item.get("timestamp"))
        if ts:
            by_day.setdefault(ts.date().isoformat(), {"timestamp": ts})["basis"] = _float(
                item.get("basisRate")
            )
    for item in taker:
        ts = _timestamp_from_ms(item.get("timestamp"))
        if ts:
            by_day.setdefault(ts.date().isoformat(), {"timestamp": ts})["taker_buy_sell_ratio"] = (
                _float(item.get("buySellRatio"))
            )

    snapshots: list[CryptoDerivativeSnapshot] = []
    for payload in by_day.values():
        snapshots.append(
            CryptoDerivativeSnapshot(
                symbol=symbol.upper(),
                timestamp=payload["timestamp"],
                funding_rate=payload.get("funding_rate"),
                open_interest=payload.get("open_interest"),
                basis=payload.get("basis"),
                taker_buy_sell_ratio=payload.get("taker_buy_sell_ratio"),
            )
        )
    if start is not None:
        snapshots = [item for item in snapshots if item.timestamp >= start]
    return sorted(snapshots, key=lambda item: item.timestamp)


def _daily_derivative_features(con: sqlite3.Connection, symbol: str) -> dict[str, dict[str, Any]]:
    rows = con.execute(
        """
        SELECT timestamp, funding_rate, open_interest, basis, taker_buy_sell_ratio
        FROM crypto_derivatives_snapshots
        WHERE UPPER(symbol) = ?
        ORDER BY timestamp ASC
        """,
        (symbol.upper(),),
    ).fetchall()
    if not rows:
        return {}
    series = {
        "funding_rate": [row[1] for row in rows],
        "open_interest": [row[2] for row in rows],
        "basis": [row[3] for row in rows],
        "taker_buy_sell_ratio": [row[4] for row in rows],
    }
    stats = {key: _mean_std(values) for key, values in series.items()}
    out: dict[str, dict[str, Any]] = {}
    previous_oi: float | None = None
    for timestamp, funding, oi, basis, taker_ratio in rows:
        oi_change = None
        if previous_oi not in {None, 0.0} and oi is not None:
            oi_change = (float(oi) / float(previous_oi)) - 1.0
        if oi is not None:
            previous_oi = float(oi)
        oi_stats = _mean_std(
            [
                (float(rows[i][2]) / float(rows[i - 1][2])) - 1.0
                for i in range(1, len(rows))
                if rows[i][2] is not None and rows[i - 1][2] not in {None, 0.0}
            ]
        )
        features = {
            "crypto__derivatives_available": 1.0,
            "crypto__funding_rate_zscore": _zscore(funding, *stats["funding_rate"]),
            "crypto__basis_zscore": _zscore(basis, *stats["basis"]),
            "crypto__open_interest_change_zscore": _zscore(oi_change, *oi_stats),
            "crypto__taker_buy_sell_ratio_zscore": _zscore(
                taker_ratio, *stats["taker_buy_sell_ratio"]
            ),
            "crypto__realized_volatility_zscore": 0.0,
        }
        present = sum(value is not None for value in (funding, oi, basis, taker_ratio))
        features["crypto__data_quality_score"] = round(present / 4.0, 4)
        out[str(timestamp)[:10]] = {
            "features": features,
            "quality": {
                "crypto_derivatives": True,
                "crypto_derivatives_source_tier": CRYPTO_DERIVATIVES_SOURCE_TIER,
                "crypto_derivatives_data_quality_score": features["crypto__data_quality_score"],
                "crypto_derivatives_missing_components": [
                    name
                    for name, value in {
                        "funding_rate": funding,
                        "open_interest": oi,
                        "basis": basis,
                        "taker_buy_sell_ratio": taker_ratio,
                    }.items()
                    if value is None
                ],
            },
        }
    return out


def _timestamp_from_ms(value: object) -> datetime | None:
    number = _float(value)
    if number is None:
        return None
    return datetime.fromtimestamp(number / 1000.0, tz=UTC)


def _mean_std(values: list[Any]) -> tuple[float | None, float | None]:
    nums = [_float(value) for value in values]
    clean = [value for value in nums if value is not None and math.isfinite(value)]
    if not clean:
        return None, None
    mean = sum(clean) / len(clean)
    if len(clean) < 2:
        return mean, None
    variance = sum((value - mean) ** 2 for value in clean) / (len(clean) - 1)
    return mean, math.sqrt(variance)


def _zscore(value: object, mean: float | None, std: float | None) -> float:
    number = _float(value)
    if number is None or mean is None or not std:
        return 0.0
    return max(-3.0, min(3.0, (number - mean) / std)) / 3.0


def _json_dict(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float(value: object) -> float | None:
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
