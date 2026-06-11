"""Integration tests for Audit Complex REST router."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.audit.audit_complex_store import (
    ApiCallAuditEntry,
    AuditComplexStore,
    ErrorAuditEntry,
    LogAuditEntry,
    ProcessSnapshotEntry,
)
from backend.routers.audit_complex_router import configure_audit_complex_store, router


@pytest.fixture
def store() -> AuditComplexStore:
    return AuditComplexStore(":memory:")


@pytest.fixture
def client(store: AuditComplexStore) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    configure_audit_complex_store(store)
    return TestClient(app)


def _seed_api_calls(store: AuditComplexStore) -> None:
    store.persist_api_call(
        ApiCallAuditEntry(
            module="scanner",
            provider="fmp",
            endpoint="/quote",
            status="success",
            duration_ms=100.0,
            estimated_cost=0.001,
        )
    )
    store.persist_api_call(
        ApiCallAuditEntry(
            module="bingx",
            provider="bingx",
            endpoint="/trade",
            status="error",
            duration_ms=500.0,
            estimated_cost=0.01,
        )
    )
    store.persist_api_call(
        ApiCallAuditEntry(
            module="scanner",
            provider="fmp",
            endpoint="/search",
            status="rate_limited",
            duration_ms=0.0,
            estimated_cost=0.0,
        )
    )


def _seed_snapshots(store: AuditComplexStore) -> None:
    store.persist_process_snapshot(
        ProcessSnapshotEntry(module="bingx", symbol="BTC-USDT", indicators={"rsi": 55.0})
    )
    store.persist_process_snapshot(
        ProcessSnapshotEntry(module="scanner", symbol="ETH-USDT", indicators={"adx": 30.0})
    )


def _seed_errors(store: AuditComplexStore) -> None:
    e1 = ErrorAuditEntry(
        module="scanner", severity="error", error_type="TIMEOUT", message="timeout"
    )
    e2 = ErrorAuditEntry(
        module="bingx", severity="critical", error_type="EXECUTION_FAILURE", message="failed"
    )
    store.persist_error(e1)
    store.persist_error(e2)


def _seed_logs(store: AuditComplexStore) -> None:
    store.persist_log(
        LogAuditEntry(level="INFO", module="system", logger_name="sys", message="started")
    )
    store.persist_log(
        LogAuditEntry(level="ERROR", module="scanner", logger_name="scan", message="error")
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuditDashboard:
    def test_dashboard_returns_all_sections(
        self, client: TestClient, store: AuditComplexStore
    ) -> None:
        _seed_api_calls(store)
        _seed_errors(store)
        _seed_logs(store)
        resp = client.get("/api/v1/audit/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "health" in data
        assert "module_summary" in data
        assert "api_call_stats" in data
        assert "error_stats" in data
        assert "log_stats" in data

    def test_dashboard_empty_store(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/dashboard")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# API Consumption
# ═══════════════════════════════════════════════════════════════════════════════


class TestApiConsumption:
    def test_api_consumption_by_module(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_api_calls(store)
        resp = client.get("/api/v1/audit/api-consumption")
        assert resp.status_code == 200
        data = resp.json()
        assert "modules" in data
        assert "scanner" in data["modules"]

    def test_api_consumption_module_detail(
        self, client: TestClient, store: AuditComplexStore
    ) -> None:
        _seed_api_calls(store)
        resp = client.get("/api/v1/audit/api-consumption/scanner")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module"] == "scanner"
        assert data["total_calls"] == 2
        assert data["error_calls"] == 0

    def test_api_consumption_module_detail_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/api-consumption/nonexistent")
        assert resp.status_code == 404

    def test_cost_projections(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_api_calls(store)
        resp = client.get("/api/v1/audit/api-consumption/projections/cost")
        assert resp.status_code == 200
        data = resp.json()
        assert "modules" in data
        assert "total_projected_monthly_usd" in data

    def test_cost_projections_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/api-consumption/projections/cost")
        assert resp.status_code == 200
        assert resp.json()["total_projected_monthly_usd"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Process Snapshots
# ═══════════════════════════════════════════════════════════════════════════════


class TestProcessSnapshots:
    def test_list_process_snapshots(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_snapshots(store)
        resp = client.get("/api/v1/audit/process-snapshots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["snapshots"]) == 2

    def test_list_process_snapshots_filtered(
        self, client: TestClient, store: AuditComplexStore
    ) -> None:
        _seed_snapshots(store)
        resp = client.get("/api/v1/audit/process-snapshots?module=scanner")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["snapshots"]) == 1

    def test_process_snapshots_by_symbol(
        self, client: TestClient, store: AuditComplexStore
    ) -> None:
        _seed_snapshots(store)
        resp = client.get("/api/v1/audit/process-snapshots/symbol/BTC-USDT")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "BTC-USDT"

    def test_process_snapshots_by_symbol_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/process-snapshots/symbol/NONEXISTENT")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_process_snapshot(self, client: TestClient, store: AuditComplexStore) -> None:
        snap = ProcessSnapshotEntry(module="test", symbol="X", indicators={})
        sid = store.persist_process_snapshot(snap)
        resp = client.get(f"/api/v1/audit/process-snapshots/snapshot/{sid}")
        assert resp.status_code == 200
        assert resp.json()["snapshot_id"] == sid

    def test_get_process_snapshot_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/process-snapshots/snapshot/nonexistent")
        assert resp.status_code == 404

    def test_process_snapshots_by_cycle(self, client: TestClient, store: AuditComplexStore) -> None:
        snap = ProcessSnapshotEntry(module="test", symbol="X", indicators={}, operation_id="op-1")
        store.persist_process_snapshot(snap)
        resp = client.get("/api/v1/audit/process-snapshots/cycle/op-1")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrors:
    def test_list_errors(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_errors(store)
        resp = client.get("/api/v1/audit/errors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["errors"]) == 2

    def test_list_errors_filtered(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_errors(store)
        resp = client.get("/api/v1/audit/errors?severity=critical")
        assert resp.status_code == 200
        assert len(resp.json()["errors"]) == 1

    def test_error_stats(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_errors(store)
        resp = client.get("/api/v1/audit/errors/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_errors"] == 2
        assert data["total_unresolved"] == 2

    def test_get_error(self, client: TestClient, store: AuditComplexStore) -> None:
        entry = ErrorAuditEntry(module="test", severity="error", error_type="X", message="test")
        eid = store.persist_error(entry)
        resp = client.get(f"/api/v1/audit/errors/{eid}")
        assert resp.status_code == 200
        assert resp.json()["error_id"] == eid

    def test_get_error_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/errors/nonexistent")
        assert resp.status_code == 404

    def test_resolve_error(self, client: TestClient, store: AuditComplexStore) -> None:
        entry = ErrorAuditEntry(module="test", severity="error", error_type="X", message="test")
        eid = store.persist_error(entry)
        resp = client.patch(
            f"/api/v1/audit/errors/{eid}/resolve",
            json={"resolved_by": "tester", "notes": "fixed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"
        # Verify persistence
        err = store.get_error(eid)
        assert err is not None
        assert err["resolved"] is True

    def test_resolve_error_404(self, client: TestClient) -> None:
        resp = client.patch("/api/v1/audit/errors/nonexistent/resolve", json={})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Logs
# ═══════════════════════════════════════════════════════════════════════════════


class TestLogs:
    def test_search_logs(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_logs(store)
        resp = client.get("/api/v1/audit/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matching"] == 2
        assert len(data["logs"]) == 2

    def test_search_logs_with_query(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_logs(store)
        resp = client.get("/api/v1/audit/logs?query=error")
        assert resp.status_code == 200
        assert resp.json()["total_matching"] == 1

    def test_advanced_log_search_post(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_logs(store)
        resp = client.post("/api/v1/audit/logs/search", json={"level": "INFO"})
        assert resp.status_code == 200
        assert resp.json()["total_matching"] == 1

    def test_log_stats(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_logs(store)
        resp = client.get("/api/v1/audit/logs/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_logs"] == 2

    def test_log_trace(self, client: TestClient, store: AuditComplexStore) -> None:
        store.persist_log(
            LogAuditEntry(
                level="INFO",
                module="sys",
                logger_name="x",
                message="step1",
                correlation_id="trace-id-1",
            )
        )
        store.persist_log(
            LogAuditEntry(
                level="ERROR",
                module="sys",
                logger_name="x",
                message="step2",
                correlation_id="trace-id-1",
            )
        )
        resp = client.get("/api/v1/audit/logs/trace/trace-id-1")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Rate Limits
# ═══════════════════════════════════════════════════════════════════════════════


class TestRateLimits:
    def test_rate_limits(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_api_calls(store)
        resp = client.get("/api/v1/audit/rate-limits")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_rate_limited"] == 1
        assert data["by_module"]["scanner"] == 1

    def test_rate_limits_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/rate-limits")
        assert resp.status_code == 200
        assert resp.json()["total_rate_limited"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Modules
# ═══════════════════════════════════════════════════════════════════════════════


class TestModules:
    def test_list_modules(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_api_calls(store)
        _seed_errors(store)
        resp = client.get("/api/v1/audit/modules")
        assert resp.status_code == 200
        modules = resp.json()
        assert "scanner" in modules
        assert "bingx" in modules

    def test_list_modules_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/modules")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_module_detail(self, client: TestClient, store: AuditComplexStore) -> None:
        _seed_api_calls(store)
        _seed_errors(store)
        _seed_snapshots(store)
        resp = client.get("/api/v1/audit/modules/scanner")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module"] == "scanner"
        assert "api_calls" in data
        assert "errors" in data
        assert "recent_snapshots" in data

    def test_module_detail_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/modules/nonexistent")
        assert resp.status_code == 200  # returns empty data, not 404


# ═══════════════════════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealth:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/api/v1/audit/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "db_path" in data
        assert "tables" in data
