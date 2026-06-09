"""Signals endpoint — Phase D execution signals (REST + WebSocket)."""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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


@ws_router.websocket("/ws/signals")
async def signal_stream(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time signal streaming to the frontend.

    The frontend connects here to receive ExecutionSignals as they are
    emitted by Phase D. When Phase D is not yet wired, the connection
    stays alive with periodic heartbeats.
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info(
        "WebSocket client connected for signal stream. Total clients: %d",
        len(_ws_clients),
    )

    try:
        while True:
            # Keep connection alive — receive heartbeats from client
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(websocket)
        logger.info(
            "WebSocket client disconnected. Remaining clients: %d",
            len(_ws_clients),
        )
    except Exception:
        _ws_clients.discard(websocket)
        logger.error("WebSocket error", exc_info=True)


async def broadcast_signal(signal: SignalResponse) -> None:
    """Broadcasts a signal to all connected WebSocket clients.

    Called by Phase D signal emitter when a new signal is generated.

    Args:
        signal: The execution signal to broadcast.
    """
    if not _ws_clients:
        return

    message = signal.model_dump_json()
    dead_clients: set[WebSocket] = set()

    for client in _ws_clients.copy():
        try:
            await client.send_text(message)
        except Exception:
            dead_clients.add(client)

    for client in dead_clients:
        _ws_clients.discard(client)
