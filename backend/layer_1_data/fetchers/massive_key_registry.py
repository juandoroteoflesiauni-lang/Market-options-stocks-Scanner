"""Institutional key broker for Massive/Polygon REST usage.

The registry owns key capability, cooldown, and safe diagnostics. It never exposes raw
API keys and it gives Layer 1 fetchers a deterministic shortlist instead of letting
each caller try every configured credential against every endpoint.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Generic, Literal, Protocol, TypeVar

Capability = Literal[
    "fallback_rest",
    "ohlcv_intraday",
    "ohlcv_daily",
    "options_snapshot",
    "reference",
    "snapshot",
    "financials",
    "distress",
    "macro",
    "indices",
    "altasset",
    "news",
    "hist_trades",
    "ws_quotes",
    "ws_trades",
]

ProviderStatus = Literal[
    "healthy",
    "cooldown_429",
    "unauthorized_401",
    "degraded",
    "disabled",
]

DEFAULT_REST_HOSTS = ("https://api.polygon.io", "https://api.massive.com")

_ROLE_CAPABILITIES: dict[str, tuple[Capability, ...]] = {
    "POLYGON_KEY": ("fallback_rest", "ohlcv_intraday", "ohlcv_daily"),
    "MASSIVE_KEY_FALLBACK": ("fallback_rest", "ohlcv_intraday", "ohlcv_daily"),
    "MASSIVE_KEY_HIST_OHLCV": ("ohlcv_intraday", "ohlcv_daily"),
    "MASSIVE_KEY_SNAPSHOT": ("snapshot", "ohlcv_intraday", "ohlcv_daily"),
    "MASSIVE_KEY_OPTIONS_PRIMARY": ("options_snapshot",),
    "MASSIVE_KEY_OPTIONS_SECONDARY": ("options_snapshot",),
    "MASSIVE_KEY_OPTIONS": ("options_snapshot",),
    "MASSIVE_KEY_FINANCIALS": ("financials",),
    "MASSIVE_KEY_DISTRESS": ("distress",),
    "MASSIVE_KEY_MACRO": ("macro", "reference"),
    "MASSIVE_KEY_INDICES": ("indices", "reference"),
    "MASSIVE_KEY_ALTASSET": ("altasset",),
    "MASSIVE_KEY_NEWS": ("news",),
    "MASSIVE_KEY_REFERENCE": ("reference",),
    "MASSIVE_KEY_HIST_TRADES": ("hist_trades",),
    "MASSIVE_KEY_WS_QUOTES": ("ws_quotes",),
    "MASSIVE_KEY_WS_TRADES": ("ws_trades",),
}

_CAPABILITY_FALLBACKS: dict[Capability, tuple[Capability, ...]] = {
    "ohlcv_intraday": ("ohlcv_intraday", "snapshot", "fallback_rest"),
    "ohlcv_daily": ("ohlcv_daily", "snapshot", "fallback_rest"),
    "snapshot": ("snapshot", "fallback_rest"),
    "reference": ("reference", "fallback_rest"),
    "options_snapshot": ("options_snapshot",),
    "financials": ("financials",),
    "distress": ("distress",),
    "macro": ("macro",),
    "indices": ("indices", "reference", "fallback_rest"),
    "altasset": ("altasset",),
    "news": ("news",),
    "hist_trades": ("hist_trades",),
    "ws_quotes": ("ws_quotes",),
    "ws_trades": ("ws_trades",),
    "fallback_rest": ("fallback_rest",),
}

_LABEL_PRIORITY: dict[str, int] = {
    "MASSIVE_KEY_HIST_OHLCV": 10,
    "MASSIVE_KEY_SNAPSHOT": 20,
    "MASSIVE_KEY_OPTIONS_PRIMARY": 10,
    "MASSIVE_KEY_OPTIONS_SECONDARY": 20,
    "MASSIVE_KEY_OPTIONS": 30,
    "MASSIVE_KEY_REFERENCE": 10,
    "MASSIVE_KEY_FINANCIALS": 10,
    "MASSIVE_KEY_DISTRESS": 10,
    "MASSIVE_KEY_MACRO": 10,
    "MASSIVE_KEY_INDICES": 10,
    "MASSIVE_KEY_ALTASSET": 10,
    "MASSIVE_KEY_NEWS": 10,
    "POLYGON_KEY": 80,
    "MASSIVE_KEY_FALLBACK": 90,
}


@dataclass(frozen=True)
class MassiveKeyBinding:
    label: str
    key: str
    labels: tuple[str, ...]
    capabilities: tuple[Capability, ...]
    hosts: tuple[str, ...] = DEFAULT_REST_HOSTS
    priority: int = 100

    @property
    def masked_key(self) -> str:
        return mask_api_key(self.key)


@dataclass
class MassiveKeyRuntime:
    status: ProviderStatus = "healthy"
    last_status_code: int | None = None
    last_operation: str | None = None
    last_checked_at: float | None = None
    cooldown_until: float = 0.0
    failures: int = 0

    def snapshot(self, now: float) -> dict[str, object]:
        cooldown_remaining = max(0.0, self.cooldown_until - now)
        return {
            "status": self.status,
            "last_status_code": self.last_status_code,
            "last_operation": self.last_operation,
            "last_checked_at": self.last_checked_at,
            "cooldown_remaining_seconds": round(cooldown_remaining, 3),
            "failures": self.failures,
        }


def mask_api_key(value: str) -> str:
    key = str(value or "")
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def build_massive_key_registry(
    settings: MassiveSettingsLike | None = None,
    environ: Mapping[str, str] | None = None,
) -> MassiveKeyRegistry:
    return MassiveKeyRegistry.from_settings(settings=settings, environ=environ)


class ProviderBudgetEngine:
    """Small token-bucket and cooldown guard by key label and operation."""

    def __init__(
        self,
        *,
        capacity: int = 60,
        refill_per_second: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = float(capacity)
        self._refill = float(refill_per_second)
        self._clock = clock
        self._lock = threading.RLock()
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._cooldowns: dict[tuple[str, str], float] = {}

    def allow(self, label: str, operation: str) -> bool:
        key = (label, operation)
        now = self._clock()
        with self._lock:
            cooldown_until = self._cooldowns.get(key, 0.0)
            if cooldown_until > now:
                return False
            tokens, updated_at = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + max(0.0, now - updated_at) * self._refill)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True

    def record_response(
        self,
        label: str,
        operation: str,
        status_code: int,
        retry_after: str | None = None,
    ) -> None:
        if status_code != 429:
            return
        cooldown = _parse_retry_after(retry_after, default=60.0)
        with self._lock:
            self._cooldowns[(label, operation)] = self._clock() + cooldown


T = TypeVar("T")


class MarketDataSingleflightCache(Generic[T]):
    """Thread-safe TTL cache with per-key singleflight and stale-on-error."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        maxsize: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._maxsize = maxsize
        self._clock = clock
        self._lock = threading.RLock()
        self._items: dict[str, tuple[float, T]] = {}
        self._key_locks: dict[str, threading.Lock] = {}

    def get_or_set(
        self,
        key: str,
        producer: Callable[[], T],
        *,
        stale_on_error: bool = True,
    ) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached
        key_lock = self._lock_for_key(key)
        with key_lock:
            cached = self.get(key)
            if cached is not None:
                return cached
            try:
                value = producer()
            except Exception:
                stale = self.get_stale(key) if stale_on_error else None
                if stale is not None:
                    return stale
                raise
            self.set(key, value)
            return value

    def get(self, key: str) -> T | None:
        now = self._clock()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            return value if expires_at >= now else None

    def get_stale(self, key: str) -> T | None:
        with self._lock:
            item = self._items.get(key)
            return item[1] if item is not None else None

    def set(self, key: str, value: T) -> None:
        with self._lock:
            if len(self._items) >= self._maxsize:
                oldest = min(self._items, key=lambda item_key: self._items[item_key][0])
                self._items.pop(oldest, None)
            self._items[key] = (self._clock() + self._ttl, value)

    def _lock_for_key(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock


@dataclass
class MassiveKeyRegistry:
    bindings: list[MassiveKeyBinding]
    budget: ProviderBudgetEngine = field(default_factory=ProviderBudgetEngine)
    _runtime: dict[str, MassiveKeyRuntime] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @classmethod
    def from_settings(
        cls,
        settings: MassiveSettingsLike | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> MassiveKeyRegistry:
        env = environ if environ is not None else os.environ
        pairs: list[tuple[str, str | None]] = _pairs_from_settings(settings, env)
        return cls.from_pairs(pairs)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> MassiveKeyRegistry:
        return cls.from_settings(settings=None, environ=environ)

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, str | None]]) -> MassiveKeyRegistry:
        by_value: dict[str, list[str]] = {}
        for label, raw in pairs:
            value = str(raw or "").strip()
            if not value:
                continue
            by_value.setdefault(value, []).append(label)

        bindings: list[MassiveKeyBinding] = []
        for key, labels in by_value.items():
            primary = _primary_label(labels)
            capabilities = _capabilities_for_labels(labels)
            bindings.append(
                MassiveKeyBinding(
                    label=primary,
                    key=key,
                    labels=tuple(labels),
                    capabilities=capabilities,
                    priority=min(_LABEL_PRIORITY.get(label, 100) for label in labels),
                )
            )
        bindings.sort(key=lambda item: (item.priority, item.label))
        return cls(bindings=bindings)

    def select_keys(
        self, capability: Capability, operation: str | None = None
    ) -> list[MassiveKeyBinding]:
        now = time.monotonic()
        requested = _CAPABILITY_FALLBACKS.get(capability, (capability,))
        out: list[MassiveKeyBinding] = []
        with self._lock:
            for binding in self.bindings:
                runtime = self._runtime.setdefault(binding.label, MassiveKeyRuntime())
                if runtime.status in {"unauthorized_401", "disabled"}:
                    continue
                if runtime.status == "cooldown_429" and runtime.cooldown_until > now:
                    continue
                if runtime.status == "cooldown_429" and runtime.cooldown_until <= now:
                    runtime.status = "healthy"
                if not any(cap in binding.capabilities for cap in requested):
                    continue
                if operation and not self.budget.allow(binding.label, operation):
                    continue
                out.append(binding)
        return out

    def record_response(
        self,
        label: str,
        operation: str,
        status_code: int,
        retry_after: str | None = None,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            runtime = self._runtime.setdefault(label, MassiveKeyRuntime())
            runtime.last_status_code = status_code
            runtime.last_operation = operation
            runtime.last_checked_at = now
            if status_code == 200:
                runtime.status = "healthy"
                runtime.failures = 0
                runtime.cooldown_until = 0.0
            elif status_code == 401:
                runtime.status = "unauthorized_401"
                runtime.failures += 1
            elif status_code == 429:
                runtime.status = "cooldown_429"
                runtime.failures += 1
                runtime.cooldown_until = now + _parse_retry_after(retry_after, default=60.0)
            elif status_code >= 500:
                runtime.status = "degraded"
                runtime.failures += 1
            self.budget.record_response(label, operation, status_code, retry_after)

    def snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        keys: list[dict[str, object]] = []
        with self._lock:
            for binding in self.bindings:
                runtime = self._runtime.setdefault(binding.label, MassiveKeyRuntime())
                keys.append(
                    {
                        "label": binding.label,
                        "labels": list(binding.labels),
                        "masked_key": binding.masked_key,
                        "capabilities": list(binding.capabilities),
                        "hosts": list(binding.hosts),
                        "priority": binding.priority,
                        **runtime.snapshot(now),
                    }
                )
        return {"provider": "massive", "keys": keys}


_GLOBAL_LOCK = threading.Lock()
_GLOBAL_REGISTRY: MassiveKeyRegistry | None = None


def get_massive_key_registry(settings: MassiveSettingsLike | None = None) -> MassiveKeyRegistry:
    global _GLOBAL_REGISTRY
    with _GLOBAL_LOCK:
        if _GLOBAL_REGISTRY is None or settings is not None:
            _GLOBAL_REGISTRY = build_massive_key_registry(settings=settings)
        return _GLOBAL_REGISTRY


def reset_massive_key_registry_for_tests() -> None:
    global _GLOBAL_REGISTRY
    with _GLOBAL_LOCK:
        _GLOBAL_REGISTRY = None


def _pairs_from_settings(
    settings: MassiveSettingsLike | None,
    environ: Mapping[str, str],
) -> list[tuple[str, str | None]]:
    cfg = settings
    if cfg is None and environ is os.environ:
        try:
            from backend.config.settings import load_settings

            cfg = load_settings()
        except Exception:
            cfg = None
    labels = [
        "POLYGON_KEY",
        "MASSIVE_KEY_FALLBACK",
        "MASSIVE_KEY_HIST_OHLCV",
        "MASSIVE_KEY_HIST_TRADES",
        "MASSIVE_KEY_REFERENCE",
        "MASSIVE_KEY_SNAPSHOT",
        "MASSIVE_KEY_OPTIONS_PRIMARY",
        "MASSIVE_KEY_OPTIONS_SECONDARY",
        "MASSIVE_KEY_OPTIONS",
        "MASSIVE_KEY_FINANCIALS",
        "MASSIVE_KEY_DISTRESS",
        "MASSIVE_KEY_MACRO",
        "MASSIVE_KEY_INDICES",
        "MASSIVE_KEY_ALTASSET",
        "MASSIVE_KEY_NEWS",
        "MASSIVE_KEY_WS_QUOTES",
        "MASSIVE_KEY_WS_TRADES",
    ]
    pairs: list[tuple[str, str | None]] = []
    for label in labels:
        value = environ.get(label)
        if value is None and cfg is not None:
            attr = _settings_attr(label)
            value = getattr(cfg, attr, None) if attr else None
        pairs.append((label, value))
    return pairs


def _settings_attr(label: str) -> str | None:
    if label == "POLYGON_KEY":
        return "polygon_key"
    if not label.startswith("MASSIVE_KEY_"):
        return None
    return "massive_key_" + label.removeprefix("MASSIVE_KEY_").lower()


def _primary_label(labels: list[str]) -> str:
    return min(labels, key=lambda label: (_LABEL_PRIORITY.get(label, 100), label))


def _capabilities_for_labels(labels: list[str]) -> tuple[Capability, ...]:
    caps: list[Capability] = []
    for label in labels:
        for cap in _ROLE_CAPABILITIES.get(label, ("fallback_rest",)):
            if cap not in caps:
                caps.append(cap)
    return tuple(caps)


def _parse_retry_after(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(1.0, min(parsed, 900.0))


class MassiveSettingsLike(Protocol):
    """Structural settings contract used without importing pydantic settings at module load."""
