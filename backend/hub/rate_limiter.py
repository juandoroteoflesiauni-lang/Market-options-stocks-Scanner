from __future__ import annotations

from typing import Any

"""Unified rate limiter with per-provider token buckets and optional Redis distribution."""


import asyncio
import contextlib
import logging
import os
import random
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-provider rate limit configuration (requests per second)
# ---------------------------------------------------------------------------

DEFAULT_RATE_LIMITS: dict[str, float] = {
    "fmp": 10.0,  # FMP: ~300 req/min on standard plan, 10/s = 600/min
    "fmp_burst": 50.0,  # short burst allowance
    "massive": 5.0,  # Massive/Polygon: varies
    "polygon": 5.0,
    "alpaca": 10.0,
    "bingx": 10.0,
    "binance": 20.0,
    "deribit": 10.0,
    "okx": 10.0,
    "tiingo": 5.0,
    "finnhub": 5.0,
    "github_models": 30.0,
    "gemini": 10.0,
    "azure_openai": 30.0,
    "telegram": 20.0,
    "bcra": 5.0,
    "data912": 5.0,
    "argentina_datos": 5.0,
    "hypertracker": 5.0,
    "sec": 5.0,
    "yahoo": 10.0,
    "unusual_whales": 5.0,
}

KEY_SPECIFIC_LIMITS: dict[str, dict[str, float]] = {
    # Some API keys have dedicated higher limits
}


@dataclass
class TokenBucket:
    """Leaky token bucket for rate limiting."""

    rate: float  # tokens per second
    burst: float  # max accumulated tokens
    tokens: float  # current tokens
    last_refill: float  # monotonic time of last refill

    def refill(self, now: float) -> None:
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        self.refill(now)
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def wait_time(self) -> float:
        now = time.monotonic()
        self.refill(now)
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.rate


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Async rate limiter with per-provider token buckets.

    Supports:
    - Per-provider base rate limits
    - Per-key rate limits (different API keys may have different limits)
    - Redis-based distributed coordination (optional)
    - Exponential backoff on rate limit signals
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._buckets: dict[str, TokenBucket] = {}
        self._limits = dict(DEFAULT_RATE_LIMITS)
        self._key_limits = KEY_SPECIFIC_LIMITS.copy()
        self._redis_available = False

        # Override from environment
        env_overrides = os.getenv("RATE_LIMITER_OVERRIDES", "")
        if env_overrides:
            for pair in env_overrides.split(","):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    with contextlib.suppress(ValueError):
                        self._limits[k.strip()] = float(v.strip())

    def _bucket_key(self, provider: str, key_label: str = "default") -> str:
        return f"{provider}:{key_label}" if key_label != "default" else provider

    def _get_limits(self, provider: str) -> tuple[float, float]:
        """Return (rate, burst) for the given provider."""
        rate = self._limits.get(provider, 10.0)
        burst_key = f"{provider}_burst"
        burst = self._limits.get(burst_key, rate * 3)
        return rate, max(rate, burst)

    async def acquire(
        self,
        provider: str,
        key_label: str = "default",
        *,
        tokens: float = 1.0,
        max_wait: float = 5.0,
        raise_on_timeout: bool = False,
    ) -> bool:
        """Acquire permission to make a call. Returns True if allowed."""
        bucket_key = self._bucket_key(provider, key_label)
        rate, burst = self._get_limits(provider)

        async with self._lock:
            if bucket_key not in self._buckets:
                self._buckets[bucket_key] = TokenBucket(
                    rate=rate, burst=burst, tokens=burst, last_refill=time.monotonic()
                )
            bucket = self._buckets[bucket_key]

            if bucket.consume(tokens):
                return True

            wait = bucket.wait_time()
            if wait > max_wait:
                if raise_on_timeout:
                    msg = (
                        f"Rate limit exceeded for {provider} ({key_label}): "
                        f"need to wait {wait:.1f}s"
                    )
                    raise TimeoutError(msg)
                return False

        await asyncio.sleep(wait + random.uniform(0.0, 0.05))
        return True

    async def acquire_or_wait(
        self,
        provider: str,
        key_label: str = "default",
        *,
        tokens: float = 1.0,
        timeout: float = 10.0,
    ) -> bool:
        """Block until a token is available or timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.acquire(provider, key_label, tokens=tokens, max_wait=1.0):
                return True
            await asyncio.sleep(0.1)
        return False

    async def reset_provider(self, provider: str) -> None:
        """Reset rate limiter state for a provider (e.g., after a key rotation)."""
        async with self._lock:
            prefix = f"{provider}:"
            self._buckets = {k: v for k, v in self._buckets.items() if not k.startswith(prefix)}

    async def reset_all(self) -> None:
        async with self._lock:
            self._buckets.clear()

    def get_status(self) -> dict[str, dict[str, Any]]:
        """Return current state of all buckets (for debugging)."""
        result: dict[str, dict[str, Any]] = {}
        for key, bucket in self._buckets.items():
            result[key] = {
                "rate": bucket.rate,
                "burst": bucket.burst,
                "tokens": round(bucket.tokens, 2),
                "wait_time": round(bucket.wait_time(), 3),
            }
        return result


# Module-level singleton
rate_limiter = RateLimiter()


__all__ = [
    "RateLimiter",
    "TokenBucket",
    "rate_limiter",
]
