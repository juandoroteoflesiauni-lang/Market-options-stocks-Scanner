"""Shared async ingestion primitives for market-data fetchers."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from backend.hub.api_consumption_monitor import ApiCallStatus, api_consumption_monitor
from backend.hub.rate_limiter import rate_limiter
from backend.observability import (
    provider_operation_from_url,
    record_cache_lookup,
    record_consumption_metric,
    record_provider_request,
    span,
)

logger = logging.getLogger("backend.layer_1_data.fetchers.async_market_core")


class AsyncTTLCache:
    """Small async-safe TTL cache with stale fallback support."""

    def __init__(self, maxsize: int = 4096) -> None:
        self._maxsize = maxsize
        self._items: dict[str, tuple[float, object]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> object | None:
        now = time.monotonic()
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at >= now:
                return value
            return None

    async def get_stale(self, key: str) -> object | None:
        async with self._lock:
            item = self._items.get(key)
            return item[1] if item else None

    async def set(self, key: str, value: object, ttl_secs: float) -> None:
        async with self._lock:
            if len(self._items) >= self._maxsize:
                oldest_key = min(self._items, key=lambda k: self._items[k][0])
                self._items.pop(oldest_key, None)
            self._items[key] = (time.monotonic() + ttl_secs, value)

    async def delete_prefix(self, prefix: str) -> None:
        async with self._lock:
            for key in [k for k in self._items if k.startswith(prefix)]:
                self._items.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_after_secs: float = 60.0
    failures: int = 0
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.failures < self.failure_threshold:
            return True
        return (time.monotonic() - self.opened_at) >= self.reset_after_secs

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


_client_lock = asyncio.Lock()
_clients: dict[str, httpx.AsyncClient] = {}
_inflight_lock = asyncio.Lock()
_inflight: dict[str, asyncio.Task[object | None]] = {}


async def get_shared_http_client(
    name: str,
    *,
    timeout: float,
    max_connections: int = 80,
    max_keepalive_connections: int = 30,
) -> httpx.AsyncClient:
    async with _client_lock:
        client = _clients.get(name)
        if client and not client.is_closed:
            return client
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
            ),
            headers={"User-Agent": "QuantumAnalyzer/4.0"},
        )
        _clients[name] = client
        return client


async def close_shared_http_clients() -> None:
    async with _client_lock:
        clients = list(_clients.values())
        _clients.clear()
    await asyncio.gather(
        *(client.aclose() for client in clients if not client.is_closed), return_exceptions=True
    )


async def fetch_json_singleflight(
    *,
    client_name: str,
    url: str,
    params: dict[str, Any],
    cache: AsyncTTLCache,
    cache_key: str,
    ttl_secs: float,
    circuit: CircuitBreaker,
    timeout: float,
    max_retries: int = 3,
    stale_on_error: bool = True,
) -> object | None:
    started = time.perf_counter()
    operation = provider_operation_from_url(url)
    cached = await cache.get(cache_key)
    if cached is not None:
        await api_consumption_monitor.record(
            provider=client_name,
            endpoint=operation,
            status=ApiCallStatus.CACHE_HIT,
            duration_seconds=time.perf_counter() - started,
            cache_hit=True,
        )
        record_cache_lookup("async_ttl", client_name, True)
        record_provider_request(
            client_name,
            operation,
            time.perf_counter() - started,
            status="cache_hit",
            cache_hit=True,
        )
        return cached
    record_cache_lookup("async_ttl", client_name, False)

    async with _inflight_lock:
        task = _inflight.get(cache_key)
        if task is None:
            task = asyncio.create_task(
                _fetch_uncached(
                    client_name=client_name,
                    url=url,
                    params=params,
                    cache=cache,
                    cache_key=cache_key,
                    ttl_secs=ttl_secs,
                    circuit=circuit,
                    timeout=timeout,
                    max_retries=max_retries,
                    stale_on_error=stale_on_error,
                )
            )
            _inflight[cache_key] = task

    try:
        return await task
    finally:
        async with _inflight_lock:
            if _inflight.get(cache_key) is task:
                _inflight.pop(cache_key, None)


async def _fetch_uncached(
    *,
    client_name: str,
    url: str,
    params: dict[str, Any],
    cache: AsyncTTLCache,
    cache_key: str,
    ttl_secs: float,
    circuit: CircuitBreaker,
    timeout: float,
    max_retries: int,
    stale_on_error: bool,
) -> object | None:
    operation = provider_operation_from_url(url)
    started = time.perf_counter()
    provider_status = "error"
    retry_count = 0
    endpoint_key = operation or url
    status_enum = ApiCallStatus.SUCCESS

    async def _stale_or_none() -> object | None:
        return await cache.get_stale(cache_key) if stale_on_error else None

    if not circuit.allow():
        logger.warning("Circuit open for %s", client_name)
        provider_status = "circuit_open"
        status_enum = ApiCallStatus.CIRCUIT_OPEN
        await api_consumption_monitor.record(
            provider=client_name,
            endpoint=endpoint_key,
            status=status_enum,
            duration_seconds=time.perf_counter() - started,
        )
        try:
            return await _stale_or_none()
        finally:
            record_provider_request(
                client_name,
                operation,
                time.perf_counter() - started,
                status=provider_status,
                cache_hit=False,
            )

    await rate_limiter.acquire(client_name)
    client = await get_shared_http_client(client_name, timeout=timeout)
    backoff = 0.5

    with span(
        f"provider.{client_name}",
        {
            "provider": client_name,
            "operation": operation,
            "cache_hit": False,
        },
    ):
        try:
            for attempt in range(1, max_retries + 1):
                try:
                    resp = await client.get(url, params=params)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    provider_status = exc.__class__.__name__
                    is_timeout = isinstance(exc, httpx.TimeoutException)
                    status_enum = ApiCallStatus.TIMEOUT if is_timeout else ApiCallStatus.ERROR
                    circuit.record_failure()
                    logger.warning(
                        "%s transport error attempt=%s/%s: %s",
                        client_name,
                        attempt,
                        max_retries,
                        exc,
                    )
                    await asyncio.sleep(backoff + random.uniform(0.0, 0.25))
                    backoff = min(backoff * 2.0, 8.0)
                    retry_count += 1
                    continue

                provider_status = str(resp.status_code)
                if resp.status_code == 200:
                    data = resp.json()
                    await cache.set(cache_key, data, ttl_secs)
                    circuit.record_success()
                    content_length = int(resp.headers.get("content-length", 0))
                    await api_consumption_monitor.record(
                        provider=client_name,
                        endpoint=endpoint_key,
                        status=ApiCallStatus.SUCCESS,
                        duration_seconds=time.perf_counter() - started,
                        bytes_received=content_length,
                        retry_count=retry_count,
                    )
                    record_consumption_metric(
                        provider=client_name,
                        endpoint=endpoint_key,
                        status="success",
                    )
                    return data

                if resp.status_code == 429:
                    status_enum = ApiCallStatus.RATE_LIMITED
                    circuit.record_failure()
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff
                    delay += random.uniform(0.0, 0.25)
                    logger.warning(
                        "%s rate-limited status=%s attempt=%s/%s delay=%.2fs url=%s",
                        client_name,
                        resp.status_code,
                        attempt,
                        max_retries,
                        delay,
                        url,
                    )
                    await api_consumption_monitor.record(
                        provider=client_name,
                        endpoint=endpoint_key,
                        status=ApiCallStatus.RATE_LIMITED,
                        duration_seconds=time.perf_counter() - started,
                        retry_count=retry_count + 1,
                    )
                    record_consumption_metric(
                        provider=client_name, endpoint=endpoint_key, rate_limited=True
                    )
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2.0, 8.0)
                    retry_count += 1
                    continue

                if resp.status_code in {500, 502, 503, 504}:
                    circuit.record_failure()
                    delay = backoff + random.uniform(0.0, 0.25)
                    logger.warning(
                        "%s retryable status=%s attempt=%s/%s delay=%.2fs url=%s",
                        client_name,
                        resp.status_code,
                        attempt,
                        max_retries,
                        delay,
                        url,
                    )
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2.0, 8.0)
                    retry_count += 1
                    continue

                circuit.record_failure()
                logger.warning(
                    "%s non-retryable status=%s url=%s",
                    client_name,
                    resp.status_code,
                    url,
                )
                await api_consumption_monitor.record(
                    provider=client_name,
                    endpoint=endpoint_key,
                    status=ApiCallStatus.ERROR,
                    duration_seconds=time.perf_counter() - started,
                    retry_count=retry_count,
                    error_message=f"HTTP {resp.status_code}",
                )
                return await _stale_or_none()

            provider_status = "exhausted"
            await api_consumption_monitor.record(
                provider=client_name,
                endpoint=endpoint_key,
                status=ApiCallStatus.ERROR,
                duration_seconds=time.perf_counter() - started,
                retry_count=retry_count,
                error_message="Max retries exhausted",
            )
            return await _stale_or_none()
        finally:
            record_provider_request(
                client_name,
                operation,
                time.perf_counter() - started,
                status=provider_status,
                cache_hit=False,
            )
