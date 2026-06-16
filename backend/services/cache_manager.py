from typing import Any
"""
Gestor de Caché Simple con Métricas
"""

import threading
from datetime import datetime


class CacheMetrics:
    def __init__(self, cache_name: str = "default"):
        self.cache_name = cache_name
        self.hits = 0
        self.misses = 0
        self.sets = 0
        self.deletes = 0
        self.expirations = 0
        self.created_at = datetime.now()
        self.last_access: datetime | None = None

    def hit(self):
        self.hits += 1
        self.last_access = datetime.now()

    def miss(self):
        self.misses += 1
        self.last_access = datetime.now()

    def set(self):
        self.sets += 1

    def delete(self):
        self.deletes += 1

    def expire(self):
        self.expirations += 1

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return (self.hits / total) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_name": self.cache_name,
            "hits": self.hits,
            "misses": self.misses,
            "total_requests": self.hits + self.misses,
            "hit_rate": round(self.hit_rate, 2),
            "efficiency": (
                "excellent"
                if self.hit_rate >= 90
                else (
                    "good" if self.hit_rate >= 75 else "moderate" if self.hit_rate >= 50 else "poor"
                )
            ),
            "sets": self.sets,
            "deletes": self.deletes,
            "expirations": self.expirations,
            "created_at": self.created_at.isoformat(),
            "last_access": self.last_access.isoformat() if self.last_access else None,
            "uptime_seconds": (datetime.now() - self.created_at).total_seconds(),
        }


class HierarchicalCache:
    DEFAULT_PATTERNS = {
        "prices:*": {"ttl": 5},
        "merval:*": {"ttl": 10},
        "arbitrage:*": {"ttl": 15},
        "forex:*": {"ttl": 30},
        "summary:*": {"ttl": 30},
        "fundamentals:*": {"ttl": 300},
        "metadata:*": {"ttl": 3600},
    }

    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._timestamps: dict[str, datetime] = {}
        self._ttls: dict[str, int] = {}
        self._patterns: dict[str, dict] = dict(self.DEFAULT_PATTERNS)
        self._lock = threading.RLock()
        self._metrics = CacheMetrics("hierarchical")

    def configure_pattern(self, pattern: str, ttl: int):
        self._patterns[pattern] = {"ttl": ttl}

    def _match_pattern(self, key: str, pattern: str) -> bool:
        if pattern.endswith("*"):
            return key.startswith(pattern[:-1])
        return key == pattern

    def _get_config_for_key(self, key: str) -> dict[str, Any]:
        for pattern, config in self._patterns.items():
            if self._match_pattern(key, pattern):
                return config
        return {"ttl": 60}

    def _is_expired(self, key: str, ttl: int) -> bool:
        if key not in self._timestamps:
            return True
        age = (datetime.now() - self._timestamps[key]).total_seconds()
        return age > ttl

    async def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._cache:
                self._metrics.miss()
                return None

            config = self._get_config_for_key(key)
            ttl = self._ttls.get(key, config.get("ttl", 60))

            if self._is_expired(key, ttl):
                await self.delete(key)
                self._metrics.expire()
                return None

            self._metrics.hit()
            return self._cache[key]

    async def set(self, key: str, value: Any, ttl: int | None = None):
        with self._lock:
            config = self._get_config_for_key(key)
            effective_ttl = ttl or config.get("ttl", 60)

            self._cache[key] = value
            self._timestamps[key] = datetime.now()
            self._ttls[key] = effective_ttl
            self._metrics.set()

    async def delete(self, key: str):
        with self._lock:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
            self._ttls.pop(key, None)
            self._metrics.delete()

    def get_metrics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "metrics": self._metrics.to_dict(),
                "size": len(self._cache),
                "keys": list(self._cache.keys())[:20],
            }


cache_manager = HierarchicalCache()


# ════════════════════════════════════════════════════════════════════════════════
# CacheManager — TTL-aware cache with Redis/in-memory backends
# ════════════════════════════════════════════════════════════════════════════════
#
# Replaces the uniform 24 h TTL default with per-data-type TTLs reflecting how
# fast each signal decays. Sync API (HTTP routes call this synchronously inside
# their async handlers — Redis I/O is fast enough). Pure-Python in-memory
# fallback when redis-py is unavailable, so dev/test environments still work.
# ════════════════════════════════════════════════════════════════════════════════

import fnmatch  # noqa: E402
import functools  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import pickle  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable  # noqa: E402

try:
    import redis


    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

try:
    import msgpack


    _MSGPACK_AVAILABLE = True
except ImportError:
    _MSGPACK_AVAILABLE = False

_cm_logger = logging.getLogger(__name__)


# Per-endpoint TTLs (seconds). Source of truth for the new cache manager.
# Lower = data decays fast. Higher = expensive AI / slow tape.
ENDPOINT_TTLS: dict[str, int] = {
    "risk_neutral_density": 300,  # 5 min — vol smile drifts fast
    "options_flow_toxicity": 300,  # 5 min
    "dealer_flow": 600,  # 10 min
    "gamma_flip": 600,  # 10 min
    "volatility_skew": 600,  # 10 min
    "zero_day_gamma_wall": 600,  # 10 min — 0DTE critical
    "meta_signal": 900,  # 15 min
    "tail_risk": 900,  # 15 min
    "shadow_delta": 900,  # 15 min
    "zomma": 900,  # 15 min
    "speed_instability": 900,  # 15 min
    "sentiment": 1800,  # 30 min
    "fear_greed": 1800,  # 30 min
    "markov_regime": 1800,  # 30 min
    "cross_asset": 3600,  # 1 h
    "macro_regime_prior": 3600,  # 1 h
    "volume_profile": 3600,  # 1 h
    "price_targets": 86400,  # 24 h — costly AI work
}

_DEFAULT_TTL_S = 300


def _ttl_for_endpoint(endpoint: str | None) -> int:
    """Look up TTL by endpoint name; fall back to default."""
    if not endpoint:
        return _DEFAULT_TTL_S
    return int(ENDPOINT_TTLS.get(endpoint, _DEFAULT_TTL_S))


def _ttl_from_key(key: str) -> int:
    """Infer TTL from cache key shape: '{symbol}:{endpoint}:[hash]'."""
    parts = key.split(":")
    if len(parts) >= 2:
        return _ttl_for_endpoint(parts[1])
    return _DEFAULT_TTL_S


def _hash_params(params: dict | tuple | list | None) -> str:
    """Stable short hash of a parameter set (for cache-key suffixes)."""
    if not params:
        return ""
    payload = json.dumps(params, default=str, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:10]


def _serialize(value):
    """msgpack when available, pickle otherwise. Both binary-safe for Redis."""
    if _MSGPACK_AVAILABLE:
        try:
            return msgpack.packb(value, use_bin_type=True)
        except (TypeError, ValueError):
            return pickle.dumps(value)
    return pickle.dumps(value)


def _deserialize(blob: bytes):
    if blob is None:
        return None
    if _MSGPACK_AVAILABLE:
        try:
            return msgpack.unpackb(blob, raw=False)
        except (msgpack.UnpackException, ValueError, TypeError):
            return pickle.loads(blob)
    return pickle.loads(blob)


# ── Backends ────────────────────────────────────────────────────────────────


class _InMemoryBackend:
    """Threadsafe in-memory backend with hard TTL eviction on read."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        with self._lock:
            self._store[key] = (time.time() + ttl_s, value)

    def delete(self, key: str) -> int:
        with self._lock:
            return 1 if self._store.pop(key, None) is not None else 0

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())

    def flush(self) -> None:
        with self._lock:
            self._store.clear()

    def memory_estimate_bytes(self) -> int:
        with self._lock:
            total = 0
            for k, (_, v) in self._store.items():
                total += sys.getsizeof(k) + sys.getsizeof(v)
            return total


class _RedisBackend:
    """Redis backend (msgpack/pickle serialization, pipelined batches)."""

    def __init__(self, client) -> None:
        self._client = client

    def get(self, key: str) -> Any | None:
        try:
            blob = self._client.get(key)
        except Exception as exc:  # network failure ⇒ treat as miss
            _cm_logger.warning("Redis GET failed: %s", exc)
            return None
        if blob is None:
            return None
        try:
            return _deserialize(blob)
        except Exception as exc:
            _cm_logger.warning("Redis deserialize failed for %s: %s", key, exc)
            return None

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        try:
            self._client.set(key, _serialize(value), ex=int(ttl_s))
        except Exception as exc:
            _cm_logger.warning("Redis SET failed: %s", exc)

    def delete(self, key: str) -> int:
        try:
            return int(self._client.delete(key))
        except Exception as exc:
            _cm_logger.warning("Redis DEL failed: %s", exc)
            return 0

    def keys(self) -> list[str]:
        try:
            return [k.decode() if isinstance(k, bytes) else k for k in self._client.scan_iter("*")]
        except Exception as exc:
            _cm_logger.warning("Redis KEYS failed: %s", exc)
            return []

    def flush(self) -> None:
        try:
            self._client.flushdb()
        except Exception as exc:
            _cm_logger.warning("Redis FLUSHDB failed: %s", exc)

    def memory_estimate_bytes(self) -> int:
        try:
            info = self._client.info("memory")
            return int(info.get("used_memory", 0))
        except Exception:
            return 0


# ── Manager ─────────────────────────────────────────────────────────────────


class CacheManager:
    """
    TTL-aware cache front-end with stat tracking, pattern invalidation, and
    @cached() decorator. Backend auto-selects Redis when available, else
    falls back to a threadsafe in-memory dict.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        force_in_memory: bool = False,
    ) -> None:
        self._hits = 0
        self._misses = 0
        self._lock = threading.RLock()

        url = redis_url or os.getenv("REDIS_URL")
        if force_in_memory or not _REDIS_AVAILABLE or not url:
            self._backend = _InMemoryBackend()
            self._backend_kind = "in_memory"
        else:
            try:
                client = redis.Redis.from_url(url, socket_timeout=1.5)
                client.ping()
                self._backend = _RedisBackend(client)
                self._backend_kind = "redis"
            except Exception as exc:
                _cm_logger.warning("Redis unavailable (%s) — falling back to in-memory.", exc)
                self._backend = _InMemoryBackend()
                self._backend_kind = "in_memory"

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def backend_kind(self) -> str:
        return self._backend_kind

    def get(self, key: str) -> Any | None:
        value = self._backend.get(key)
        with self._lock:
            if value is None:
                self._misses += 1
            else:
                self._hits += 1
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        ttl_s = int(ttl) if ttl is not None else _ttl_from_key(key)
        self._backend.set(key, value, ttl_s)

    def invalidate(self, key: str) -> int:
        return self._backend.delete(key)

    def invalidate_pattern(self, pattern: str) -> int:
        """fnmatch-style pattern, e.g. 'SPY:*' or '*:meta_signal:*'."""
        n = 0
        for k in self._backend.keys():
            if fnmatch.fnmatchcase(k, pattern):
                n += self._backend.delete(k)
        return n

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            hits, misses = self._hits, self._misses
        total = hits + misses
        hit_rate = (hits / total) if total > 0 else 0.0
        keys = self._backend.keys()
        return {
            "backend": self._backend_kind,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hit_rate, 4),
            "n_keys": len(keys),
            "memory_estimate_mb": round(self._backend.memory_estimate_bytes() / (1024**2), 4),
        }

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0

    def flush(self) -> None:
        """Test/admin helper: drop everything."""
        self._backend.flush()
        self.reset_stats()

    # ── Decorator ──────────────────────────────────────────────────────────

    def cached(
        self,
        endpoint_name: str,
        key_arg: str = "symbol",
        ttl: int | None = None,
    ) -> Callable:
        """
        Decorator: cache the wrapped function's return value.

        Cache key shape: '{symbol_value}:{endpoint_name}:{args_hash}'
        TTL: explicit `ttl` arg → ENDPOINT_TTLS[endpoint_name] → default.
        """
        effective_ttl = int(ttl) if ttl is not None else _ttl_for_endpoint(endpoint_name)

        def _decorator(fn: Callable) -> Callable:
            try:
                from inspect import signature

                params = list(signature(fn).parameters.keys())
            except (TypeError, ValueError):
                params = []

            @functools.wraps(fn)
            def _wrap(*args, **kwargs):
                # Resolve symbol from args/kwargs
                sym = kwargs.get(key_arg)
                if sym is None and key_arg in params:
                    idx = params.index(key_arg)
                    if idx < len(args):
                        sym = args[idx]
                sym_part = str(sym).upper().strip() if sym else "_"
                params_hash = _hash_params({"a": list(args[1:]), "k": kwargs})
                key = f"{sym_part}:{endpoint_name}:{params_hash}"

                cached_val = self.get(key)
                if cached_val is not None:
                    return cached_val

                result = fn(*args, **kwargs)
                if result is not None:
                    self.set(key, result, ttl=effective_ttl)
                return result

            return _wrap

        return _decorator


# Module-level singleton (auto Redis or in-memory).
ttl_cache_manager = CacheManager()


def cached(endpoint_name: str, ttl: int | None = None, key_arg: str = "symbol") -> Callable:
    """Module-level decorator alias bound to ttl_cache_manager."""
    return ttl_cache_manager.cached(endpoint_name=endpoint_name, ttl=ttl, key_arg=key_arg)
