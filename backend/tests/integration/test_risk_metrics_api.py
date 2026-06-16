from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.risk_metrics_router import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_get_risk_metrics_api() -> None:
    """Test the GET /risk-metrics endpoint."""
    response = client.get("/api/v1/funding/risk-metrics?window=10")
    assert response.status_code == 200

    data = response.json()
    assert "expectancy_r" in data
    assert "profit_factor" in data
    assert "sample_size" in data


def test_post_mock_trade_api() -> None:
    """Test the POST /mock-trade endpoint."""
    response = client.post("/api/v1/funding/mock-trade")
    assert response.status_code == 200
    data = response.json()
    assert "expectancy_r" in data
    assert "sample_size" in data

