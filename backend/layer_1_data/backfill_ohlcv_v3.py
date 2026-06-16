from __future__ import annotations
from typing import Protocol, Any
"""Safe-max OHLCV backfill for QuantumAnalyzer model v3.

The module is intentionally isolated from the existing chart/backfill code.  It
probes available providers, ranks the ones that work, fans out symbol downloads
with provider-level guards, and appends normalized daily bars to DuckDB in an
idempotent way.
"""


import argparse
import asyncio
import contextlib
import io
import json
import math
import os
import random
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import logging
logger = logging.getLogger(__name__)


CANARY_SYMBOLS = ["AAPL", "SPY", "MSFT", "PARA"]
DEFAULT_YEARS = 6
DEFAULT_INTERVAL = "1d"
DEFAULT_UNIVERSE_LIMIT = 6_000
DEFAULT_MIN_AVG_VOLUME = 100_000
DEFAULT_DB_PATH = Path("data/quantum_analyzer.duckdb")
DEFAULT_MODEL_PATH = Path("artifacts/models/quantum_alpha_v3.pth")


class _SqlResult(Protocol):
    def fetchone(self: _SqlResult) -> tuple[object, ...] | None: ...


class _SqlConnection(Protocol):
    def execute(
        self: _SqlConnection,
        query: str,
        parameters: tuple[object, ...] = (),
    ) -> _SqlResult: ...


@dataclass(frozen=True)
class OhlcvBar:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    adjusted: bool
    source: str
    source_priority: int


@dataclass(frozen=True)
class ProviderCredential:
    label: str
    token: str
    secret: str | None = None

    @property
    def alias(self) -> str:
        token = self.token.strip()
        masked = "***" if len(token) <= 8 else f"{token[:4]}...{token[-4:]}"
        return f"{self.label}:{masked}"


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    credential_alias: str
    ok: bool
    latency_ms: float
    rows: int
    min_date: date | None
    max_date: date | None
    status_code: int | None = None
    error: str | None = None

    @property
    def score(self) -> float:
        if not self.ok:
            return -1.0
        depth = min(self.rows / 1_000.0, 5.0)
        speed = max(0.0, 2.0 - min(self.latency_ms, 2_000.0) / 1_000.0)
        return depth + speed


@dataclass(frozen=True)
class FetchOutcome:
    provider: str
    symbol: str
    bars: list[OhlcvBar]
    ok: bool
    error: str | None
    status_code: int | None
    latency_ms: float


@dataclass
class BackfillRunConfig:
    symbols: list[str]
    years: int = DEFAULT_YEARS
    dry_run: bool = False
    concurrency: int = 24
    batch_size: int = 5_000
    db_path: Path = DEFAULT_DB_PATH
    interval: str = DEFAULT_INTERVAL


@dataclass
class BackfillRunResult:
    run_id: str
    total_symbols: int
    symbols_with_data: int
    symbols_without_data: list[str]
    total_bars: int
    inserted_or_changed: int
    provider_health: list[ProviderHealth]
    provider_counts: dict[str, int]
    elapsed_seconds: float


class ProviderAdapter(Protocol):
    name: str
    source_priority: int

    async def probe(self, symbols: list[str], years: int) -> ProviderHealth: ...

    async def fetch_daily(self, symbol: str, years: int) -> FetchOutcome: ...


def _clean_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, int | float):
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        return datetime.fromtimestamp(raw, tz=UTC).date()
    text = str(value).strip()
    if not text:
        return None
    with_context = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(with_context).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _volume(value: object) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number) or number < 0:
        return 0
    return int(number)


def normalize_ohlcv_rows(
    symbol: str,
    rows: Iterable[dict[str, Any]],
    *,
    source: str,
    source_priority: int,
    adjusted: bool = True,
) -> list[OhlcvBar]:
    """Normalize provider payloads and keep the last valid row per date."""
    sym = _clean_symbol(symbol)
    by_date: dict[date, OhlcvBar] = {}
    for row in rows:
        d = _parse_date(
            row.get("date")
            or row.get("datetime")
            or row.get("timestamp")
            or row.get("t")
            or row.get("time")
        )
        o = _finite_positive(row.get("open") or row.get("o"))
        h = _finite_positive(row.get("high") or row.get("h"))
        low = _finite_positive(row.get("low") or row.get("l"))
        c = _finite_positive(
            row.get("adjClose")
            or row.get("adj_close")
            or row.get("adjclose")
            or row.get("close")
            or row.get("c")
        )
        if d is None or None in (o, h, low, c):
            continue
        open_px = float(o)
        high_px = float(h)
        low_px = float(low)
        close_px = float(c)
        if high_px < max(open_px, close_px) or low_px > min(open_px, close_px):
            continue
        by_date[d] = OhlcvBar(
            symbol=sym,
            date=d,
            open=open_px,
            high=high_px,
            low=low_px,
            close=close_px,
            volume=_volume(row.get("volume") or row.get("v")),
            adjusted=adjusted,
            source=source,
            source_priority=source_priority,
        )
    return [by_date[d] for d in sorted(by_date)]


def rank_providers(health: Iterable[ProviderHealth]) -> list[ProviderHealth]:
    return sorted((h for h in health if h.ok), key=lambda h: h.score, reverse=True)


class CircuitBreaker:
    def __init__(
        self,
        *,
        max_failures: int = 3,
        cooldown_seconds: float = 120.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.cooldown_seconds = cooldown_seconds
        self._now = now
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        opened = self._opened_at.get(key)
        if opened is None:
            return True
        if self._now() - opened >= self.cooldown_seconds:
            self._failures[key] = 0
            self._opened_at.pop(key, None)
            return True
        return False

    def record_success(self, key: str) -> None:
        self._failures[key] = 0
        self._opened_at.pop(key, None)

    def record_failure(self, key: str) -> None:
        failures = self._failures.get(key, 0) + 1
        self._failures[key] = failures
        if failures >= self.max_failures:
            self._opened_at[key] = self._now()


class AsyncRateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self._min_interval = 1.0 / max(requests_per_second, 0.1)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class HttpOhlcvProvider:
    name = "http"
    source_priority = 50

    def __init__(
        self,
        credential: ProviderCredential | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.credential = credential
        self.timeout_seconds = timeout_seconds

    async def probe(self, symbols: list[str], years: int) -> ProviderHealth:
        started = time.perf_counter()
        rows = 0
        min_d: date | None = None
        max_d: date | None = None
        last_status: int | None = None
        last_error: str | None = None
        for symbol in symbols:
            outcome = await self.fetch_daily(symbol, years)
            last_status = outcome.status_code
            if outcome.ok and outcome.bars:
                rows += len(outcome.bars)
                dates = [b.date for b in outcome.bars]
                min_d = min(dates) if min_d is None else min(min_d, min(dates))
                max_d = max(dates) if max_d is None else max(max_d, max(dates))
            elif outcome.error:
                last_error = outcome.error
        latency = (time.perf_counter() - started) * 1000.0
        return ProviderHealth(
            provider=self.name,
            credential_alias=self.credential.alias if self.credential else "none",
            ok=rows > 0,
            latency_ms=round(latency, 2),
            rows=rows,
            min_date=min_d,
            max_date=max_d,
            status_code=last_status,
            error=None if rows > 0 else last_error or "no_rows",
        )


class PolygonMassiveProvider(HttpOhlcvProvider):
    name = "polygon_massive"
    source_priority = 1

    def __init__(
        self, credential: ProviderCredential, host: str = "https://api.polygon.io"
    ) -> None:
        super().__init__(credential)
        self.host = host.rstrip("/")

    async def fetch_daily(self, symbol: str, years: int) -> FetchOutcome:
        import httpx

        started = time.perf_counter()
        sym = _clean_symbol(symbol)
        end = datetime.now(tz=UTC).date()
        start = end - timedelta(days=int(years * 366))
        url = f"{self.host}/v2/aggs/ticker/{sym}/range/1/day/{start.isoformat()}/{end.isoformat()}"
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50_000,
            "apiKey": self.credential.token,
        }
        raw_rows: list[dict[str, Any]] = []
        status: int | None = None
        error: str | None = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                next_url: str | None = None
                for _ in range(20):
                    resp = await client.get(next_url or url, params=None if next_url else params)
                    status = resp.status_code
                    if resp.status_code != 200:
                        error = f"http_{resp.status_code}"
                        break
                    body = resp.json()
                    chunk = body.get("results")
                    if isinstance(chunk, list):
                        raw_rows.extend(r for r in chunk if isinstance(r, dict))
                    next_raw = body.get("next_url")
                    if not isinstance(next_raw, str) or not next_raw:
                        break
                    sep = "&" if "?" in next_raw else "?"
                    next_url = f"{next_raw}{sep}apiKey={self.credential.token}"
        except Exception as exc:
            error = str(exc)
        bars = normalize_ohlcv_rows(
            sym, raw_rows, source=self.name, source_priority=self.source_priority
        )
        return FetchOutcome(
            self.name,
            sym,
            bars,
            ok=error is None,
            error=error,
            status_code=status,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )


class FmpHistoricalProvider(HttpOhlcvProvider):
    name = "fmp"
    source_priority = 3

    async def fetch_daily(self, symbol: str, years: int) -> FetchOutcome:
        import httpx

        started = time.perf_counter()
        sym = _clean_symbol(symbol)
        end = datetime.now(tz=UTC).date()
        start = end - timedelta(days=int(years * 366))
        url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{sym}"
        params = {"from": start.isoformat(), "to": end.isoformat(), "apikey": self.credential.token}
        status: int | None = None
        error: str | None = None
        raw_rows: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, params=params)
            status = resp.status_code
            if resp.status_code == 200:
                body = resp.json()
                hist = body.get("historical")
                raw_rows = hist if isinstance(hist, list) else []
            else:
                error = f"http_{resp.status_code}"
        except Exception as exc:
            error = str(exc)
        bars = normalize_ohlcv_rows(
            sym, raw_rows, source=self.name, source_priority=self.source_priority
        )
        return FetchOutcome(
            self.name,
            sym,
            bars,
            ok=error is None,
            error=error,
            status_code=status,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )


class TiingoProvider(HttpOhlcvProvider):
    name = "tiingo"
    source_priority = 2

    async def fetch_daily(self, symbol: str, years: int) -> FetchOutcome:
        import httpx

        started = time.perf_counter()
        sym = _clean_symbol(symbol)
        end = datetime.now(tz=UTC).date()
        start = end - timedelta(days=int(years * 366))
        url = f"https://api.tiingo.com/tiingo/daily/{sym}/prices"
        params = {"startDate": start.isoformat(), "endDate": end.isoformat(), "format": "json"}
        headers = {"Authorization": f"Token {self.credential.token}"}
        status: int | None = None
        error: str | None = None
        raw_rows: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, params=params, headers=headers)
            status = resp.status_code
            if resp.status_code == 200:
                body = resp.json()
                raw_rows = body if isinstance(body, list) else []
            else:
                error = f"http_{resp.status_code}"
        except Exception as exc:
            error = str(exc)
        bars = normalize_ohlcv_rows(
            sym, raw_rows, source=self.name, source_priority=self.source_priority
        )
        return FetchOutcome(
            self.name,
            sym,
            bars,
            ok=error is None,
            error=error,
            status_code=status,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )


class AlpacaProvider(HttpOhlcvProvider):
    name = "alpaca"
    source_priority = 4

    def __init__(self, credential: ProviderCredential, base_url: str) -> None:
        super().__init__(credential)
        self.base_url = base_url.rstrip("/")

    async def fetch_daily(self, symbol: str, years: int) -> FetchOutcome:
        import httpx

        started = time.perf_counter()
        sym = _clean_symbol(symbol)
        end = datetime.now(tz=UTC)
        start = end - timedelta(days=int(years * 366))
        url = f"{self.base_url}/v2/stocks/{sym}/bars"
        params = {
            "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 10_000,
            "adjustment": "all",
            "feed": os.getenv("ALPACA_BARS_FEED", "iex"),
        }
        headers = {
            "APCA-API-KEY-ID": self.credential.token,
            "APCA-API-SECRET-KEY": self.credential.secret or "",
        }
        status: int | None = None
        error: str | None = None
        raw_rows: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, params=params, headers=headers)
            status = resp.status_code
            if resp.status_code == 200:
                body = resp.json()
                bars = body.get("bars")
                raw_rows = bars if isinstance(bars, list) else []
            else:
                error = f"http_{resp.status_code}"
        except Exception as exc:
            error = str(exc)
        bars = normalize_ohlcv_rows(
            sym, raw_rows, source=self.name, source_priority=self.source_priority
        )
        return FetchOutcome(
            self.name,
            sym,
            bars,
            ok=error is None,
            error=error,
            status_code=status,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )


class YFinanceProvider(ProviderAdapter):
    name = "yfinance"
    source_priority = 10

    async def probe(self, symbols: list[str], years: int) -> ProviderHealth:
        started = time.perf_counter()
        rows = 0
        min_d: date | None = None
        max_d: date | None = None
        error: str | None = None
        for symbol in symbols:
            outcome = await self.fetch_daily(symbol, years)
            if outcome.bars:
                rows += len(outcome.bars)
                dates = [b.date for b in outcome.bars]
                min_d = min(dates) if min_d is None else min(min_d, min(dates))
                max_d = max(dates) if max_d is None else max(max_d, max(dates))
            elif outcome.error:
                error = outcome.error
        return ProviderHealth(
            self.name,
            "public",
            rows > 0,
            round((time.perf_counter() - started) * 1000.0, 2),
            rows,
            min_d,
            max_d,
            error=None if rows > 0 else error or "no_rows",
        )

    async def fetch_daily(self, symbol: str, years: int) -> FetchOutcome:
        started = time.perf_counter()
        sym = _clean_symbol(symbol)

        def _fetch() -> list[dict[str, Any]]:
            import yfinance as yf

            def _history() -> object:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                    io.StringIO()
                ):
                    return yf.Ticker(sym).history(
                        period=f"{years}y", interval="1d", auto_adjust=False
                    )

            try:
                df = _history()
            except Exception as exc:
                if "unable to open database file" not in str(exc).lower():
                    raise
                cache_dir = Path(".tmp/yfinance_cache")
                cache_dir.mkdir(parents=True, exist_ok=True)
                if hasattr(yf, "set_tz_cache_location"):
                    yf.set_tz_cache_location(str(cache_dir))
                df = _history()
            if df is None or df.empty:
                return []
            df = df.reset_index()
            rows: list[dict[str, Any]] = []
            for row in df.to_dict("records"):
                rows.append(
                    {
                        "date": row.get("Date") or row.get("Datetime"),
                        "open": row.get("Open"),
                        "high": row.get("High"),
                        "low": row.get("Low"),
                        "close": row.get("Adj Close") or row.get("Close"),
                        "volume": row.get("Volume"),
                    }
                )
            return rows

        error: str | None = None
        raw_rows: list[dict[str, Any]] = []
        try:
            raw_rows = await asyncio.to_thread(_fetch)
        except Exception as exc:
            error = str(exc)
        bars = normalize_ohlcv_rows(
            sym, raw_rows, source=self.name, source_priority=self.source_priority
        )
        return FetchOutcome(
            self.name,
            sym,
            bars,
            ok=error is None,
            error=error,
            status_code=None,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )


class Data912Provider(HttpOhlcvProvider):
    name = "data912"
    source_priority = 5

    def __init__(self, credential: ProviderCredential, base_url: str) -> None:
        super().__init__(credential)
        self.base_url = base_url.rstrip("/")

    async def fetch_daily(self, symbol: str, years: int) -> FetchOutcome:
        import httpx

        started = time.perf_counter()
        sym = _clean_symbol(symbol)
        status: int | None = None
        error: str | None = None
        raw_rows: list[dict[str, Any]] = []
        url = f"{self.base_url}/historical/stocks/{sym}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(
                    url, headers={"Authorization": f"Bearer {self.credential.token}"}
                )
            status = resp.status_code
            if resp.status_code == 200:
                body = resp.json()
                payload = body.get("data") if isinstance(body, dict) else body
                raw_rows = payload if isinstance(payload, list) else []
            else:
                error = f"http_{resp.status_code}"
        except Exception as exc:
            error = str(exc)
        cutoff = datetime.now(tz=UTC).date() - timedelta(days=int(years * 366))
        bars = [
            b
            for b in normalize_ohlcv_rows(
                sym, raw_rows, source=self.name, source_priority=self.source_priority
            )
            if b.date >= cutoff
        ]
        return FetchOutcome(
            self.name,
            sym,
            bars,
            ok=error is None,
            error=error,
            status_code=status,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )


class BackfillStorage:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)  # nosec # NOSONAR
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def init_schema(self) -> None:
        import duckdb

        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ohlcv_daily_v3 (
                    symbol VARCHAR NOT NULL,
                    date DATE NOT NULL,
                    interval VARCHAR NOT NULL DEFAULT '1d',
                    open DOUBLE NOT NULL,
                    high DOUBLE NOT NULL,
                    low DOUBLE NOT NULL,
                    close DOUBLE NOT NULL,
                    volume BIGINT NOT NULL,
                    adjusted BOOLEAN NOT NULL,
                    source VARCHAR NOT NULL,
                    source_priority INTEGER NOT NULL,
                    inserted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS backfill_ohlcv_v3_runs (
                    run_id VARCHAR PRIMARY KEY,
                    started_at TIMESTAMP,
                    finished_at TIMESTAMP,
                    years INTEGER,
                    dry_run BOOLEAN,
                    total_symbols INTEGER,
                    symbols_with_data INTEGER,
                    total_bars BIGINT,
                    inserted_or_changed BIGINT,
                    config_json JSON
                );
                CREATE TABLE IF NOT EXISTS backfill_ohlcv_v3_health (
                    run_id VARCHAR,
                    provider VARCHAR,
                    credential_alias VARCHAR,
                    ok BOOLEAN,
                    latency_ms DOUBLE,
                    rows BIGINT,
                    min_date DATE,
                    max_date DATE,
                    status_code INTEGER,
                    error VARCHAR
                );
                CREATE TABLE IF NOT EXISTS backfill_ohlcv_v3_universe (
                    run_id VARCHAR,
                    symbol VARCHAR,
                    rank INTEGER,
                    source VARCHAR
                );
                CREATE OR REPLACE VIEW training_samples_v3 AS
                SELECT
                    symbol,
                    date,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    adjusted,
                    source
                FROM ohlcv_daily_v3
                WHERE interval = '1d';
                """
            )

    def upsert_bars(self, bars: list[OhlcvBar], *, interval: str = "1d") -> int:
        if not bars:
            return 0
        import duckdb

        rows = [
            (
                b.symbol,
                b.date,
                interval,
                b.open,
                b.high,
                b.low,
                b.close,
                b.volume,
                b.adjusted,
                b.source,
                b.source_priority,
            )
            for b in bars
        ]
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TEMP TABLE incoming_ohlcv_v3 (
                    symbol VARCHAR,
                    date DATE,
                    interval VARCHAR,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume BIGINT,
                    adjusted BOOLEAN,
                    source VARCHAR,
                    source_priority INTEGER
                )
                """
            )
            conn.executemany(
                "INSERT INTO incoming_ohlcv_v3 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
            )
            conn.execute(
                """
                CREATE TEMP TABLE best_ohlcv_v3 AS
                SELECT * EXCLUDE (rn)
                FROM (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY symbol, date, interval
                            ORDER BY source_priority ASC
                        ) AS rn
                    FROM incoming_ohlcv_v3
                )
                WHERE rn = 1
                """
            )
            changed = conn.execute(
                """
                SELECT COUNT(*)
                FROM best_ohlcv_v3 b
                LEFT JOIN ohlcv_daily_v3 t
                  ON t.symbol = b.symbol
                 AND t.date = b.date
                 AND t.interval = b.interval
                WHERE t.symbol IS NULL
                   OR t.open <> b.open
                   OR t.high <> b.high
                   OR t.low <> b.low
                   OR t.close <> b.close
                   OR t.volume <> b.volume
                   OR t.adjusted <> b.adjusted
                   OR t.source_priority <> b.source_priority
                   OR t.source <> b.source
                """
            ).fetchone()[0]
            conn.execute(
                """
                DELETE FROM ohlcv_daily_v3 t
                USING best_ohlcv_v3 b
                WHERE t.symbol = b.symbol
                  AND t.date = b.date
                  AND t.interval = b.interval
                """
            )
            conn.execute(
                """
                INSERT INTO ohlcv_daily_v3 (
                    symbol, date, interval, open, high, low, close, volume,
                    adjusted, source, source_priority
                )
                SELECT
                    symbol, date, interval, open, high, low, close, volume,
                    adjusted, source, source_priority
                FROM best_ohlcv_v3
                """
            )
            return int(changed)

    def record_run(self, result: BackfillRunResult, config: BackfillRunConfig) -> None:
        import duckdb

        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO backfill_ohlcv_v3_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    datetime.now(tz=UTC) - timedelta(seconds=result.elapsed_seconds),
                    datetime.now(tz=UTC),
                    config.years,
                    config.dry_run,
                    result.total_symbols,
                    result.symbols_with_data,
                    result.total_bars,
                    result.inserted_or_changed,
                    json.dumps(
                        {
                            "symbols": config.symbols,
                            "concurrency": config.concurrency,
                            "batch_size": config.batch_size,
                            "interval": config.interval,
                        }
                    ),
                ),
            )
            conn.executemany(
                """
                INSERT INTO backfill_ohlcv_v3_health VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        result.run_id,
                        h.provider,
                        h.credential_alias,
                        h.ok,
                        h.latency_ms,
                        h.rows,
                        h.min_date,
                        h.max_date,
                        h.status_code,
                        h.error,
                    )
                    for h in result.provider_health
                ],
            )
            conn.executemany(
                "INSERT INTO backfill_ohlcv_v3_universe VALUES (?, ?, ?, ?)",
                [(result.run_id, s, i + 1, "input") for i, s in enumerate(config.symbols)],
            )


class BackfillScheduler:
    def __init__(
        self,
        providers: list[ProviderAdapter],
        *,
        config: BackfillRunConfig,
        storage: BackfillStorage | None = None,
    ) -> None:
        self.providers = providers
        self.config = config
        self.storage = storage
        self.breaker = CircuitBreaker()
        self.limiters = {p.name: AsyncRateLimiter(self._default_rps(p.name)) for p in providers}

    @staticmethod
    def _default_rps(provider: str) -> float:
        return {
            "yfinance": 3.0,
            "polygon_massive": 4.0,
            "tiingo": 3.0,
            "fmp": 2.0,
            "alpaca": 3.0,
            "data912": 1.0,
        }.get(provider, 1.0)

    async def _probe_providers(self) -> tuple[list[ProviderHealth], list[ProviderAdapter]]:
        health = await asyncio.gather(
            *(p.probe(CANARY_SYMBOLS, self.config.years) for p in self.providers),
            return_exceptions=True,
        )
        health_rows: list[ProviderHealth] = []
        by_name = {p.name: p for p in self.providers}
        for provider, item in zip(self.providers, health, strict=True):
            if isinstance(item, Exception):
                health_rows.append(
                    ProviderHealth(
                        provider.name, "unknown", False, 0.0, 0, None, None, error=str(item)
                    )
                )
            else:
                health_rows.append(item)
        ranked_names = [h.provider for h in rank_providers(health_rows)]
        ranked = [by_name[name] for name in ranked_names if name in by_name]
        return health_rows, ranked

    async def _fetch_symbol(
        self, symbol: str, providers: list[ProviderAdapter]
    ) -> tuple[str, list[OhlcvBar], str | None]:
        sym = _clean_symbol(symbol)
        for provider in providers:
            key = provider.name
            if not self.breaker.allow(key):
                continue
            await self.limiters[key].acquire()
            outcome = await provider.fetch_daily(sym, self.config.years)
            if outcome.ok and outcome.bars:
                self.breaker.record_success(key)
                return sym, outcome.bars, provider.name
            if not outcome.ok:
                self.breaker.record_failure(key)
                await asyncio.sleep(min(5.0, 0.25 + random.random() * 0.5))
        return sym, [], None

    async def run(self) -> BackfillRunResult:
        started = time.perf_counter()
        run_id = str(uuid.uuid4())
        symbols = [_clean_symbol(s) for s in self.config.symbols if _clean_symbol(s)]
        health_rows, ranked_providers = await self._probe_providers()
        if not ranked_providers:
            ranked_providers = self.providers

        semaphore = asyncio.Semaphore(max(1, self.config.concurrency))
        pending: list[OhlcvBar] = []
        inserted_or_changed = 0
        total_bars = 0
        provider_counts: dict[str, int] = {}
        without_data: list[str] = []

        if self.storage and not self.config.dry_run:
            self.storage.init_schema()

        async def worker(symbol: str) -> tuple[str, list[OhlcvBar], str | None]:
            async with semaphore:
                return await self._fetch_symbol(symbol, ranked_providers)

        for coro in asyncio.as_completed([worker(s) for s in symbols]):
            sym, bars, provider_name = await coro
            if not bars:
                without_data.append(sym)
                continue
            total_bars += len(bars)
            if provider_name:
                provider_counts[provider_name] = provider_counts.get(provider_name, 0) + len(bars)
            if self.config.dry_run or self.storage is None:
                continue
            pending.extend(bars)
            if len(pending) >= self.config.batch_size:
                inserted_or_changed += self.storage.upsert_bars(
                    pending, interval=self.config.interval
                )
                pending = []

        if pending and self.storage and not self.config.dry_run:
            inserted_or_changed += self.storage.upsert_bars(pending, interval=self.config.interval)

        result = BackfillRunResult(
            run_id=run_id,
            total_symbols=len(symbols),
            symbols_with_data=len(symbols) - len(without_data),
            symbols_without_data=sorted(without_data),
            total_bars=total_bars,
            inserted_or_changed=inserted_or_changed,
            provider_health=health_rows,
            provider_counts=provider_counts,
            elapsed_seconds=round(time.perf_counter() - started, 2),
        )
        if self.storage and not self.config.dry_run:
            self.storage.record_run(result, self.config)
        return result


def _env_credentials(prefixes: list[str]) -> list[ProviderCredential]:
    creds: list[ProviderCredential] = []
    for name in prefixes:
        value = os.getenv(name)
        if value:
            creds.append(ProviderCredential(label=name.lower(), token=value.strip()))
    return creds


def _load_dotenv_if_present(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE lines without overriding the process environment."""
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = raw_value.strip().strip('"').strip("'")
        os.environ[key] = value


def create_default_providers() -> list[ProviderAdapter]:
    _load_dotenv_if_present()
    providers: list[ProviderAdapter] = []
    hosts = [
        h.strip()
        for h in (
            os.getenv("MASSIVE_REST_BASE_URLS") or "https://api.polygon.io,https://api.massive.com"
        ).split(",")
        if h.strip()
    ]
    massive_names = [
        "POLYGON_KEY",
        "MASSIVE_KEY_HIST_OHLCV",
        "MASSIVE_KEY_SNAPSHOT",
        "MASSIVE_KEY_FINANCIALS",
        "MASSIVE_KEY_OPTIONS_PRIMARY",
        "MASSIVE_KEY_OPTIONS_SECONDARY",
        "MASSIVE_KEY_FALLBACK",
    ]
    seen_tokens: set[str] = set()
    for cred in _env_credentials(massive_names):
        if cred.token in seen_tokens:
            continue
        seen_tokens.add(cred.token)
        for host in hosts:
            providers.append(PolygonMassiveProvider(cred, host=host))

    for cred in _env_credentials(
        ["TIINGO_API_KEY_1", "TIINGO_API_KEY_2", "TIINGO_API_KEY_3", "TIINGO_API_KEY"]
    ):
        providers.append(TiingoProvider(cred))

    for cred in _env_credentials(["FMP_KEY_MARKET", "FMP_KEY_QUOTES", "FMP_KEY_TECHNICAL"]):
        providers.append(FmpHistoricalProvider(cred))

    alpaca_key = os.getenv("ALPACA_API_KEY")
    alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
    if alpaca_key and alpaca_secret:
        providers.append(
            AlpacaProvider(
                ProviderCredential("alpaca", alpaca_key, secret=alpaca_secret),
                os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"),
            )
        )

    data912_key = os.getenv("DATA912_API_KEY")
    if data912_key:
        providers.append(
            Data912Provider(
                ProviderCredential("data912", data912_key),
                os.getenv("DATA912_BASE_URL", "https://data912.com"),
            )
        )

    providers.append(YFinanceProvider())
    return providers


def _iso_or_none(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _table_exists(conn: _SqlConnection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = ?
        """,
        (table_name,),
    ).fetchone()
    return bool(row and row[0])


def _safe_credential_alias(alias: str | None) -> str:
    if not alias:
        return ""
    text = str(alias)
    if "..." in text:
        return text
    label, sep, secret = text.partition(":")
    if not sep:
        if len(text) <= 8:
            return text
        return f"{text[:4]}...{text[-4:]}"
    if len(secret) <= 8:
        return f"{label}:{secret}"
    return f"{label}:{secret[:4]}...{secret[-4:]}"


def _model_artifact_status(model_path: Path = DEFAULT_MODEL_PATH) -> dict[str, Any]:
    expected = str(model_path)
    try:
        if not model_path.exists():
            return {
                "expectedPath": expected,
                "status": "missing",
                "sizeBytes": None,
                "modifiedAt": None,
            }
        stat = model_path.stat()
        return {
            "expectedPath": expected,
            "status": "available",
            "sizeBytes": int(stat.st_size),
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
    except OSError:
        return {
            "expectedPath": expected,
            "status": "unknown",
            "sizeBytes": None,
            "modifiedAt": None,
        }


def _empty_predictive_backfill_status(model_path: Path = DEFAULT_MODEL_PATH) -> dict[str, Any]:
    model = _model_artifact_status(model_path)
    quality = _predictive_quality_blocks(
        symbols=0,
        bars=0,
        samples=0,
        span_days=0,
        readiness="empty",
        model_status=model,
        providers=[],
    )
    return {
        "dataset": {
            "symbols": 0,
            "bars": 0,
            "samplesCandidate": 0,
            "minDate": None,
            "maxDate": None,
            "readiness": "empty",
        },
        "latestRun": {
            "runId": None,
            "startedAt": None,
            "finishedAt": None,
            "dryRun": None,
            "totalSymbols": 0,
            "symbolsWithData": 0,
            "totalBars": 0,
            "insertedOrChanged": 0,
        },
        "providers": [],
        "model": model,
        "explanation": {
            "currentStage": "trained" if model["status"] == "available" else "needs_backfill",
            "nextAction": "Correr el backfill OHLCV V3 real para poblar DuckDB antes de entrenar.",
        },
        **quality,
    }


def _engine_coverage_summary_safe() -> dict[str, Any]:
    try:
        from backend.services.predictive_engine_audit import summarize_engine_coverage

        return summarize_engine_coverage()
    except Exception as exc:
        return {
            "total": 0,
            "wired": 0,
            "partially_wired": 0,
            "unused": 0,
            "blocked": 0,
            "broken": 0,
            "research_only": 0,
            "by_status": {},
            "error": str(exc),
        }


def _predictive_quality_blocks(
    *,
    symbols: int,
    bars: int,
    samples: int,
    span_days: int,
    readiness: str,
    model_status: dict[str, Any],
    providers: list[dict[str, Any]],
) -> dict[str, Any]:
    provider_ok = sum(1 for p in providers if p.get("ok"))
    data_status = readiness if bars else "empty"
    data_reasons: list[str] = []
    if symbols == 0:
        data_reasons.append("no_symbols")
    if bars == 0:
        data_reasons.append("no_bars")
    if provider_ok == 0 and providers:
        data_reasons.append("no_healthy_provider")
    if readiness != "ready":
        data_reasons.append("dataset_not_ready")

    model_reasons: list[str] = []
    if readiness != "ready":
        model_reasons.append("dataset_not_ready")
    if model_status.get("status") != "available":
        model_reasons.append("model_artifact_missing")
    gate_status = "approved" if not model_reasons else "blocked"

    return {
        "data_quality": {
            "status": data_status,
            "spanDays": int(span_days),
            "barsPerSymbol": round(float(bars / symbols), 2) if symbols else 0.0,
            "healthyProviders": provider_ok,
            "reasons": data_reasons,
        },
        "training_quality": {
            "primaryHorizonDays": 5,
            "samplesCandidate": int(samples),
            "walkForwardPolicy": "strict_expanding_window",
            "leakagePolicy": "features_at_t_only_targets_forward_1d_5d_10d",
            "minimumPromotionSamples": 300,
            "status": "sufficient" if samples >= 300 and readiness == "ready" else "insufficient",
        },
        "model_gate": {
            "status": gate_status,
            "reasons": model_reasons,
            "requires": "OOS walk-forward metrics must beat naive and rule-based baselines.",
        },
        "engine_coverage_summary": _engine_coverage_summary_safe(),
    }


def get_predictive_backfill_status(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    model_path: Path | str = DEFAULT_MODEL_PATH,
    ready_symbol_floor: int = 3_000,
    ready_min_days: int = 365 * 5,
) -> dict[str, Any]:
    """Return read-only dashboard metrics for the Predictive OHLCV V3 pipeline."""
    import duckdb

    db = Path(db_path)
    model = Path(model_path)
    if not db.exists():
        return _empty_predictive_backfill_status(model)

    try:
        with duckdb.connect(str(db), read_only=True) as conn:
            required = {
                "ohlcv_daily_v3",
                "backfill_ohlcv_v3_runs",
                "backfill_ohlcv_v3_health",
                "backfill_ohlcv_v3_universe",
            }
            if not all(_table_exists(conn, table) for table in required):
                return _empty_predictive_backfill_status(model)
            dataset_row = conn.execute(
                """
                SELECT COUNT(DISTINCT symbol), COUNT(*), MIN(date), MAX(date)
                FROM ohlcv_daily_v3
                WHERE interval = '1d'
                """
            ).fetchone()
            samples_row = conn.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN n > 26 THEN n - 26 ELSE 0 END), 0)
                FROM (
                    SELECT symbol, COUNT(*) AS n
                    FROM ohlcv_daily_v3
                    WHERE interval = '1d'
                    GROUP BY symbol
                )
                """
            ).fetchone()
            latest_run = conn.execute(
                """
                SELECT run_id, started_at, finished_at, dry_run, total_symbols,
                       symbols_with_data, total_bars, inserted_or_changed
                FROM backfill_ohlcv_v3_runs
                ORDER BY COALESCE(started_at, finished_at) DESC NULLS LAST
                LIMIT 1
                """
            ).fetchone()
            providers: list[dict[str, Any]] = []
            if latest_run:
                provider_rows = conn.execute(
                    """
                    SELECT provider, credential_alias, ok, rows, latency_ms, status_code, error
                    FROM backfill_ohlcv_v3_health
                    WHERE run_id = ?
                    ORDER BY ok DESC, rows DESC, latency_ms ASC
                    """,
                    (latest_run[0],),
                ).fetchall()
                providers = [
                    {
                        "provider": str(row[0]),
                        "credentialAlias": _safe_credential_alias(row[1]),
                        "ok": bool(row[2]),
                        "rows": int(row[3] or 0),
                        "latencyMs": float(row[4]) if row[4] is not None else None,
                        "statusCode": int(row[5]) if row[5] is not None else None,
                        "error": str(row[6]) if row[6] else None,
                    }
                    for row in provider_rows
                ]
    except Exception:
        return _empty_predictive_backfill_status(model)

    symbols = int(dataset_row[0] or 0) if dataset_row else 0
    bars = int(dataset_row[1] or 0) if dataset_row else 0
    min_date = dataset_row[2] if dataset_row else None
    max_date = dataset_row[3] if dataset_row else None
    samples = int(samples_row[0] or 0) if samples_row else 0
    span_days = (max_date - min_date).days if min_date and max_date else 0
    readiness = "empty"
    if bars > 0:
        readiness = (
            "ready" if symbols >= ready_symbol_floor and span_days >= ready_min_days else "partial"
        )

    model_status = _model_artifact_status(model)
    if model_status["status"] == "available":
        stage = "trained"
        next_action = (
            "El modelo QuantumAlpha V3 existe; conectar la carga automática al runtime Predictive."
        )
    elif readiness == "ready":
        stage = "ready_for_training"
        next_action = (
            "Entrenar QuantumAlpha V3 usando training_samples_v3 y guardar los pesos .pth."
        )
    else:
        stage = "needs_backfill"
        next_action = "Completar el backfill real para ampliar símbolos, barras y rango histórico."

    quality = _predictive_quality_blocks(
        symbols=symbols,
        bars=bars,
        samples=samples,
        span_days=span_days,
        readiness=readiness,
        model_status=model_status,
        providers=providers,
    )
    return {
        "dataset": {
            "symbols": symbols,
            "bars": bars,
            "samplesCandidate": samples,
            "minDate": _iso_or_none(min_date),
            "maxDate": _iso_or_none(max_date),
            "readiness": readiness,
        },
        "latestRun": {
            "runId": str(latest_run[0]) if latest_run else None,
            "startedAt": _iso_or_none(latest_run[1]) if latest_run else None,
            "finishedAt": _iso_or_none(latest_run[2]) if latest_run else None,
            "dryRun": bool(latest_run[3]) if latest_run and latest_run[3] is not None else None,
            "totalSymbols": int(latest_run[4] or 0) if latest_run else 0,
            "symbolsWithData": int(latest_run[5] or 0) if latest_run else 0,
            "totalBars": int(latest_run[6] or 0) if latest_run else 0,
            "insertedOrChanged": int(latest_run[7] or 0) if latest_run else 0,
        },
        "providers": providers,
        "model": model_status,
        "explanation": {
            "currentStage": stage,
            "nextAction": next_action,
        },
        **quality,
    }


async def discover_liquid_us_symbols(limit: int = DEFAULT_UNIVERSE_LIMIT) -> list[str]:
    """Best-effort US liquid universe discovery, with yfinance-compatible fallback."""
    import httpx

    _load_dotenv_if_present()
    symbols: list[str] = []
    fmp_key = os.getenv("FMP_KEY_MARKET") or os.getenv("FMP_KEY_QUOTES")
    if fmp_key:
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.get(
                    "https://financialmodelingprep.com/api/v3/stock-screener",
                    params={
                        "exchange": "NASDAQ,NYSE,AMEX",
                        "isActivelyTrading": "true",
                        "limit": limit,
                        "apikey": fmp_key,
                    },
                )
            if resp.status_code == 200:
                body = resp.json()
                if isinstance(body, list):
                    filtered = []
                    for row in body:
                        if not isinstance(row, dict):
                            continue
                        sym = str(row.get("symbol") or "").upper().strip()
                        volume = _volume(row.get("volume") or row.get("avgVolume"))
                        if _looks_like_common_us_symbol(sym) and volume >= DEFAULT_MIN_AVG_VOLUME:
                            filtered.append(sym)
                    symbols = filtered[:limit]
        except Exception:
            symbols = []
    if symbols:
        return symbols
    return [
        "SPY",
        "QQQ",
        "IWM",
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "GOOGL",
        "TSLA",
    ]


def _looks_like_common_us_symbol(symbol: str) -> bool:
    if not symbol or len(symbol) > 6:
        return False
    bad_fragments = ("-W", ".W", "-U", ".U", "-P", ".P", "WS", "WT")
    return not any(fragment in symbol for fragment in bad_fragments)


def _parse_symbols(value: str | None) -> list[str]:
    if not value:
        return []
    maybe_path = Path(value)  # nosec # NOSONAR
    if maybe_path.exists():
        return [
            line.strip()
            for line in maybe_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return [item.strip() for item in value.split(",") if item.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe-max OHLCV backfill for model v3.")
    parser.add_argument("--symbols", help="CSV symbols or path to a newline-delimited symbol file.")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--discover-liquid-us", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_UNIVERSE_LIMIT)
    return parser


def require_duckdb_for_write(*, dry_run: bool) -> None:
    if dry_run:
        return
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "DuckDB es requerido para una corrida real del backfill OHLCV V3.\n"
            "Reejecuta agregando la dependencia al entorno temporal de uv:\n"
            "  uv run --python .uv-python\\cpython-3.12.13-windows-x86_64-none\\python.exe "
            "--with httpx --with yfinance --with pandas --with duckdb "
            "scripts\\backfill_ohlcv_v3.py --discover-liquid-us --years 6 --concurrency 24\n"
            "Para probar sin DuckDB, usa --dry-run."
        ) from exc


async def main_async(argv: list[str] | None = None) -> BackfillRunResult:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    require_duckdb_for_write(dry_run=args.dry_run)
    symbols = _parse_symbols(args.symbols)
    if args.discover_liquid_us or not symbols:
        symbols = await discover_liquid_us_symbols(limit=args.limit)
    config = BackfillRunConfig(
        symbols=symbols,
        years=args.years,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
        batch_size=args.batch_size,
        db_path=Path(args.db_path),
    )
    storage = BackfillStorage(config.db_path)
    scheduler = BackfillScheduler(create_default_providers(), config=config, storage=storage)
    result = await scheduler.run()
    logger.info(format_run_result(result))
    return result


def format_run_result(result: BackfillRunResult) -> str:
    health_lines = [
        f"  - {h.provider} [{h.credential_alias}] ok={h.ok} rows={h.rows} latency_ms={h.latency_ms} status={h.status_code} error={h.error}"
        for h in result.provider_health
    ]
    return "\n".join(
        [
            "OHLCV V3 BACKFILL SUMMARY",
            f"run_id={result.run_id}",
            f"symbols={result.total_symbols}",
            f"symbols_with_data={result.symbols_with_data}",
            f"symbols_without_data={len(result.symbols_without_data)}",
            f"total_bars={result.total_bars}",
            f"inserted_or_changed={result.inserted_or_changed}",
            f"elapsed_seconds={result.elapsed_seconds}",
            f"provider_counts={json.dumps(result.provider_counts, sort_keys=True)}",
            "provider_health:",
            *health_lines,
        ]
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
