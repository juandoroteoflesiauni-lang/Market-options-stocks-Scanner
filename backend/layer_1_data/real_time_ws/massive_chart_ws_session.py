from __future__ import annotations
from typing import Any
"""
Massive / Polygon stocks WebSocket bridge for live chart updates.

Docs (Massive, ex-Polygon): trades ``T.{sym}``, second aggregates ``A.{sym}`` (1s chart),
minute aggregates ``AM.{sym}`` on ``wss://socket.massive.com/stocks``.

Keys: ``MASSIVE_KEY_OPTIONS_PRIMARY``, ``MASSIVE_KEY_OPTIONS_SECONDARY``, then
``MASSIVE_KEY_OPTIONS``, ``MASSIVE_KEY_WS_TRADES`` — first successful auth wins.
"""


import asyncio
import contextlib
import json
import logging
import math
from collections.abc import Callable

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from backend.config.settings import Config
from backend.layer_1_data.datos.intraday_bars_fetcher import fetch_intraday_bars

logger = logging.getLogger(__name__)

_TF_BUCKET_MS: dict[str, int] = {
    "1s": 1_000,
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def _json_sanitize(obj: object) -> object:
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {str(k): _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_sanitize(v) for v in obj]
    if isinstance(obj, tuple):
        return [_json_sanitize(v) for v in obj]
    try:
        xf = float(obj)
        if math.isnan(xf) or math.isinf(xf):
            return None
        return xf
    except (TypeError, ValueError):
        return obj


def massive_chart_ws_keys(settings: Config) -> list[str]:
    """Deduped keys: primary/secondary options keys first, then general options / WS trades."""
    out: list[str] = []
    for k in (
        settings.massive_key_options_primary,
        settings.massive_key_options_secondary,
        settings.massive_key_options,
        settings.massive_key_ws_trades,
    ):
        ks = str(k).strip() if k else ""
        if ks and ks not in out:
            out.append(ks)
    return out


def _norm_start_ms(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    # Polygon/Massive often use seconds for some aggregate ``s`` fields.
    if v < 1_000_000_000_000:
        v *= 1000
    return v


def _bucket_start_ms(ts_ms: int, tf: str) -> int:
    step = _TF_BUCKET_MS.get(tf, 60_000)
    return (ts_ms // step) * step


def massive_subscribe_params(symbol: str, tf: str) -> str:
    """Polygon/Massive stocks WS: ``T`` always; ``A`` = second bar, ``AM`` = minute bar."""
    u = symbol.upper().strip()
    if tf == "1s":
        return f"T.{u},A.{u}"
    return f"T.{u},AM.{u}"


def _auth_failed(payload: object) -> bool:
    if not isinstance(payload, list) or len(payload) == 0:
        return False
    first = payload[0]
    if not isinstance(first, dict):
        return False
    return str(first.get("status", "")).lower() == "auth_failed"


async def run_massive_chart_bridge(
    websocket: WebSocket,
    symbol: str,
    *,
    settings: Config,
    process_candles: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    """
    Drive ``websocket`` with the same JSON shapes as ``/ws/chart`` (Alpaca):
    initial array of candles with indicators, then single-row updates.
    """
    sym = symbol.upper().strip()
    if not sym:
        await websocket.send_json({"error": "symbol required"})
        await websocket.close()
        return

    keys = massive_chart_ws_keys(settings)
    if not keys:
        await websocket.send_json(
            {"error": "No Massive keys configured (MASSIVE_KEY_OPTIONS_PRIMARY / SECONDARY / …)"},
        )
        await websocket.close()
        return

    ws_url = (settings.massive_ws_url or "wss://socket.massive.com/stocks").strip()
    candle_buffer: list[dict[str, Any]] = []
    buffer_lock = asyncio.Lock()
    current_tf = "5m"
    shutdown = asyncio.Event()
    upstream: Any = None

    async def connect_upstream(subscribe_params: str) -> Any:
        nonlocal upstream
        for api_key in keys:
            if shutdown.is_set():
                return None
            try:
                logger.info("massive_chart_ws: connecting %s", ws_url)
                conn = await websockets.connect(ws_url, ping_interval=20, ping_timeout=20)
                _ = await conn.recv()
                await conn.send(json.dumps({"action": "auth", "params": api_key}))
                auth_raw = await conn.recv()
                auth_data = json.loads(auth_raw)
                if _auth_failed(auth_data):
                    logger.warning("massive_chart_ws: auth_failed — next key")
                    await conn.close()
                    continue
                await conn.send(json.dumps({"action": "subscribe", "params": subscribe_params}))
                logger.info("massive_chart_ws: subscribed %s", subscribe_params)
                upstream = conn
                return conn
            except (TimeoutError, ConnectionClosed, OSError, json.JSONDecodeError) as exc:
                logger.warning("massive_chart_ws: connect error: %s", exc)
                continue
        logger.error("massive_chart_ws: all keys failed for %s", ws_url)
        return None

    async def apply_trade_to_buffer(ts_ms: int, price: float, size: int) -> None:
        nonlocal candle_buffer
        async with buffer_lock:
            bar_start = _bucket_start_ms(ts_ms, current_tf)
            vol_add = max(int(size), 0) if isinstance(size, int) else 0

            idx = None
            for i, c in enumerate(candle_buffer):
                if int(c["time"]) == bar_start:
                    idx = i
                    break
            if idx is not None:
                c = candle_buffer[idx]
                c["close"] = price
                if price > float(c["high"]):
                    c["high"] = price
                if price < float(c["low"]):
                    c["low"] = price
                if vol_add:
                    c["volume"] = int(c.get("volume") or 0) + vol_add
            elif not candle_buffer or bar_start > int(candle_buffer[-1]["time"]):
                candle_buffer.append(
                    {
                        "time": bar_start,
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": vol_add,
                    },
                )
                if len(candle_buffer) > 12_000:
                    candle_buffer.pop(0)
            else:
                return

            if len(candle_buffer) >= 20:
                processed = process_candles(candle_buffer)
                await websocket.send_json(_json_sanitize({**processed[-1], "type": "tick"}))
            else:
                c = candle_buffer[-1]
                await websocket.send_json(_json_sanitize({**c, "type": "tick"}))

    async def apply_am_to_buffer(item: dict[str, Any]) -> None:
        """Minute aggregate (official OHLCV); bar time = bucket aligned to current_tf when tf==1m else merge into bucket."""
        nonlocal candle_buffer
        s_raw = item.get("s")
        start_ms = _norm_start_ms(s_raw)
        if start_ms is None:
            return
        o, h, lo, c = item.get("o"), item.get("h"), item.get("l"), item.get("c")
        v = item.get("v")
        if any(x is None for x in (o, h, lo, c)):
            return
        try:
            fo, fh, fl, fc = float(o), float(h), float(lo), float(c)
            vol = int(float(v)) if v is not None else 0
        except (TypeError, ValueError):
            return

        bar_start = start_ms if current_tf == "1m" else _bucket_start_ms(start_ms, current_tf)

        new_candle = {
            "time": bar_start,
            "open": fo,
            "high": fh,
            "low": fl,
            "close": fc,
            "volume": vol,
        }
        async with buffer_lock:
            if candle_buffer and int(candle_buffer[-1]["time"]) == bar_start:
                prev = candle_buffer[-1]
                new_candle["open"] = float(prev["open"])
                new_candle["high"] = max(float(prev["high"]), fh)
                new_candle["low"] = min(float(prev["low"]), fl)
                new_candle["volume"] = int(prev.get("volume") or 0) + vol
                candle_buffer[-1] = new_candle
            elif not candle_buffer or bar_start > int(candle_buffer[-1]["time"]):
                candle_buffer.append(new_candle)
                if len(candle_buffer) > 12_000:
                    candle_buffer.pop(0)
            else:
                for i, row in enumerate(candle_buffer):
                    if int(row["time"]) == bar_start:
                        new_candle["open"] = float(row["open"])
                        new_candle["high"] = max(float(row["high"]), fh)
                        new_candle["low"] = min(float(row["low"]), fl)
                        new_candle["volume"] = int(row.get("volume") or 0) + vol
                        candle_buffer[i] = new_candle
                        break
                else:
                    return

            if len(candle_buffer) >= 5:
                processed = process_candles(candle_buffer)
                await websocket.send_json(_json_sanitize(processed[-1]))
            else:
                await websocket.send_json(_json_sanitize(new_candle))

    async def apply_official_second_bar(item: dict[str, Any]) -> None:
        """Polygon per-second aggregate ``A`` (OHLCV for one second)."""
        nonlocal candle_buffer
        s_raw = item.get("s")
        start_ms = _norm_start_ms(s_raw)
        if start_ms is None:
            return
        bar_start = (start_ms // 1000) * 1000
        o, h, lo, c = item.get("o"), item.get("h"), item.get("l"), item.get("c")
        v = item.get("v")
        if any(x is None for x in (o, h, lo, c)):
            return
        try:
            fo, fh, fl, fc = float(o), float(h), float(lo), float(c)
            vol = int(float(v)) if v is not None else 0
        except (TypeError, ValueError):
            return
        new_candle = {
            "time": bar_start,
            "open": fo,
            "high": fh,
            "low": fl,
            "close": fc,
            "volume": vol,
        }
        async with buffer_lock:
            if candle_buffer and int(candle_buffer[-1]["time"]) == bar_start:
                candle_buffer[-1] = new_candle
            elif not candle_buffer or bar_start > int(candle_buffer[-1]["time"]):
                candle_buffer.append(new_candle)
                if len(candle_buffer) > 12_000:
                    candle_buffer.pop(0)
            else:
                for i, row in enumerate(candle_buffer):
                    if int(row["time"]) == bar_start:
                        candle_buffer[i] = new_candle
                        break
                else:
                    return
            if len(candle_buffer) >= 5:
                processed = process_candles(candle_buffer)
                await websocket.send_json(_json_sanitize(processed[-1]))
            else:
                await websocket.send_json(_json_sanitize(new_candle))

    async def on_massive_item(item: dict[str, Any]) -> None:
        if shutdown.is_set():
            return
        ev = item.get("ev")
        if ev == "T" and str(item.get("sym", "")).upper() == sym:
            ts_ms = _norm_start_ms(item.get("t"))
            p = item.get("p")
            s = item.get("s")
            if ts_ms is None or p is None:
                return
            try:
                fp = float(p)
            except (TypeError, ValueError):
                return
            sz = int(s) if isinstance(s, int) else (int(float(s)) if s is not None else 0)
            await apply_trade_to_buffer(ts_ms, fp, sz)
        elif ev == "AM" and str(item.get("sym", "")).upper() == sym:
            # Para tf > 1m las velas en vivo vienen de ticks (``T``); ``AM`` es minuto oficial.
            if current_tf == "1m":
                await apply_am_to_buffer(item)
        elif ev == "A" and str(item.get("sym", "")).upper() == sym:
            if current_tf == "1s":
                await apply_official_second_bar(item)

    async def backfill_and_start(tf_key: str) -> None:
        nonlocal candle_buffer, current_tf
        current_tf = tf_key if tf_key in _TF_BUCKET_MS else "5m"
        result = fetch_intraday_bars(sym, interval=current_tf, settings=settings)
        bars = result.get("bars") or []
        async with buffer_lock:
            candle_buffer = []
            for b in bars:
                t_raw = b.get("t")
                t_ms = _norm_start_ms(t_raw)
                if t_ms is None:
                    continue
                candle_buffer.append(
                    {
                        "time": int(t_ms),
                        "open": float(b["open"]),
                        "high": float(b["high"]),
                        "low": float(b["low"]),
                        "close": float(b["close"]),
                        "volume": int(float(b.get("volume") or 0)),
                    },
                )
            if len(candle_buffer) >= 5:
                payload = process_candles(candle_buffer)
            else:
                payload = candle_buffer
        await websocket.send_json(_json_sanitize(payload))
        logger.info(
            "massive_chart_ws: backfill sym=%s tf=%s bars=%s source=%s",
            sym,
            current_tf,
            len(candle_buffer),
            result.get("source"),
        )

    async def upstream_recv_loop(conn: Any) -> None:
        try:
            while not shutdown.is_set():
                raw = await asyncio.wait_for(conn.recv(), timeout=120.0)
                data = json.loads(raw)
                items = data if isinstance(data, list) else [data]
                for it in items:
                    if isinstance(it, dict):
                        await on_massive_item(it)
        except TimeoutError:
            logger.warning("massive_chart_ws: upstream recv timeout sym=%s", sym)
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("massive_chart_ws: upstream loop sym=%s: %s", sym, exc)

    pump: asyncio.Task[Any] | None = None
    conn: Any = None

    await backfill_and_start(current_tf)
    sub0 = massive_subscribe_params(sym, current_tf)
    conn = await connect_upstream(sub0)
    if conn is None:
        with contextlib.suppress(Exception):
            await websocket.send_json({"error": "Massive WebSocket auth failed for all keys"})
        return

    pump = asyncio.create_task(upstream_recv_loop(conn))

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                break
            action = data.get("action")
            if action == "change_timeframe":
                raw_tf = str(data.get("timeframe", "5m")).lower()
                new_tf = raw_tf if raw_tf in _TF_BUCKET_MS else "5m"
                if new_tf != current_tf:
                    if pump is not None:
                        pump.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await pump
                        pump = None
                    if conn is not None:
                        with contextlib.suppress(Exception):
                            await conn.close()
                        conn = None
                        upstream = None
                    await backfill_and_start(new_tf)
                    sub1 = massive_subscribe_params(sym, current_tf)
                    conn = await connect_upstream(sub1)
                    if conn is None:
                        with contextlib.suppress(Exception):
                            await websocket.send_json(
                                {"error": "Massive WebSocket auth failed after timeframe change"},
                            )
                        break
                    pump = asyncio.create_task(upstream_recv_loop(conn))
            elif action == "toggle_demo":
                pass
    finally:
        shutdown.set()
        if pump is not None:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
        if upstream is not None:
            with contextlib.suppress(Exception):
                await upstream.close()
            upstream = None
        logger.info("massive_chart_ws: session closed sym=%s", sym)
