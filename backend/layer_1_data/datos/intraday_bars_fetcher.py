"""Intraday OHLCV Bars Fetcher — QuantumAnalyzer Layer 1.

Fetches tick-aggregated OHLCV data (candlestick bars) for multiple timeframes
using the Polygon.io REST API (primary), Alpaca Markets (secondary), and
Massive/Polygon mirror keys (tertiary fallback).

Supported intervals: 1s, 5m, 15m, 30m, 1h, 4h, 1d (1s vía Polygon/Massive REST; Alpaca no expone 1s).
"""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

try:
    from config.logger_setup import get_logger
    from config.settings import Config, load_settings
except ModuleNotFoundError:  # pragma: no cover
    from backend.config.logger_setup import get_logger
    from backend.config.settings import Config, load_settings

from backend.layer_1_data.datos.massive_options_fetcher import (
    _ensure_polygon_api_key_on_url,
    _massive_key_bindings,
    _rest_hosts,
)

logger = get_logger(__name__)

# ─── Polygon interval mapping ──────────────────────────────────────────────────
_POLYGON_MULTIPLIER: dict[str, int] = {
    "1s": 1,
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1,
}
_POLYGON_TIMESPAN: dict[str, str] = {
    "1s": "second",
    "1m": "minute",
    "5m": "minute",
    "15m": "minute",
    "30m": "minute",
    "1h": "minute",
    "4h": "minute",
    "1d": "day",
}
# How many calendar days back to look for each timeframe (institutional defaults)
_LOOKBACK_DAYS: dict[str, int] = {
    "1s": 7,
    "1m": 90,
    "5m": 180,
    "15m": 365,
    "30m": 730,
    "1h": 1825,
    "4h": 3650,
    "1d": 7300,
}
# Max bars returned per interval (increased for institutional deep history)
_MAX_BARS: dict[str, int] = {
    "1s": 50_000,
    "1m": 50_000,
    "5m": 50_000,
    "15m": 50_000,
    "30m": 50_000,
    "1h": 50_000,
    "4h": 50_000,
    "1d": 20_000,
}

VALID_INTERVALS = Literal["1s", "1m", "5m", "15m", "30m", "1h", "4h", "1d"]

_NY = ZoneInfo("America/New_York")


def _ny_aggs_date_range(interval: str, lookback_override: int | None = None) -> tuple[str, str]:
    """Rango ``from``/``to`` YYYY-MM-DD alineado al calendario de la bolsa (ET)."""
    lookback = lookback_override if lookback_override is not None else _LOOKBACK_DAYS[interval]
    end_ny = datetime.now(tz=_NY)
    start_ny = end_ny - timedelta(days=lookback)
    d0 = start_ny.date().isoformat()
    end_date = end_ny.date()
    if _POLYGON_TIMESPAN[interval] != "day":
        end_date = end_date + timedelta(days=1)
    return d0, end_date.isoformat()


def _parse_bar(row: dict[str, Any]) -> dict[str, float | int] | None:
    """Normalise a single Polygon/Alpaca aggs result row."""
    o, h, lo, c = row.get("o"), row.get("h"), row.get("l"), row.get("c")
    if any(x is None for x in (o, h, lo, c)):
        return None
    try:
        fo, fh, fl, fc = float(o), float(h), float(lo), float(c)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) and x > 0 for x in (fo, fh, fl, fc)):
        return None
    vol_raw = row.get("v")
    try:
        vol = float(vol_raw) if vol_raw is not None else 0.0
    except (TypeError, ValueError):
        vol = 0.0
    ts_raw = row.get("t")  # Unix ms for Polygon
    try:
        ts = int(ts_raw) if ts_raw is not None else 0
    except (TypeError, ValueError):
        ts = 0
    return {"t": ts, "open": fo, "high": fh, "low": fl, "close": fc, "volume": max(vol, 0.0)}


def _merge_polygon_raw_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une páginas de aggs y deduplica por timestamp ``t`` (ms)."""
    by_t: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        tr = row.get("t")
        try:
            k = int(tr) if tr is not None else 0
        except (TypeError, ValueError):
            continue
        if k > 0:
            by_t[k] = row
    return [by_t[k] for k in sorted(by_t.keys())]


def _fetch_polygon_aggs_host(
    symbol: str,
    interval: str,
    api_key: str,
    host: str,
    d0: str,
    d1: str,
    *,
    limit: int = 50_000,
) -> list[dict[str, Any]] | None:
    """
    Polygon/Massive v2 aggs con paginación ``next_url``.

    Sin paginar, la primera página puede omitir las velas más recientes (p. ej. sesión del día en curso).
    """
    multiplier = _POLYGON_MULTIPLIER[interval]
    timespan = _POLYGON_TIMESPAN[interval]
    base = host.rstrip("/")
    path = f"{base}/v2/aggs/ticker/{symbol.upper()}/range/{multiplier}/{timespan}/{d0}/{d1}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": limit,
        "apiKey": api_key,
    }
    raw_chunks: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=45.0) as client:
            next_url: str | None = None
            pages = 0
            max_pages = 30
            while pages < max_pages:
                if next_url:
                    r = client.get(_ensure_polygon_api_key_on_url(next_url, api_key))
                else:
                    r = client.get(path, params=params)
                pages += 1
                if r.status_code != 200:
                    if pages == 1:
                        logger.debug(
                            "polygon_intraday: HTTP %s host=%s %s/%s",
                            r.status_code,
                            base.replace("https://", ""),
                            symbol,
                            interval,
                        )
                        return None
                    break
                body = r.json()
                if not isinstance(body, dict):
                    break
                chunk = body.get("results")
                if isinstance(chunk, list):
                    for row in chunk:
                        if isinstance(row, dict):
                            raw_chunks.append(row)
                nu = body.get("next_url")
                next_url = nu if isinstance(nu, str) and nu.strip() else None
                if not next_url:
                    break
        if not raw_chunks:
            return None
        merged = _merge_polygon_raw_rows(raw_chunks)
        bars = [b for row in merged if (b := _parse_bar(row)) is not None]
        if not bars:
            return None
        max_bars = _MAX_BARS[interval]
        return bars[-max_bars:] if len(bars) > max_bars else bars
    except Exception as exc:
        logger.debug(
            "polygon_intraday: exception host=%s %s/%s: %s",
            base.replace("https://", ""),
            symbol,
            interval,
            exc,
        )
        return None


def _fetch_polygon_bars_all_hosts(
    symbol: str,
    interval: str,
    api_key: str,
    settings: Config,
    max_bars_override: int | None = None,
    lookback_days: int | None = None,
) -> tuple[list[dict[str, Any]] | None, str]:
    """Prueba ``api.polygon.io`` y ``api.massive.com`` (y ``MASSIVE_REST_BASE_URLS``) con la misma clave."""
    d0, d1 = _ny_aggs_date_range(interval, lookback_override=lookback_days)
    for host in _rest_hosts(settings):
        bars = _fetch_polygon_aggs_host(
            symbol, interval, api_key, host, d0, d1, limit=max_bars_override or 50_000
        )
        if bars:
            return bars, host.replace("https://", "")
    return None, ""


def _fetch_fmp_bars(
    symbol: str,
    interval: str,
    api_key: str,
    max_bars: int = 1000,
) -> list[dict[str, Any]] | None:
    """FMP intraday bars as an alternative source."""
    fmp_tf: dict[str, str] = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1hour",
        "4h": "4hour",
    }
    tf = fmp_tf.get(interval)
    if not tf:
        return None

    url = f"https://financialmodelingprep.com/stable/historical-chart/{tf}"
    params = {"symbol": symbol.upper(), "apikey": api_key}
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(url, params=params)
        if r.status_code != 200:
            logger.debug("fmp_intraday: HTTP %s for %s/%s", r.status_code, symbol, interval)
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            return None

        bars: list[dict[str, Any]] = []
        try:
            from zoneinfo import ZoneInfo

            ny_tz = ZoneInfo("America/New_York")
        except ImportError:
            import pytz

            ny_tz = pytz.timezone("America/New_York")

        for row in reversed(data):
            if not isinstance(row, dict):
                continue
            dt_str = row.get("date")
            if not dt_str:
                continue
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=ny_tz)
                ts = int(dt.timestamp() * 1000)
            except Exception:
                continue

            fmp_row = {
                "t": ts,
                "o": row.get("open"),
                "h": row.get("high"),
                "l": row.get("low"),
                "c": row.get("close"),
                "v": row.get("volume"),
            }
            b = _parse_bar(fmp_row)
            if b:
                bars.append(b)
        if not bars:
            return None
        return bars[-max_bars:] if len(bars) > max_bars else bars
    except Exception as exc:
        logger.debug("fmp_intraday: exception %s/%s: %s", symbol, interval, exc)
        return None


def _fetch_alpaca_bars(
    symbol: str,
    interval: str,
    api_key: str,
    secret_key: str,
    base_url: str,
    max_bars: int = 1000,
    lookback_days: int = 5,
) -> list[dict[str, Any]] | None:
    """Alpaca Markets intraday bars as a secondary source."""
    alpaca_tf: dict[str, str] = {
        "1m": "1Min",
        "5m": "5Min",
        "15m": "15Min",
        "30m": "30Min",
        "1h": "1Hour",
        "4h": "4Hour",
        "1d": "1Day",
    }
    tf = alpaca_tf.get(interval)
    if not tf:
        return None

    end_ny = datetime.now(tz=_NY)
    start_ny = end_ny - timedelta(days=lookback_days)
    start_dt = start_ny.astimezone(UTC)
    # Fin explícito “ahora” UTC para no cortar la sesión US del día en curso frente a un to solo fecha.
    end_dt = datetime.now(tz=UTC)

    url = f"{base_url.rstrip('/')}/v2/stocks/{symbol.upper()}/bars"
    params = {
        "timeframe": tf,
        "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": max_bars,
        "adjustment": "all",
        "feed": "iex",
    }
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(url, params=params, headers=headers)
        if r.status_code != 200:
            logger.debug("alpaca_intraday: HTTP %s for %s/%s", r.status_code, symbol, interval)
            return None
        body = r.json()
        raw = body.get("bars")
        if not isinstance(raw, list) or not raw:
            return None
        bars: list[dict[str, Any]] = []
        for row in raw:
            alpaca_row = {
                "t": (
                    int(datetime.fromisoformat(row["t"].replace("Z", "+00:00")).timestamp() * 1000)
                    if isinstance(row.get("t"), str)
                    else 0
                ),
                "o": row.get("o"),
                "h": row.get("h"),
                "l": row.get("l"),
                "c": row.get("c"),
                "v": row.get("v"),
            }
            b = _parse_bar(alpaca_row)
            if b:
                bars.append(b)
        max_bars = _MAX_BARS[interval]
        return bars[-max_bars:] if len(bars) > max_bars else bars
    except Exception as exc:
        logger.debug("alpaca_intraday: exception for %s/%s: %s", symbol, interval, exc)
        return None


def fetch_intraday_bars(
    symbol: str,
    interval: str = "5m",
    *,
    settings: Config | None = None,
    max_bars: int | None = None,
    lookback_days: int | None = None,
    accept_stale_current_session: bool = False,
) -> dict[str, Any]:
    """
    Fetch intraday OHLCV bars for a given symbol and interval.

    Priority:
        1. FMP Enterprise when Market Scanner requests it
        2. Polygon.io (POLYGON_KEY env var)
        3. Massive/Polygon mirror keys (from options fetcher key rotation)
        4. Alpaca Markets (ALPACA_API_KEY + ALPACA_SECRET_KEY)

    Returns:
        dict with keys: bars, interval, source, count, error
        Each bar: {t (unix ms), open, high, low, close, volume}
    """
    if interval not in _POLYGON_MULTIPLIER:
        return {
            "bars": [],
            "interval": interval,
            "source": "",
            "count": 0,
            "error": f"Invalid interval '{interval}'. Valid: {list(_POLYGON_MULTIPLIER.keys())}",
        }

    cfg = settings or load_settings()
    sym = symbol.upper().strip()
    fmp_key = (
        getattr(cfg, "fmp_key_market", None)
        or getattr(cfg, "fmp_key_technical", None)
        or getattr(cfg, "fmp_api_key", None)
        or os.getenv("FMP_API_KEY", "")
    )
    scanner_provider = str(
        getattr(cfg, "market_scanner_data_provider", "fmp_enterprise") or ""
    ).lower()
    prefer_fmp = (
        accept_stale_current_session
        and bool(getattr(cfg, "market_scanner_fmp_primary", True))
        and scanner_provider == "fmp_enterprise"
    )

    if prefer_fmp and fmp_key:
        bars = _fetch_fmp_bars(
            sym,
            interval,
            fmp_key,
            max_bars=max_bars or 1000,
        )
        if bars:
            logger.info("intraday_bars: %d bars %s/%s via FMP Enterprise", len(bars), sym, interval)
            return {
                "bars": bars,
                "interval": interval,
                "source": "fmp_enterprise",
                "count": len(bars),
                "error": None,
            }

    # ── 1. Polygon primary key ────────────────────────────────────────────────
    polygon_key = os.getenv("POLYGON_KEY", "") or getattr(cfg, "polygon_key", "") or ""
    if polygon_key:
        bars, host_used = _fetch_polygon_bars_all_hosts(
            sym, interval, polygon_key, cfg, max_bars_override=max_bars, lookback_days=lookback_days
        )
        if bars:
            src = f"polygon_primary@{host_used}" if host_used else "polygon_primary"
            logger.info("intraday_bars: %d bars %s/%s via %s", len(bars), sym, interval, src)
            return {
                "bars": bars,
                "interval": interval,
                "source": src,
                "count": len(bars),
                "error": None,
            }

    # ── 2. Alpaca secondary ───────────────────────────────────────────────────
    alpaca_key = os.getenv("ALPACA_API_KEY", "") or getattr(cfg, "alpaca_api_key", "") or ""
    alpaca_secret = (
        os.getenv("ALPACA_SECRET_KEY", "") or getattr(cfg, "alpaca_secret_key", "") or ""
    )
    alpaca_base = os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets")
    if alpaca_key and alpaca_secret:
        bars = _fetch_alpaca_bars(
            sym,
            interval,
            alpaca_key,
            alpaca_secret,
            alpaca_base,
            max_bars=max_bars or 1000,
            lookback_days=lookback_days or 5,
        )
        if bars:
            logger.info("intraday_bars: %d bars %s/%s via Alpaca", len(bars), sym, interval)
            return {
                "bars": bars,
                "interval": interval,
                "source": "alpaca",
                "count": len(bars),
                "error": None,
            }

    # ── 3. FMP Enterprise/Fallback ───────────────────────────────────────────
    if fmp_key and not prefer_fmp:
        bars = _fetch_fmp_bars(
            sym,
            interval,
            fmp_key,
            max_bars=max_bars or 1000,
        )
        if bars:
            logger.info("intraday_bars: %d bars %s/%s via FMP", len(bars), sym, interval)
            return {
                "bars": bars,
                "interval": interval,
                "source": "fmp",
                "count": len(bars),
                "error": None,
            }

    # ── 4. Massive/Polygon mirror keys ────────────────────────────────────────
    keys = _massive_key_bindings(cfg)
    for label, api_key in keys:
        bars, host_used = _fetch_polygon_bars_all_hosts(
            sym, interval, api_key, cfg, max_bars_override=max_bars, lookback_days=lookback_days
        )
        if bars:
            src = f"massive_{label}@{host_used}" if host_used else f"massive_{label}"
            logger.info("intraday_bars: %d bars %s/%s via %s", len(bars), sym, interval, src)
            return {
                "bars": bars,
                "interval": interval,
                "source": src,
                "count": len(bars),
                "error": None,
            }

    logger.warning("intraday_bars: all sources failed for %s/%s", sym, interval)
    return {
        "bars": [],
        "interval": interval,
        "source": "",
        "count": 0,
        "error": f"All sources failed for {sym}/{interval}",
    }
