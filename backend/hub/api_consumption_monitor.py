from __future__ import annotations
from typing import Any
"""API Consumption Monitor — real-time tracking, cost estimation, and quota alerts."""


import asyncio
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost configuration — provider pricing per 1k calls (USD)
# ---------------------------------------------------------------------------

FMP_PRICING_TIER: dict[str, float] = {
    "quotes": 0.001,  # /quote, /batch-quote
    "statements": 0.005,  # income/balance/cash-flow
    "fundamentals": 0.005,  # key-metrics, ratios, enterprise-value
    "financials": 0.010,  # full financial growth
    "news": 0.002,
    "profiles": 0.003,
    "technical": 0.004,
    "analyst": 0.004,
    "calendars": 0.003,
    "transcripts": 0.020,
    "insider": 0.005,
    "macro": 0.002,
    "etf": 0.003,
    "filings": 0.010,
    "market": 0.002,
    "default": 0.005,
}

MASSIVE_PRICING_TIER: dict[str, float] = {
    "options_snapshot": 0.050,
    "equity_snapshot": 0.010,
    "historical_bars": 0.010,
    "macro": 0.005,
    "distress": 0.020,
    "default": 0.010,
}

AI_PRICING_TIER: dict[str, float] = {
    "chat_completion": 0.150,  # per 1k tokens output (GPT-4 class)
    "gemini": 0.050,
    "default": 0.100,
}

PROVIDER_PRICING: dict[str, dict[str, float]] = {
    "fmp": FMP_PRICING_TIER,
    "massive": MASSIVE_PRICING_TIER,
    "polygon": {"default": 0.005},
    "alpaca": {"default": 0.002},
    "bingx": {"default": 0.001},
    "binance": {"default": 0.001},
    "deribit": {"default": 0.001},
    "okx": {"default": 0.001},
    "tiingo": {"default": 0.003},
    "finnhub": {"default": 0.002},
    "github_models": AI_PRICING_TIER,
    "gemini": AI_PRICING_TIER,
    "azure_openai": AI_PRICING_TIER,
    "telegram": {"default": 0.0},
    "bcra": {"default": 0.0},
    "data912": {"default": 0.001},
    "argentina_datos": {"default": 0.0},
    "hypertracker": {"default": 0.005},
    "sec": {"default": 0.010},
    "yahoo": {"default": 0.001},
    "default": {"default": 0.005},
}


def _estimate_cost(provider: str, endpoint: str) -> float:
    """Estimate per-call cost in USD by matching endpoint keywords to tiers."""
    tiers = PROVIDER_PRICING.get(provider, PROVIDER_PRICING["default"])
    for key, cost in tiers.items():
        if key == "default":
            continue
        if key in endpoint:
            return cost
    return tiers.get("default", 0.005)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class ApiCallStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    CIRCUIT_OPEN = "circuit_open"
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    REDIRECT = "redirect"


@dataclass(frozen=True)
class ConsumptionRecord:
    provider: str
    endpoint: str
    api_key_label: str
    status: ApiCallStatus
    duration_seconds: float
    timestamp_ns: int
    estimated_cost_usd: float
    cache_hit: bool = False
    bytes_received: int = 0
    retry_count: int = 0
    error_message: str = ""
    module: str = ""


@dataclass
class ProviderStats:
    total_calls: int = 0
    success_calls: int = 0
    error_calls: int = 0
    rate_limited: int = 0
    circuit_open: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    total_duration_seconds: float = 0.0
    total_cost_usd: float = 0.0
    total_bytes: int = 0
    last_call_timestamp: float = 0.0
    calls_per_endpoint: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cost_per_endpoint: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    calls_per_key: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cost_per_key: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    latency_rolling_p50: float = 0.0
    latency_rolling_p99: float = 0.0

    @property
    def error_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.error_calls / self.total_calls * 100.0

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return self.cache_hits / total * 100.0

    @property
    def avg_duration(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.total_duration_seconds / self.total_calls


@dataclass
class ConsumptionReport:
    provider_name: str
    stats: ProviderStats
    period_start: datetime
    period_end: datetime
    projected_monthly_cost: float = 0.0


# ---------------------------------------------------------------------------
# Core monitor
# ---------------------------------------------------------------------------


class ApiConsumptionMonitor:
    """Async-safe consumption tracker for all external API providers.

    Records every API call, aggregates per provider/endpoint/key, estimates
    costs, tracks cache efficiency, and exposes reports for dashboards.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._records: list[ConsumptionRecord] = []
        self._stats: dict[str, ProviderStats] = defaultdict(ProviderStats)
        self._period_start = time.time()
        self._period_start_dt = datetime.now(UTC)
        self._max_records = int(os.getenv("API_CONSUMPTION_MAX_RECORDS", "100000"))

        # Rolling latency per provider
        self._latency_samples: dict[str, list[float]] = defaultdict(list)
        self._latency_max_samples = 1024

    async def record(
        self,
        provider: str,
        endpoint: str,
        api_key_label: str = "default",
        *,
        status: ApiCallStatus = ApiCallStatus.SUCCESS,
        duration_seconds: float = 0.0,
        cache_hit: bool = False,
        bytes_received: int = 0,
        retry_count: int = 0,
        error_message: str = "",
        module: str = "",
    ) -> None:
        """Record a single API call."""
        estimated_cost = _estimate_cost(provider, endpoint)
        record = ConsumptionRecord(
            provider=provider,
            endpoint=endpoint,
            api_key_label=api_key_label,
            status=status,
            duration_seconds=duration_seconds,
            timestamp_ns=time.time_ns(),
            estimated_cost_usd=estimated_cost if not cache_hit else 0.0,
            cache_hit=cache_hit,
            bytes_received=bytes_received,
            retry_count=retry_count,
            error_message=error_message,
            module=module,
        )

        async with self._lock:
            self._stats[provider].total_calls += 1
            self._stats[provider].total_duration_seconds += duration_seconds
            self._stats[provider].total_bytes += bytes_received
            self._stats[provider].total_cost_usd += record.estimated_cost_usd
            self._stats[provider].last_call_timestamp = time.time()
            self._stats[provider].calls_per_endpoint[endpoint] += 1
            self._stats[provider].cost_per_endpoint[endpoint] += record.estimated_cost_usd
            self._stats[provider].calls_per_key[api_key_label] += 1
            self._stats[provider].cost_per_key[api_key_label] += record.estimated_cost_usd

            match status:
                case ApiCallStatus.SUCCESS:
                    self._stats[provider].success_calls += 1
                case ApiCallStatus.ERROR:
                    self._stats[provider].error_calls += 1
                case ApiCallStatus.TIMEOUT:
                    self._stats[provider].error_calls += 1
                case ApiCallStatus.RATE_LIMITED:
                    self._stats[provider].rate_limited += 1
                case ApiCallStatus.CIRCUIT_OPEN:
                    self._stats[provider].circuit_open += 1
                case ApiCallStatus.CACHE_HIT:
                    self._stats[provider].cache_hits += 1
                case ApiCallStatus.CACHE_MISS:
                    self._stats[provider].cache_misses += 1

            self._latency_samples[provider].append(duration_seconds)
            if len(self._latency_samples[provider]) > self._latency_max_samples:
                self._latency_samples[provider].pop(0)
            self._compute_rolling_latency(provider)

            self._records.append(record)
            if len(self._records) > self._max_records:
                self._records.pop(0)

        # Persist to audit_complex store (fire-and-forget)
        try:
            from backend.audit.audit_complex_store import ApiCallAuditEntry
            from backend.audit.structured_logger import get_correlation_id

            audit_entry = ApiCallAuditEntry(
                module=module or "unknown",
                provider=provider,
                endpoint=endpoint,
                status=status.value if isinstance(status, ApiCallStatus) else str(status),
                duration_ms=duration_seconds * 1000.0,
                estimated_cost=estimated_cost if not cache_hit else 0.0,
                api_key_label=api_key_label,
                cache_hit=cache_hit,
                bytes_received=bytes_received,
                retry_count=retry_count,
                error_message=error_message,
                correlation_id=get_correlation_id() or "",
            )
            # Fire-and-forget: do not block the caller
            import asyncio

            asyncio.get_event_loop().create_task(self._persist_audit_api_call(audit_entry))
        except Exception:
            pass

    async def _persist_audit_api_call(self, entry: Any) -> None:
        """Async helper to persist an API call to the audit store."""
        try:
            from backend.audit.audit_complex_store import AuditComplexStore
            from backend.config.settings import load_settings

            settings = load_settings()
            store = AuditComplexStore(db_path=settings.audit_db_path)
            store.persist_api_call(entry)
        except Exception:
            pass

    def _compute_rolling_latency(self, provider: str) -> None:
        samples = sorted(self._latency_samples[provider])
        if not samples:
            return
        n = len(samples)
        stats = self._stats[provider]
        stats.latency_rolling_p50 = samples[min(n - 1, int(n * 0.50))]
        stats.latency_rolling_p99 = samples[min(n - 1, int(n * 0.99))]

    async def get_report(self, provider: str | None = None) -> list[ConsumptionReport]:
        """Get consumption report for one or all providers."""
        now = datetime.now(UTC)
        elapsed_hours = (now - self._period_start_dt).total_seconds() / 3600.0
        reports: list[ConsumptionReport] = []

        async with self._lock:
            providers = [provider] if provider else sorted(self._stats.keys())
            for prov in providers:
                stats = self._stats.get(prov)
                if not stats:
                    continue
                monthly_cost = (
                    (stats.total_cost_usd / elapsed_hours * 730.0) if elapsed_hours > 0 else 0.0
                )
                reports.append(
                    ConsumptionReport(
                        provider_name=prov,
                        stats=stats,
                        period_start=self._period_start_dt,
                        period_end=now,
                        projected_monthly_cost=monthly_cost,
                    )
                )
        return reports

    async def get_dashboard(self) -> dict[str, Any]:
        """Returns a dict suitable for JSON dashboard display."""
        reports = await self.get_report()
        total_cost = sum(r.stats.total_cost_usd for r in reports)
        total_calls = sum(r.stats.total_calls for r in reports)
        total_errors = sum(r.stats.error_calls for r in reports)
        total_rate_limited = sum(r.stats.rate_limited for r in reports)
        total_cache_hits = sum(r.stats.cache_hits for r in reports)
        total_cache_misses = sum(r.stats.cache_misses for r in reports)

        providers = {}
        for r in reports:
            top_endpoints = sorted(
                r.stats.calls_per_endpoint.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:10]
            top_keys = sorted(
                r.stats.calls_per_key.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:10]
            providers[r.provider_name] = {
                "total_calls": r.stats.total_calls,
                "success_calls": r.stats.success_calls,
                "error_calls": r.stats.error_calls,
                "error_rate": round(r.stats.error_rate, 2),
                "rate_limited": r.stats.rate_limited,
                "circuit_open": r.stats.circuit_open,
                "cache_hit_rate": round(r.stats.cache_hit_rate, 2),
                "cache_hits": r.stats.cache_hits,
                "cache_misses": r.stats.cache_misses,
                "total_cost_usd": round(r.stats.total_cost_usd, 6),
                "projected_monthly_cost_usd": round(r.projected_monthly_cost, 4),
                "avg_duration_ms": round(r.stats.avg_duration * 1000, 2),
                "latency_p50_ms": round(r.stats.latency_rolling_p50 * 1000, 1),
                "latency_p99_ms": round(r.stats.latency_rolling_p99 * 1000, 1),
                "top_endpoints": top_endpoints,
                "top_api_keys": top_keys,
            }

        elapsed_hours = (datetime.now(UTC) - self._period_start_dt).total_seconds() / 3600.0
        return {
            "period_start": self._period_start_dt.isoformat(),
            "period_end": datetime.now(UTC).isoformat(),
            "elapsed_hours": round(elapsed_hours, 2),
            "total_calls": total_calls,
            "total_cost_usd": round(total_cost, 6),
            "projected_monthly_cost_usd": round(
                (total_cost / elapsed_hours * 730.0) if elapsed_hours > 0 else 0.0, 4
            ),
            "total_errors": total_errors,
            "total_rate_limited": total_rate_limited,
            "total_cache_hits": total_cache_hits,
            "total_cache_misses": total_cache_misses,
            "overall_cache_hit_rate": round(
                (
                    (total_cache_hits / (total_cache_hits + total_cache_misses) * 100)
                    if (total_cache_hits + total_cache_misses) > 0
                    else 0.0
                ),
                2,
            ),
            "providers": providers,
        }

    async def reset(self) -> None:
        """Reset all accumulated stats (e.g., after a new billing cycle)."""
        async with self._lock:
            self._records.clear()
            self._stats.clear()
            self._latency_samples.clear()
            self._period_start = time.time()
            self._period_start_dt = datetime.now(UTC)

    def get_metric_labels(self, provider: str, endpoint: str) -> dict[str, str]:
        """Generate Prometheus-compatible label values."""
        return {
            "provider": provider.replace(" ", "_").lower(),
            "endpoint": endpoint.replace("/", "_").replace("{", "").replace("}", ""),
        }


# Module-level singleton
api_consumption_monitor = ApiConsumptionMonitor()


# ---------------------------------------------------------------------------
# Convenience helpers for instrumentation
# ---------------------------------------------------------------------------


async def record_api_call(
    provider: str,
    endpoint: str,
    api_key_label: str = "default",
    *,
    status: str = "success",
    duration_seconds: float = 0.0,
    cache_hit: bool = False,
    bytes_received: int = 0,
    retry_count: int = 0,
    error_message: str = "",
    module: str = "",
) -> None:
    """Top-level helper to record a call from anywhere."""
    is_hit = cache_hit and status == "success"
    call_status = ApiCallStatus.CACHE_HIT if is_hit else ApiCallStatus(status)
    await api_consumption_monitor.record(
        provider=provider,
        endpoint=endpoint,
        api_key_label=api_key_label,
        status=call_status,
        duration_seconds=duration_seconds,
        cache_hit=cache_hit,
        bytes_received=bytes_received,
        retry_count=retry_count,
        error_message=error_message,
        module=module,
    )


__all__ = [
    "ApiCallStatus",
    "ApiConsumptionMonitor",
    "ConsumptionRecord",
    "ConsumptionReport",
    "ProviderStats",
    "api_consumption_monitor",
    "record_api_call",
]
