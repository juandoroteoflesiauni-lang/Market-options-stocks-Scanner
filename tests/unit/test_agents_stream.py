"""Tests for agent SSE stream generator."""

from __future__ import annotations

import pytest

from backend.domain.agentic_models import StreamEventType
from backend.services.ai_core.agent_manager import AgentManager


@pytest.mark.asyncio
async def test_stream_emits_done_last() -> None:
    async def fake_stream(preferred, request):
        _ = (preferred, request)
        yield "chunk"

    class _FakeRouter:
        async def stream(self, preferred, request):
            async for c in fake_stream(preferred, request):
                yield c

    manager = AgentManager(llm_callable=lambda *a, **k: __import__("asyncio").sleep(0))
    manager._router = _FakeRouter()  # type: ignore[assignment]

    events = []
    async for event in manager.orquestar_analisis_stream("test context"):
        events.append(event)

    assert events
    assert events[-1].event_type == StreamEventType.DONE
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)


@pytest.mark.asyncio
async def test_stream_continues_after_agent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = AgentManager()

    class _FailRouter:
        async def stream(self, preferred, request):
            _ = (preferred, request)
            raise RuntimeError("provider down")
            yield ""  # pragma: no cover

    manager._router = _FailRouter()  # type: ignore[assignment]

    events = []
    async for event in manager.orquestar_analisis_stream("ctx"):
        events.append(event)
        if len(events) > 30:
            break

    error_events = [e for e in events if e.event_type == StreamEventType.ERROR]
    assert error_events
    assert any(e.event_type == StreamEventType.DONE for e in events)
