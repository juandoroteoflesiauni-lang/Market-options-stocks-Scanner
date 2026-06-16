"""Unit tests for LLMContextCache."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.config.cache_thresholds import CacheThresholds
from backend.domain.agentic_models import CachedContextEntry
from backend.services.ai_core.llm_context_cache import LLMContextCache, build_context_cache_key
from backend.services.cache_manager import HierarchicalCache


@pytest.mark.asyncio
async def test_cache_hit_on_second_call() -> None:
    memory = HierarchicalCache()
    cache = LLMContextCache(
        memory_cache=memory,
        thresholds=CacheThresholds(llm_context_bucket_seconds=300, llm_context_ttl_seconds=300),
    )
    calls = 0

    async def fetcher() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"value": "macro"}

    first, hit1 = await cache.get_or_fetch(
        feature="macro_snapshot",
        symbol="SPY",
        source="test",
        fetcher=fetcher,
        serialize=lambda d: d,
    )
    second, hit2 = await cache.get_or_fetch(
        feature="macro_snapshot",
        symbol="SPY",
        source="test",
        fetcher=fetcher,
        serialize=lambda d: d,
    )
    assert hit1 is False
    assert hit2 is True
    assert calls == 1
    assert first == {"value": "macro"}
    assert isinstance(second, CachedContextEntry)


@pytest.mark.asyncio
async def test_same_window_reuse_across_symbols_bucket() -> None:
    memory = HierarchicalCache()
    cache = LLMContextCache(memory_cache=memory)
    fixed = datetime(2026, 6, 16, 10, 4, 0, tzinfo=UTC)
    key_spy = build_context_cache_key("macro_snapshot", "SPY", now=fixed)
    key_qqq = build_context_cache_key("macro_snapshot", "QQQ", now=fixed)
    assert key_spy != key_qqq
    entry = CachedContextEntry(
        payload={"x": 1},
        source="test",
        created_at=fixed,
        cost_saved=Decimal("0.01"),
    )
    await cache.set_entry(key_spy, entry)
    got = await cache.get_entry(key_spy)
    assert got is not None
    assert got.payload["x"] == 1


def test_key_bucket_boundary() -> None:
    t0 = datetime(2026, 6, 16, 10, 4, 0, tzinfo=UTC)
    t1 = datetime(2026, 6, 16, 10, 4, 59, tzinfo=UTC)
    k0 = build_context_cache_key("f", "SPY", now=t0, bucket_seconds=300)
    k1 = build_context_cache_key("f", "SPY", now=t1, bucket_seconds=300)
    assert k0 == k1
    t_next = datetime(2026, 6, 16, 10, 9, 0, tzinfo=UTC)
    k_next = build_context_cache_key("f", "SPY", now=t_next, bucket_seconds=300)
    assert k0 != k_next


@pytest.mark.asyncio
async def test_redis_unavailable_degrades_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.services.ai_core.llm_context_cache.redis_url_configured",
        lambda: "redis://invalid:6379/0",
    )
    memory = HierarchicalCache()
    cache = LLMContextCache(memory_cache=memory)
    calls = 0

    async def fetcher() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    _, hit1 = await cache.get_or_fetch(
        feature="macro_snapshot",
        symbol="SPY",
        source="test",
        fetcher=fetcher,
        serialize=lambda s: {"s": s},
    )
    _, hit2 = await cache.get_or_fetch(
        feature="macro_snapshot",
        symbol="SPY",
        source="test",
        fetcher=fetcher,
        serialize=lambda s: {"s": s},
    )
    assert hit1 is False
    assert hit2 is True
    assert calls == 1


@pytest.mark.asyncio
async def test_concurrent_fetch_coalesces() -> None:
    memory = HierarchicalCache()
    cache = LLMContextCache(memory_cache=memory)
    calls = 0

    async def slow_fetcher() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "data"

    results = await asyncio.gather(
        cache.get_or_fetch(
            feature="macro_snapshot",
            symbol="SPY",
            source="test",
            fetcher=slow_fetcher,
            serialize=lambda s: {"s": s},
        ),
        cache.get_or_fetch(
            feature="macro_snapshot",
            symbol="SPY",
            source="test",
            fetcher=slow_fetcher,
            serialize=lambda s: {"s": s},
        ),
    )
    assert calls == 1
    assert results[0][1] is False or results[1][1] is False
