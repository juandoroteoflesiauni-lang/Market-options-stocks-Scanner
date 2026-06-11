"""Signals endpoint — Phase D execution signals (REST + WebSocket)."""

import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.api.contracts import SignalResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["signals"])
ws_router = APIRouter(tags=["signals"])


@router.get("/latest", response_model=list[SignalResponse])
async def get_latest_signals() -> list[SignalResponse]:
    """Returns the latest execution signals from Phase D.

    TODO: Wire to real Phase D signal buffer when monitor is running.
    """
    return []


# ── WebSocket for real-time signal streaming ───────────────────

_ws_clients: set[WebSocket] = set()


@ws_router.websocket("/ws/stream/{symbol}")
async def signal_stream(websocket: WebSocket, symbol: str) -> None:
    """WebSocket endpoint for real-time signal streaming to the frontend.

    The frontend connects here to receive ExecutionSignals as they are
    emitted by Phase D.
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info(
        "WebSocket client connected to stream %s. Total clients: %d",
        symbol,
        len(_ws_clients),
    )

    # Push current state to the new client immediately so the UI updates
    try:
        emitter = websocket.app.state.emitter
        for sym, price in emitter.prev_prices.items():
            base_price = emitter.base_prices.get(sym, price)
            price_change = price - base_price
            price_change_pct = ((price - base_price) / base_price) * 100 if base_price > 0 else 0.0
            payload = {
                "symbol": sym,
                "price": f"{price:.2f}",
                "priceChange": f"{price_change:.2f}",
                "priceChangePct": f"{price_change_pct:.2f}",
            }

            if hasattr(emitter, "am_prices") and sym in emitter.am_prices:
                payload["afterMarketPrice"] = f"{emitter.am_prices[sym]:.4f}"
                payload["afterMarketChangePct"] = f"{emitter.am_changes[sym]:.2f}"

            if hasattr(emitter, "candles") and sym in emitter.candles:
                payload["candles"] = emitter.candles[sym]

            import json

            await websocket.send_text(json.dumps(payload))
    except Exception as e:
        logger.warning("Could not push initial state: %s", e)

    try:
        while True:
            # Keep connection alive — receive heartbeats from client
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(websocket)
        logger.info(
            "WebSocket client disconnected from %s. Remaining clients: %d",
            symbol,
            len(_ws_clients),
        )
    except Exception:
        _ws_clients.discard(websocket)
        logger.error("WebSocket error", exc_info=True)


async def broadcast_signal(payload: dict[str, Any] | BaseModel) -> None:
    """Broadcasts a payload to all connected WebSocket clients.

    Called by Phase D signal emitter when a new tick or signal is generated.

    Args:
        payload: The execution signal or tick dictionary to broadcast.
    """
    if not _ws_clients:
        return

    import json

    if isinstance(payload, BaseModel):
        message = payload.model_dump_json()
    else:
        message = json.dumps(payload)

    dead_clients: set[WebSocket] = set()

    for client in _ws_clients.copy():
        try:
            await client.send_text(message)
        except Exception:
            dead_clients.add(client)

    for client in dead_clients:
        _ws_clients.discard(client)
