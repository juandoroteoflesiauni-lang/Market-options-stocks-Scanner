"""WebSocket routes for ultra-low-latency venue mirrors."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.config.logger_setup import get_logger
from backend.services.bingx_live_ticker_hub import get_live_ticker_hub

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["websocket"])


@router.websocket("/ws/live-ticker")
async def live_ticker_stream(websocket: WebSocket) -> None:
    """Stream BingX account + mark/ticker updates tick-by-tick to the dashboard.

    Message contract (JSON text frames):
    - ``type: "snapshot"`` — initial state right after connect.
    - ``type: "tick"`` — incremental update (account + positions mirror).

    Each payload includes:
    - ``account``: ``total_equity``, ``available_margin``, ``used_margin``
    - ``positions``: rows with ``current_spot``, ``pnl_real_apalancado``, ``current_zone``
    """
    await websocket.accept()
    hub = get_live_ticker_hub()
    await hub.register(websocket)
    logger.info("websocket.live_ticker.connected clients=%s", hub.client_count)
    try:
        while True:
            # Keep the connection alive; venue data is server-pushed.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("websocket.live_ticker.closed error=%s", exc)
    finally:
        await hub.unregister(websocket)
