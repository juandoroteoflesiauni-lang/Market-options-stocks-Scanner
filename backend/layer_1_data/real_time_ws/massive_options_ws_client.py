"""Massive / Polygon-style options WebSocket (quotes).

Uses the same JSON ``action`` / ``params`` handshake as ``MassiveWSClient``
(stocks). Subscribe with ``Q.<ticker>`` where ``ticker`` is ``O:AAPL250117C00150000``.

If the upstream URL or plan does not match, the coroutine exits cleanly after
logging — callers should not treat absence of messages as fatal.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


def normalize_option_tickers(raw: list[str]) -> list[str]:
    """Deduplicate and enforce ``O:`` OCC prefix."""
    out: list[str] = []
    for t in raw:
        s = str(t).strip().upper()
        if not s:
            continue
        if not s.startswith("O:"):
            s = f"O:{s}"
        if s not in out:
            out.append(s)
    return out


def _auth_failed(payload: object) -> bool:
    if not isinstance(payload, list) or len(payload) == 0:
        return False
    first = payload[0]
    if not isinstance(first, dict):
        return False
    return str(first.get("status", "")).lower() == "auth_failed"


async def stream_massive_option_quotes(
    api_keys: list[str],
    ws_url: str,
    option_symbols: list[str],
    on_message: Callable[[dict[str, Any]], Awaitable[None]],
    stop: asyncio.Event,
) -> None:
    """Connect with rotating API keys; subscribe to ``Q.<O:…>``; forward events until ``stop``."""
    keys = [k.strip() for k in api_keys if k and str(k).strip()]
    tickers = normalize_option_tickers(option_symbols)
    if not keys or not tickers:
        logger.info("massive_options_ws: skip — missing keys or tickers")
        return

    sub_params = ",".join(f"Q.{sym}" for sym in tickers)
    if len(sub_params) > 50_000:
        logger.warning("massive_options_ws: subscribe string very long (%s chars)", len(sub_params))

    ws: Any = None
    try:
        for api_key in keys:
            if stop.is_set():
                return
            try:
                logger.info("massive_options_ws: connecting %s", ws_url)
                ws = await websockets.connect(ws_url, ping_interval=20, ping_timeout=20)
                greeting = await ws.recv()
                logger.debug("massive_options_ws: greeting %s", greeting)

                await ws.send(json.dumps({"action": "auth", "params": api_key}))
                auth_raw = await ws.recv()
                auth_data = json.loads(auth_raw)
                if _auth_failed(auth_data):
                    logger.warning("massive_options_ws: auth_failed — next key")
                    await ws.close()
                    ws = None
                    continue
                logger.info("massive_options_ws: auth ok, subscribing %d contracts", len(tickers))
                await ws.send(json.dumps({"action": "subscribe", "params": sub_params}))
                break
            except (TimeoutError, ConnectionClosed, OSError) as exc:
                logger.warning("massive_options_ws: connect/auth error: %s", exc)
                if ws is not None:
                    await ws.close()
                    ws = None
                continue
        if ws is None:
            logger.error("massive_options_ws: all API keys failed for %s", ws_url)
            return

        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
            except TimeoutError:
                continue
            except ConnectionClosed:
                break
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and stop.is_set():
                        return
                    if isinstance(item, dict):
                        await on_message(item)
            elif isinstance(data, dict):
                await on_message(data)
    finally:
        if ws is not None:
            try:  # noqa: SIM105
                await ws.close()
            except Exception:
                pass
        logger.info("massive_options_ws: session ended")
