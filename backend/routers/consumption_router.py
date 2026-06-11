"""REST endpoints for API consumption monitoring dashboard."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.hub.api_consumption_monitor import api_consumption_monitor
from backend.hub.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/consumption", tags=["consumption"])


class ConsumptionDashboardResponse(BaseModel):
    period_start: str
    period_end: str
    elapsed_hours: float
    total_calls: int
    total_cost_usd: float
    projected_monthly_cost_usd: float
    total_errors: int
    total_rate_limited: int
    total_cache_hits: int
    total_cache_misses: int
    overall_cache_hit_rate: float
    providers: dict[str, Any]


class RateLimiterStatusResponse(BaseModel):
    buckets: dict[str, dict[str, Any]]


class ResetRequest(BaseModel):
    confirm: bool = False


@router.get("/dashboard")
async def consumption_dashboard() -> ConsumptionDashboardResponse:
    """Returns the current API consumption dashboard data."""
    dashboard = await api_consumption_monitor.get_dashboard()
    return ConsumptionDashboardResponse(**dashboard)


@router.get("/providers")
async def list_providers() -> list[str]:
    """Returns list of all tracked providers."""
    reports = await api_consumption_monitor.get_report()
    return [r.provider_name for r in reports]


@router.get("/providers/{provider}")
async def provider_detail(provider: str) -> dict[str, Any]:
    """Returns detailed consumption stats for a specific provider."""
    reports = await api_consumption_monitor.get_report(provider)
    if not reports:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")
    r = reports[0]
    return {
        "provider": r.provider_name,
        "period_start": r.period_start.isoformat(),
        "period_end": r.period_end.isoformat(),
        "total_calls": r.stats.total_calls,
        "success_calls": r.stats.success_calls,
        "error_calls": r.stats.error_calls,
        "error_rate": round(r.stats.error_rate, 2),
        "rate_limited": r.stats.rate_limited,
        "circuit_open": r.stats.circuit_open,
        "cache_hits": r.stats.cache_hits,
        "cache_misses": r.stats.cache_misses,
        "cache_hit_rate": round(r.stats.cache_hit_rate, 2),
        "total_cost_usd": round(r.stats.total_cost_usd, 6),
        "projected_monthly_cost_usd": round(r.projected_monthly_cost, 4),
        "avg_duration_ms": round(r.stats.avg_duration * 1000, 2),
        "latency_p50_ms": round(r.stats.latency_rolling_p50 * 1000, 1),
        "latency_p99_ms": round(r.stats.latency_rolling_p99 * 1000, 1),
        "top_endpoints": [
            {"endpoint": ep, "calls": count}
            for ep, count in sorted(
                r.stats.calls_per_endpoint.items(), key=lambda x: x[1], reverse=True
            )[:20]
        ],
        "top_api_keys": [
            {"key_label": k, "calls": count}
            for k, count in sorted(r.stats.calls_per_key.items(), key=lambda x: x[1], reverse=True)[
                :10
            ]
        ],
        "cost_per_endpoint": [
            {"endpoint": ep, "cost_usd": round(cost, 6)}
            for ep, cost in sorted(
                r.stats.cost_per_endpoint.items(), key=lambda x: x[1], reverse=True
            )[:20]
        ],
    }


@router.get("/rate-limiter")
async def rate_limiter_status() -> RateLimiterStatusResponse:
    """Returns current state of all rate limiter token buckets."""
    status = rate_limiter.get_status()
    return RateLimiterStatusResponse(buckets=status)


@router.post("/reset")
async def reset_consumption_stats(body: ResetRequest) -> dict[str, str]:
    """Reset all accumulated consumption stats (for new billing cycle)."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Must set confirm=true to reset consumption stats",
        )
    await api_consumption_monitor.reset()
    logger.info("API consumption stats reset by user request")
    return {"status": "reset"}


__all__ = ["router"]
