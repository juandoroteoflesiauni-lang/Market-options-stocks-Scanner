"""SSE stream for live agent orchestration (cockpit). # [TH]"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from backend.services.ai_core.agent_manager import AgentManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

_HEARTBEAT_SECONDS = 15.0


async def _sse_generator(context: str) -> asyncio.AsyncIterator[str]:
    """Format AgentStreamEvent frames as SSE with periodic heartbeats."""
    manager = AgentManager()
    event_queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()

    async def _producer() -> None:
        try:
            async for event in manager.orquestar_analisis_stream(context):
                await event_queue.put(("event", event))
        except asyncio.CancelledError:
            await event_queue.put(None)
            raise
        except Exception as exc:
            await event_queue.put(("error", exc))
        finally:
            await event_queue.put(None)

    producer_task = asyncio.create_task(_producer())
    try:
        while True:
            try:
                item = await asyncio.wait_for(event_queue.get(), timeout=_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if item is None:
                break
            kind, payload = item
            if kind == "error":
                exc = payload
                logger.warning("agents_stream.failed error=%s", exc)
                err = {"event_type": "error", "agent": "system", "data": str(exc), "seq": -1}
                yield f"data: {json.dumps(err)}\n\n"
                yield (
                    "data: "
                    + json.dumps({"event_type": "done", "agent": "system", "data": "", "seq": -1})
                    + "\n\n"
                )
                break
            event = payload
            frame = event.model_dump(mode="json")  # type: ignore[union-attr]
            yield f"data: {json.dumps(frame)}\n\n"
    except asyncio.CancelledError:
        logger.info("agents_stream.client_disconnected")
        raise
    finally:
        producer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer_task


@router.get("/stream")
async def stream_agents(
    context: str = Query(default="Mercado US: contexto macro y opciones del dia."),
) -> StreamingResponse:
    """Stream agent debate state via Server-Sent Events."""
    return StreamingResponse(
        _sse_generator(context),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
