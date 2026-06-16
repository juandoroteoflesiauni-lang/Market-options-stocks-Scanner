from __future__ import annotations
from typing import Any
"""REST endpoints for the Audit Complex — unified audit, logging and process recording."""


import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.audit.audit_complex_store import AuditComplexStore
from backend.config.settings import load_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/audit", tags=["audit-complex"])

# ── Lazy singleton store ─────────────────────────────────────────────────────

_store: AuditComplexStore | None = None


def _get_store() -> AuditComplexStore:
    global _store
    if _store is None:
        settings = load_settings()
        _store = AuditComplexStore(db_path=settings.audit_db_path)
    return _store


def configure_audit_complex_store(store: AuditComplexStore) -> None:
    """Inject a pre-configured store (used during app lifespan startup)."""
    global _store
    _store = store


# ═══════════════════════════════════════════════════════════════════════════════
# Response models
# ═══════════════════════════════════════════════════════════════════════════════


class AuditDashboardResponse(BaseModel):
    health: dict[str, Any]
    module_summary: dict[str, dict[str, Any]]
    api_call_stats: dict[str, dict[str, Any]]
    error_stats: dict[str, dict[str, Any]]
    log_stats: dict[str, Any]


class ApiConsumptionByModuleResponse(BaseModel):
    modules: dict[str, dict[str, Any]]
    provider_breakdown: dict[str, dict[str, dict[str, Any]]]


class ProcessSnapshotListResponse(BaseModel):
    snapshots: list[dict[str, Any]]
    total: int


class ErrorListResponse(BaseModel):
    errors: list[dict[str, Any]]
    total: int


class ErrorStatsResponse(BaseModel):
    by_module: dict[str, dict[str, Any]]
    total_errors: int
    total_resolved: int
    total_unresolved: int


class LogSearchResponse(BaseModel):
    logs: list[dict[str, Any]]
    total_matching: int


class LogStatsResponse(BaseModel):
    by_level: dict[str, int]
    by_module: dict[str, dict[str, Any]]
    total_logs: int


class ModuleDetailResponse(BaseModel):
    module: str
    api_calls: dict[str, Any]
    errors: dict[str, Any]
    recent_snapshots: list[dict[str, Any]]


class ResolveErrorRequest(BaseModel):
    resolved_by: str = ""
    notes: str = ""


class LogSearchRequest(BaseModel):
    query: str | None = None
    module: str | None = None
    level: str | None = None
    correlation_id: str | None = None
    tag: str | None = None
    limit: int = 100


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints — Dashboard
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/dashboard")
async def audit_dashboard() -> AuditDashboardResponse:
    """Main audit dashboard — consolidated view of all audit subsystems."""
    store = _get_store()
    return AuditDashboardResponse(
        health=store.get_audit_health(),
        module_summary=store.get_module_summary(),
        api_call_stats=store.get_api_call_stats_by_module(),
        error_stats=store.get_error_stats_by_module(),
        log_stats=store.get_log_stats(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints — API Consumption by Module
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api-consumption")
async def api_consumption_by_module() -> ApiConsumptionByModuleResponse:
    """API consumption stats grouped by module (scanner, bingx, etc.)."""
    store = _get_store()
    return ApiConsumptionByModuleResponse(
        modules=store.get_api_call_stats_by_module(),
        provider_breakdown=store.get_api_call_stats_by_provider_per_module(),
    )


@router.get("/api-consumption/{module}")
async def api_consumption_module_detail(module: str) -> dict[str, Any]:
    """Detailed API consumption for a specific module."""
    store = _get_store()
    calls = store.list_api_calls(module=module, limit=500)
    if not calls:
        raise HTTPException(status_code=404, detail=f"Module '{module}' has no recorded API calls")

    # Aggregate stats from the calls
    total = len(calls)
    errors = sum(1 for c in calls if c["status"] in ("error", "timeout"))
    rate_limited = sum(1 for c in calls if c["status"] == "rate_limited")
    total_cost = sum(c["estimated_cost"] for c in calls)
    cache_hits = sum(1 for c in calls if c["cache_hit"])

    providers: dict[str, int] = {}
    endpoints: dict[str, int] = {}
    for c in calls:
        providers[c["provider"]] = providers.get(c["provider"], 0) + 1
        endpoints[c["endpoint"]] = endpoints.get(c["endpoint"], 0) + 1

    return {
        "module": module,
        "total_calls": total,
        "error_calls": errors,
        "error_rate_pct": round(errors / total * 100, 2) if total > 0 else 0.0,
        "rate_limited": rate_limited,
        "total_cost_usd": round(total_cost, 6),
        "cache_hits": cache_hits,
        "cache_hit_rate_pct": round(cache_hits / total * 100, 2) if total > 0 else 0.0,
        "providers_used": providers,
        "top_endpoints": sorted(endpoints.items(), key=lambda x: x[1], reverse=True)[:20],
        "recent_calls": calls[:20],
    }


@router.get("/api-consumption/projections/cost")
async def cost_projections() -> dict[str, Any]:
    """Cost projections per module based on current consumption rate."""
    store = _get_store()
    all_calls = store.list_api_calls(limit=2000)
    if not all_calls:
        return {"modules": {}, "total_projected_monthly_usd": 0.0}

    from datetime import UTC, datetime

    # Group by module and compute hourly rate
    module_data: dict[str, dict[str, Any]] = {}
    for c in all_calls:
        mod = c["module"]
        if mod not in module_data:
            module_data[mod] = {
                "cost": 0.0,
                "calls": 0,
                "first_ts": c["timestamp"],
                "last_ts": c["timestamp"],
            }
        module_data[mod]["cost"] += c["estimated_cost"]
        module_data[mod]["calls"] += 1
        if c["timestamp"] < module_data[mod]["first_ts"]:
            module_data[mod]["first_ts"] = c["timestamp"]
        if c["timestamp"] > module_data[mod]["last_ts"]:
            module_data[mod]["last_ts"] = c["timestamp"]

    now = datetime.now(UTC)
    projections: dict[str, Any] = {}
    total_monthly = 0.0

    for mod, data in module_data.items():
        try:
            first = datetime.fromisoformat(data["first_ts"])
            hours = max((now - first).total_seconds() / 3600.0, 0.01)
        except (ValueError, TypeError):
            hours = 1.0
        monthly = data["cost"] / hours * 730.0
        total_monthly += monthly
        projections[mod] = {
            "current_cost_usd": round(data["cost"], 6),
            "total_calls": data["calls"],
            "hours_tracked": round(hours, 2),
            "projected_monthly_usd": round(monthly, 4),
        }

    return {"modules": projections, "total_projected_monthly_usd": round(total_monthly, 4)}


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints — Process Snapshots
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/process-snapshots")
async def list_process_snapshots(
    module: str | None = Query(None),
    symbol: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> ProcessSnapshotListResponse:
    """List process snapshots with optional filters."""
    store = _get_store()
    snapshots = store.list_process_snapshots(module=module, symbol=symbol, limit=limit)
    return ProcessSnapshotListResponse(
        snapshots=snapshots,
        total=store.count_process_snapshots(),
    )


@router.get("/process-snapshots/symbol/{symbol}")
async def process_snapshots_by_symbol(
    symbol: str,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """All process snapshots for a given symbol (e.g. MSFT-USDT)."""
    store = _get_store()
    return store.list_process_snapshots(symbol=symbol, limit=limit)


@router.get("/process-snapshots/snapshot/{snapshot_id}")
async def get_process_snapshot(snapshot_id: str) -> dict[str, Any]:
    """Full detail of a single process snapshot."""
    store = _get_store()
    snap = store.get_process_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_id}' not found")
    return snap


@router.get("/process-snapshots/cycle/{operation_id}")
async def process_snapshots_by_cycle(operation_id: str) -> list[dict[str, Any]]:
    """All process snapshots tied to a specific operation/cycle ID."""
    store = _get_store()
    return store.list_process_snapshots(operation_id=operation_id, limit=200)


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints — Errors
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/errors")
async def list_errors(
    module: str | None = Query(None),
    severity: str | None = Query(None),
    resolved: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> ErrorListResponse:
    """List errors with optional filters by module, severity, resolved status."""
    store = _get_store()
    errors = store.list_errors(module=module, severity=severity, resolved=resolved, limit=limit)
    return ErrorListResponse(
        errors=errors,
        total=store.count_errors(),
    )


@router.get("/errors/stats")
async def error_stats() -> ErrorStatsResponse:
    """Error statistics grouped by module."""
    store = _get_store()
    by_module = store.get_error_stats_by_module()
    total = sum(m["total"] for m in by_module.values())
    resolved = sum(m["resolved"] for m in by_module.values())
    return ErrorStatsResponse(
        by_module=by_module,
        total_errors=total,
        total_resolved=resolved,
        total_unresolved=total - resolved,
    )


@router.get("/errors/{error_id}")
async def get_error(error_id: str) -> dict[str, Any]:
    """Full detail of a single error including stack trace and context."""
    store = _get_store()
    err = store.get_error(error_id)
    if not err:
        raise HTTPException(status_code=404, detail=f"Error '{error_id}' not found")
    return err


@router.patch("/errors/{error_id}/resolve")
async def resolve_error(error_id: str, body: ResolveErrorRequest) -> dict[str, str]:
    """Mark an error as resolved."""
    store = _get_store()
    ok = store.resolve_error(error_id, resolved_by=body.resolved_by, notes=body.notes)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Error '{error_id}' not found")
    return {"status": "resolved", "error_id": error_id}


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints — Logs
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/logs")
async def search_logs(
    query: str | None = Query(None),
    module: str | None = Query(None),
    level: str | None = Query(None),
    correlation_id: str | None = Query(None),
    tag: str | None = Query(None),
    limit: int = Query(100, ge=1, le=2000),
) -> LogSearchResponse:
    """Search structured logs with filters."""
    store = _get_store()
    logs = store.search_logs(
        query=query,
        module=module,
        level=level,
        correlation_id=correlation_id,
        tag=tag,
        limit=limit,
    )
    return LogSearchResponse(logs=logs, total_matching=len(logs))


@router.post("/logs/search")
async def advanced_log_search(body: LogSearchRequest) -> LogSearchResponse:
    """Advanced log search with POST body (for complex queries)."""
    store = _get_store()
    logs = store.search_logs(
        query=body.query,
        module=body.module,
        level=body.level,
        correlation_id=body.correlation_id,
        tag=body.tag,
        limit=body.limit,
    )
    return LogSearchResponse(logs=logs, total_matching=len(logs))


@router.get("/logs/stats")
async def log_stats() -> LogStatsResponse:
    """Log statistics by level and module."""
    store = _get_store()
    stats = store.get_log_stats()
    return LogStatsResponse(
        by_level=stats["by_level"],
        by_module=stats["by_module"],
        total_logs=stats["total_logs"],
    )


@router.get("/logs/trace/{correlation_id}")
async def log_trace(correlation_id: str) -> list[dict[str, Any]]:
    """Full log trace for a correlation ID — reconstructs entire call chain."""
    store = _get_store()
    return store.get_logs_by_correlation_id(correlation_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints — Rate Limits
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/rate-limits")
async def rate_limits_by_module() -> dict[str, Any]:
    """Rate limit events grouped by module and provider."""
    store = _get_store()
    rate_calls = store.list_api_calls(status="rate_limited", limit=500)

    by_module: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    for c in rate_calls:
        by_module[c["module"]] = by_module.get(c["module"], 0) + 1
        by_provider[c["provider"]] = by_provider.get(c["provider"], 0) + 1

    return {
        "total_rate_limited": len(rate_calls),
        "by_module": by_module,
        "by_provider": by_provider,
        "recent": rate_calls[:20],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints — Per-Module Detail
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/modules")
async def list_modules() -> list[str]:
    """List all modules with audit data."""
    store = _get_store()
    return sorted(store.get_module_summary().keys())


@router.get("/modules/{module}")
async def module_detail(module: str) -> ModuleDetailResponse:
    """Complete audit detail for a single module."""
    store = _get_store()

    # API calls
    api_calls = store.list_api_calls(module=module, limit=50)
    api_stats = store.get_api_call_stats_by_module().get(module, {})

    # Errors
    errors = store.list_errors(module=module, limit=50)
    error_stats = store.get_error_stats_by_module().get(module, {})

    # Recent snapshots
    snapshots = store.list_process_snapshots(module=module, limit=10)

    return ModuleDetailResponse(
        module=module,
        api_calls={
            "stats": api_stats,
            "recent": api_calls[:10],
        },
        errors={
            "stats": error_stats,
            "recent": errors[:10],
        },
        recent_snapshots=snapshots,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints — Health
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/health")
async def audit_health() -> dict[str, Any]:
    """Audit subsystem health check."""
    store = _get_store()
    return store.get_audit_health()


__all__ = ["configure_audit_complex_store", "router"]
