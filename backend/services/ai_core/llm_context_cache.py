"""Multi-level LLM/macro context cache with graceful Redis degrade. # [TH][PD-2]"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeVar

from backend.config.cache_thresholds import DEFAULT_CACHE_THRESHOLDS, CacheThresholds
from backend.domain.agentic_models import CachedContextEntry
from backend.services.cache_manager import HierarchicalCache, cache_manager
from backend.services.scanner_cache_redis import redis_url_configured

logger = logging.getLogger(__name__)

T = TypeVar("T")


def build_context_cache_key(
    feature: str,
    symbol: str,
    *,
    now: datetime | None = None,
    bucket_seconds: int | None = None,
) -> str:
    """Build time-bucketed cache key: feature + symbol + 5-min floor."""
    thresholds = (
        DEFAULT_CACHE_THRESHOLDS
        if bucket_seconds is None
        else CacheThresholds(llm_context_bucket_seconds=bucket_seconds)
    )
    moment = now or datetime.now(tz=UTC)
    bucket = int(moment.timestamp()) // thresholds.llm_context_bucket_seconds
    return f"llmctx:{feature}:{symbol.upper()}:{bucket}"


def _redis_context_key(cache_key: str) -> str:
    return f"qa:llmctx:{cache_key}"


class LLMContextCache:
    """Redis (opt) → in-memory HierarchicalCache; never raises on backend failure."""

    def __init__(
        self,
        *,
        memory_cache: HierarchicalCache | None = None,
        thresholds: CacheThresholds | None = None,
    ) -> None:
        self._memory = memory_cache or cache_manager
        self._thresholds = thresholds or DEFAULT_CACHE_THRESHOLDS
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._memory.configure_pattern(
            "llmctx:*", {"ttl": self._thresholds.llm_context_ttl_seconds}
        )

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    def _redis_get(self, cache_key: str) -> CachedContextEntry | None:
        url = redis_url_configured()
        if not url:
            return None
        try:
            import redis

            client = redis.from_url(url, decode_responses=True)
            raw = client.get(_redis_context_key(cache_key))
            if not raw:
                return None
            data = json.loads(raw)
            return CachedContextEntry.model_validate(data)
        except Exception as exc:
            logger.debug("llm_context_cache.redis_get_failed key=%s err=%s", cache_key, exc)
            return None

    def _redis_set(self, cache_key: str, entry: CachedContextEntry) -> None:
        url = redis_url_configured()
        if not url:
            return
        try:
            import redis

            client = redis.from_url(url, decode_responses=True)
            client.setex(
                _redis_context_key(cache_key),
                self._thresholds.llm_context_redis_ttl_seconds,
                entry.model_dump_json(),
            )
        except Exception as exc:
            logger.debug("llm_context_cache.redis_set_failed key=%s err=%s", cache_key, exc)

    async def get_entry(self, cache_key: str) -> CachedContextEntry | None:
        """Read from Redis then memory; swallow backend errors."""
        redis_entry = self._redis_get(cache_key)
        if redis_entry is not None:
            return redis_entry
        mem_val = await self._memory.get(cache_key)
        if mem_val is None:
            return None
        if isinstance(mem_val, CachedContextEntry):
            return mem_val
        try:
            return CachedContextEntry.model_validate(mem_val)
        except Exception:
            return None

    async def set_entry(self, cache_key: str, entry: CachedContextEntry) -> None:
        """Write to memory and best-effort Redis."""
        await self._memory.set(cache_key, entry, ttl=self._thresholds.llm_context_ttl_seconds)
        self._redis_set(cache_key, entry)

    async def get_or_fetch(
        self,
        *,
        feature: str,
        symbol: str,
        source: str,
        fetcher: Callable[[], Awaitable[T]],
        serialize: Callable[[T], dict[str, object]],
        estimated_cost_usd: Decimal = Decimal("0"),
    ) -> tuple[T | CachedContextEntry, bool]:
        """Return (value, cache_hit). Coalesces concurrent fetches per key."""
        cache_key = build_context_cache_key(
            feature,
            symbol,
            bucket_seconds=self._thresholds.llm_context_bucket_seconds,
        )
        cached = await self.get_entry(cache_key)
        if cached is not None:
            await self._record_cache_hit(feature, symbol, cached.cost_saved)
            return cached, True

        lock = await self._lock_for(cache_key)
        async with lock:
            cached = await self.get_entry(cache_key)
            if cached is not None:
                await self._record_cache_hit(feature, symbol, cached.cost_saved)
                return cached, True

            raw = await fetcher()
            entry = CachedContextEntry(
                payload=serialize(raw),
                source=source,
                created_at=datetime.now(tz=UTC),
                cost_saved=estimated_cost_usd,
            )
            await self.set_entry(cache_key, entry)
            return raw, False

    @staticmethod
    async def _record_cache_hit(feature: str, symbol: str, cost_saved: Decimal) -> None:
        try:
            from backend.audit.hooks import audit_api_call

            await audit_api_call(
                module="llm_context_cache",
                provider="cache",
                endpoint=f"/llmctx/{feature}",
                status="success",
                cache_hit=True,
                estimated_cost=float(cost_saved),
                request_context={"symbol": symbol, "feature": feature},
            )
        except Exception:
            pass


_default_cache: LLMContextCache | None = None


def get_llm_context_cache() -> LLMContextCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = LLMContextCache()
    return _default_cache


__all__ = ["LLMContextCache", "build_context_cache_key", "get_llm_context_cache"]
