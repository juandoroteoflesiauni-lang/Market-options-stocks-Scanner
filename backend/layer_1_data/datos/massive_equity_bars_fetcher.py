"""Velas diarias del subyacente vía REST Massive / Polygon (misma auth que options snapshot).

Expone cierres (HV) y OHLCV completo para ``SMCEngine`` (Capa 3 técnico) en el snapshot de opciones.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import numpy as np
import pandas as pd

try:
    from config.logger_setup import get_logger
    from config.settings import Config, load_settings
except ModuleNotFoundError:  # pragma: no cover
    from backend.config.logger_setup import get_logger
    from backend.config.settings import Config, load_settings

from backend.layer_1_data.datos.massive_options_fetcher import (
    _api_denial_message,
    _massive_key_bindings,
    _rest_hosts,
)

logger = get_logger(__name__)


def _rows_from_aggs_results(results: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Convierte chunk Polygon/Massive a filas open/high/low/close/volume."""
    rows: list[dict[str, float]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        o, h, low, c, t = row.get("o"), row.get("h"), row.get("l"), row.get("c"), row.get("t")
        if o is None or h is None or low is None or c is None:
            continue
        try:
            fo, fh, fl, fc = float(o), float(h), float(low), float(c)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(x) and x > 0 for x in (fo, fh, fl, fc)):
            continue
        v_raw = row.get("v")
        try:
            vol = float(v_raw) if v_raw is not None else 0.0
        except (TypeError, ValueError):
            vol = 0.0
        vol = max(vol, 1.0)
        rows.append({"open": fo, "high": fh, "low": fl, "close": fc, "volume": vol, "t": t})
    return rows


def fetch_equity_daily_bars(
    symbol: str,
    *,
    settings: Config | None = None,
    lookback_calendar_days: int = 500,
) -> tuple[np.ndarray | None, pd.DataFrame | None, dict[str, Any]]:
    """
    Una sola petición REST: cierres ascendentes + DataFrame OHLCV para SMC.

    Returns:
        (closes, ohlcv_df, meta) — ``ohlcv_df`` None si falla; columnas: open, high, low, close, volume.
    """
    cfg = settings or load_settings()
    sym = symbol.upper().strip()
    meta: dict[str, Any] = {"symbol": sym, "bars": 0, "ohlcv_rows": 0, "source": "", "error": None}
    if not sym:
        meta["error"] = "empty_symbol"
        return None, None, meta

    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=int(lookback_calendar_days))
    d0, d1 = start.isoformat(), end.isoformat()
    hosts = _rest_hosts(cfg)
    keys = _massive_key_bindings(cfg)
    if not keys:
        meta["error"] = "no_massive_keys"
        logger.info("massive_equity_bars: no Massive keys — skip aggs")
        return None, None, meta

    for host in hosts:
        base = host.rstrip("/")
        url = f"{base}/v2/aggs/ticker/{sym}/range/1/day/{d0}/{d1}"
        base_params: dict[str, str | int] = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50_000,
        }
        for label, api_key in keys:
            for auth_style, params, headers in (
                ("apiKey", {**base_params, "apiKey": api_key}, {}),
                ("apikey", {**base_params, "apikey": api_key}, {}),
                ("Bearer", dict(base_params), {"Authorization": f"Bearer {api_key}"}),
            ):
                try:
                    with httpx.Client(timeout=45.0) as client:
                        r = client.get(url, params=params, headers=headers)
                except Exception as exc:
                    logger.debug(
                        "massive_equity_bars: request err host=%s sym=%s: %s", host, sym, exc
                    )
                    continue
                if r.status_code != 200:
                    if r.status_code in (401, 403, 404):
                        logger.debug(
                            "massive_equity_bars: %s %s HTTP %s — %s",
                            label,
                            host.replace("https://", ""),
                            r.status_code,
                            _api_denial_message(r.text)[:200],
                        )
                    continue
                try:
                    body = r.json()
                except json.JSONDecodeError:
                    continue
                if not isinstance(body, dict):
                    continue
                results = body.get("results")
                if not isinstance(results, list) or not results:
                    continue
                rows = _rows_from_aggs_results(results)
                if len(rows) < 5:
                    continue
                df = pd.DataFrame(rows)
                closes = df["close"].to_numpy(dtype=np.float64)
                src = f"{label}@{base.replace('https://', '')}"
                meta.update(
                    {
                        "bars": len(closes),
                        "ohlcv_rows": len(df),
                        "source": src,
                        "auth_style": auth_style,
                        "error": None,
                    }
                )
                logger.info(
                    "massive_equity_bars: %d OHLCV rows for %s via %s",
                    len(df),
                    sym,
                    src,
                )
                return closes, df, meta

    meta["error"] = "all_hosts_keys_failed"
    logger.warning("massive_equity_bars: no daily aggs for %s", sym)
    return None, None, meta


def fetch_equity_daily_closes(
    symbol: str,
    *,
    settings: Config | None = None,
    lookback_calendar_days: int = 500,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Solo cierres — delega en :func:`fetch_equity_daily_bars` (una petición HTTP)."""
    closes, _df, meta = fetch_equity_daily_bars(
        symbol, settings=settings, lookback_calendar_days=lookback_calendar_days
    )
    return closes, meta
