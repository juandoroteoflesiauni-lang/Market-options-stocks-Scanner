"""
Multi-level cache implementation with L1 (in-memory) + L2 (Redis) tiers.

Architecture:
    L1 Cache: cachetools.TTLCache (1000 entries, 5 min TTL)
    - Ultra-fast access (<1μs)
    - Process-local
    - Evicted on process restart

    L2 Cache: Redis (shared across instances)
    - Fast access (~1ms)
    - Persistent across restarts
    - Shared across worker processes

Data Flow:
    GET: L1 → L2 → Backend (with promotion to L1)
    SET: L1 + L2 (parallel write)
    DELETE: L1 + L2 (parallel delete)

Performance:
    - L1 hit: 0.001ms
    - L2 hit: 1ms
    - Backend: 200-800ms
    - Cache hit rate target: 85%+
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# Try to import Redis (optional dependency)
try:
    import redis.asyncio as redis

    REDIS_AVAILABLE = True
except ImportError:
    redis = None  # type: ignore
    REDIS_AVAILABLE = False
    logger.warning("redis not installed - L2 cache disabled. Install: pip install redis")


class CacheMetrics:
    """Cache hit/miss metrics for monitoring."""

    def __init__(self):
        self.hits: int = 0
        self.misses: int = 0
        self.errors: int = 0
        self._start_time: float = time.time()

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total

    @property
    def uptime(self) -> float:
        return time.time() - self._start_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "hit_rate": round(self.hit_rate, 4),
            "uptime_secs": round(self.uptime, 2),
        }


class MultiLevelCache:
    """
    Multi-level cache with L1 (in-memory) and L2 (Redis) tiers.

    Parameters
    ----------
    l1_maxsize : int
        Maximum entries in L1 cache (default: 1000)
    l1_ttl : int
        L1 cache TTL in seconds (default: 300 = 5 min)
    redis_url : str, optional
        Redis connection URL (default: "redis://localhost:6379")
    redis_db : int
        Redis database number (default: 0)
    key_prefix : str
        Prefix for all cache keys (default: "quantum:")

    Examples
    --------
    >>> cache = MultiLevelCache()
    >>> await cache.set("key", {"data": "value"}, ttl=3600)
    >>> result = await cache.get("key")
    >>> await cache.delete("key")
    """

    def __init__(
        self,
        l1_maxsize: int = 1000,
        l1_ttl: int = 300,
        redis_url: str = "redis://localhost:6379",
        redis_db: int = 0,
        key_prefix: str = "quantum:fund:",
    ):
        self._l1: TTLCache = TTLCache(maxsize=l1_maxsize, ttl=l1_ttl)
        self._l1_ttl = l1_ttl
        self._redis_url = redis_url
        self._redis_db = redis_db
        self._key_prefix = key_prefix
        self._redis: redis.Redis | None = None  # type: ignore
        self._metrics = CacheMetrics()
        self._connected: bool = False

    async def connect(self) -> None:
        """Initialize Redis connection if available."""
        if not REDIS_AVAILABLE:
            logger.info("Redis not available - L2 cache disabled")
            return

        try:
            self._redis = redis.Redis(
                host=(
                    self._redis_url.split("://")[1].split("/")[0].split(":")[0]
                    if "://" in self._redis_url
                    else "localhost"
                ),
                db=self._redis_db,
                decode_responses=False,  # We handle encoding/decoding
            )
            await self._redis.ping()
            self._connected = True
            logger.info("L2 cache (Redis) connected successfully")
        except Exception as exc:
            logger.warning(f"Redis connection failed: {exc}. L2 cache disabled.")
            self._connected = False

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._redis and self._connected:
            await self._redis.close()
            self._connected = False

    def _make_key(self, key: str) -> str:
        """Create prefixed cache key."""
        return f"{self._key_prefix}{key}"

    def _serialize(self, value: Any) -> bytes:
        """Serialize value to bytes."""
        return json.dumps(value, default=str).encode("utf-8")

    def _deserialize(self, data: bytes) -> Any:
        """Deserialize value from bytes."""
        return json.loads(data.decode("utf-8"))

    async def get(self, key: str) -> Any | None:
        """
        Get value from cache (L1 → L2 → None).

        Parameters
        ----------
        key : str
            Cache key

        Returns
        -------
        Optional[Any]
            Cached value or None if not found
        """
        full_key = self._make_key(key)

        # Try L1 first
        if full_key in self._l1:
            self._metrics.hits += 1
            logger.debug(f"L1 cache HIT: {key}")
            return self._l1[full_key]

        # Try L2 (Redis)
        if self._connected and self._redis:
            try:
                data = await self._redis.get(full_key)
                if data:
                    value = self._deserialize(data)
                    self._l1[full_key] = value  # Promote to L1
                    self._metrics.hits += 1
                    logger.debug(f"L2 cache HIT: {key}")
                    return value
            except Exception as exc:
                self._metrics.errors += 1
                logger.warning(f"L2 cache error: {exc}")

        self._metrics.misses += 1
        logger.debug(f"Cache MISS: {key}")
        return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
    ) -> None:
        """
        Set value in cache (L1 + L2 parallel write).

        Parameters
        ----------
        key : str
            Cache key
        value : Any
            Value to cache
        ttl : int, optional
            TTL in seconds (default: None = use L1 TTL)
        """
        full_key = self._make_key(key)
        ttl = ttl or self._l1_ttl

        # Always write to L1
        self._l1[full_key] = value

        # Write to L2 (Redis) if connected
        if self._connected and self._redis:
            try:
                await self._redis.setex(full_key, ttl, self._serialize(value))
            except Exception as exc:
                self._metrics.errors += 1
                logger.warning(f"L2 cache set error: {exc}")

    async def delete(self, key: str) -> None:
        """
        Delete value from cache (L1 + L2).

        Parameters
        ----------
        key : str
            Cache key
        """
        full_key = self._make_key(key)

        # Delete from L1
        self._l1.pop(full_key, None)

        # Delete from L2
        if self._connected and self._redis:
            try:
                await self._redis.delete(full_key)
            except Exception as exc:
                self._metrics.errors += 1
                logger.warning(f"L2 cache delete error: {exc}")

    async def clear(self) -> None:
        """Clear all cache entries."""
        self._l1.clear()

        if self._connected and self._redis:
            try:
                # Delete all keys with our prefix
                pattern = f"{self._key_prefix}*"
                cursor = 0
                while True:
                    cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
                    if keys:
                        await self._redis.delete(*keys)
                    if cursor == 0:
                        break
            except Exception as exc:
                self._metrics.errors += 1
                logger.warning(f"L2 cache clear error: {exc}")

    def get_metrics(self) -> dict[str, Any]:
        """Get cache metrics."""
        return self._metrics.to_dict()

    async def health_check(self) -> dict[str, Any]:
        """
        Perform health check on all cache tiers.

        Returns
        -------
        dict[str, Any]
            Health status for each tier
        """
        health = {
            "l1": {
                "status": "healthy",
                "size": len(self._l1),
                "maxsize": self._l1.maxsize,
            },
            "l2": {
                "status": "disabled",
                "connected": False,
            },
            "metrics": self.get_metrics(),
        }

        if self._connected and self._redis:
            try:
                await self._redis.ping()
                health["l2"] = {
                    "status": "healthy",
                    "connected": True,
                }
            except Exception as exc:
                health["l2"] = {
                    "status": "unhealthy",
                    "connected": False,
                    "error": str(exc),
                }

        return health


# Global cache instance (singleton pattern)
_cache: MultiLevelCache | None = None


def get_cache() -> MultiLevelCache:
    """Get or create global cache instance."""
    global _cache
    if _cache is None:
        _cache = MultiLevelCache()
    return _cache


async def init_cache(
    redis_url: str = "redis://localhost:6379",
    l1_maxsize: int = 1000,
) -> MultiLevelCache:
    """Initialize global cache with Redis connection."""
    global _cache
    _cache = MultiLevelCache(redis_url=redis_url, l1_maxsize=l1_maxsize)
    await _cache.connect()
    return _cache


__all__ = [
    "CacheMetrics",
    "MultiLevelCache",
    "get_cache",
    "init_cache",
]
