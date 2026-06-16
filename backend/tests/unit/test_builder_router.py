from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.builder_dashboard_service import (
    BuilderDashboardService,
    BuilderEvaluateRequest,
)
from backend.services.builder_state_store import BuilderStateStore

client = TestClient(app)


def test_builder_metrics_endpoint_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "predictions.db"
    service = BuilderDashboardService(predictions_db=db_path)
    app.dependency_overrides[
        __import__(
            "backend.api.routes.builder_router",
            fromlist=["get_builder_dashboard_service"],
        ).get_builder_dashboard_service
    ] = lambda: service
    try:
        response = client.get("/api/v1/funding/builder/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["profile_id"] == "MFFU_BUILDER_50K"
        assert data["phase"] == "EVAL_ACTIVE"
        assert "distance_to_trailing_dd" in data
        assert "distance_to_dll_soft_pause" in data
        assert "eval_progress_pct" in data
        assert "buffer_progress_pct" in data
        assert "payout_eligibility_state" in data
    finally:
        app.dependency_overrides.clear()


def test_builder_state_endpoint_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "predictions.db"
    service = BuilderDashboardService(predictions_db=db_path)
    router_module = __import__(
        "backend.api.routes.builder_router",
        fromlist=["get_builder_dashboard_service"],
    )
    app.dependency_overrides[router_module.get_builder_dashboard_service] = lambda: service
    try:
        response = client.get("/api/v1/funding/builder/state")
        assert response.status_code == 200
        payload = response.json()
        assert payload["state"]["phase"] == "EVAL_ACTIVE"
        assert payload["metrics"]["phase"] == "EVAL_ACTIVE"
    finally:
        app.dependency_overrides.clear()


def test_builder_evaluate_endpoint_returns_contracts(tmp_path: Path) -> None:
    db_path = tmp_path / "predictions.db"
    service = BuilderDashboardService(predictions_db=db_path)
    router_module = __import__(
        "backend.api.routes.builder_router",
        fromlist=["get_builder_dashboard_service"],
    )
    app.dependency_overrides[router_module.get_builder_dashboard_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/funding/builder/evaluate",
            json={
                "symbol": "MNQ",
                "direction": "LONG",
                "entry": 20000.0,
                "stop": 19997.5,
                "prefer_micro": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "is_allowed" in data
        assert "contracts" in data
        assert "reason_codes" in data
        assert data["phase"] == "EVAL_ACTIVE"
    finally:
        app.dependency_overrides.clear()


def test_builder_dashboard_service_metrics_with_sim_state(tmp_path: Path) -> None:
    store = BuilderStateStore(predictions_db=tmp_path / "predictions.db")
    store.save_state(
        store.load_state().model_copy(
            update={
                "phase": "SIM_ACTIVE",
                "current_equity": Decimal("52500"),
            }
        )
    )
    store.create_payout_cycle()
    service = BuilderDashboardService(predictions_db=tmp_path / "predictions.db")
    metrics = service.get_metrics()

    assert metrics.phase == "SIM_ACTIVE"
    assert Decimal(metrics.eval_progress_pct) >= Decimal("0")
    assert Decimal(metrics.distance_to_trailing_dd) > Decimal("0")


def test_builder_evaluate_batch_endpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "predictions.db"
    service = BuilderDashboardService(predictions_db=db_path)
    router_module = __import__(
        "backend.api.routes.builder_router",
        fromlist=["get_builder_dashboard_service"],
    )
    app.dependency_overrides[router_module.get_builder_dashboard_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/funding/builder/evaluate-batch",
            json={
                "candidates": [
                    {"symbol": "MNQ", "entry": 20000.0, "stop": 19997.5, "prefer_micro": True},
                    {"symbol": "MES", "entry": 5000.0, "stop": 4999.0, "prefer_micro": True},
                ]
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 2
        assert all("loss_if_stopped_usd" in r for r in data["results"])
    finally:
        app.dependency_overrides.clear()


def test_builder_backtest_endpoint() -> None:
    response = client.post(
        "/api/v1/funding/builder/backtest",
        json={"daily_pnls": [1000, 1000, 1000]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["survived"] is True
    assert data["eval_passed"] is True


def test_builder_evaluate_includes_loss_scenario(tmp_path: Path) -> None:
    db_path = tmp_path / "predictions.db"
    service = BuilderDashboardService(predictions_db=db_path)
    router_module = __import__(
        "backend.api.routes.builder_router",
        fromlist=["get_builder_dashboard_service"],
    )
    app.dependency_overrides[router_module.get_builder_dashboard_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/funding/builder/evaluate",
            json={"symbol": "MNQ", "entry": 20000.0, "stop": 19990.0, "prefer_micro": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert "loss_if_stopped_usd" in data
        assert "equity_after_loss" in data
        assert "breaches_on_stop" in data
    finally:
        app.dependency_overrides.clear()


def test_builder_dashboard_service_evaluate_blocks_breach(tmp_path: Path) -> None:
    store = BuilderStateStore(predictions_db=tmp_path / "predictions.db")
    store.save_state(
        store.load_state().model_copy(
            update={
                "current_equity": Decimal("47900"),
                "high_watermark_balance": Decimal("50000"),
            }
        )
    )
    service = BuilderDashboardService(predictions_db=tmp_path / "predictions.db")
    result = service.evaluate_candidate(
        BuilderEvaluateRequest(
            symbol="MNQ",
            entry=20000.0,
            stop=19997.5,
            prefer_micro=True,
        )
    )

    assert result.is_allowed is False
    assert result.contracts == 0
    assert "builder_trailing_dd_critical" in result.reason_codes
