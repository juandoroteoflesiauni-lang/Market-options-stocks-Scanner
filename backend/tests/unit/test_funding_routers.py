from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

def test_global_context_router() -> None:
    response = client.get("/api/v1/funding/global-context")
    assert response.status_code == 200
    data = response.json()
    assert "market_regime" in data
    assert "is_valid" in data

def test_sizing_router() -> None:
    payload = {
        "kelly_base": 0.05,
        "global_factor": 1.0,
        "multi_factors": {
            "f_conviction": 1.0,
            "f_volatility": 1.0,
            "f_drawdown": 1.0,
            "f_regime": 1.0
        },
        "survival_recommended_risk_pct": 1.0,
        "remaining_daily_risk_pct": 2.0,
        "remaining_max_risk_pct": 5.0,
        "equity": 100000.0,
        "stop_distance_pct": 1.0
    }
    response = client.post("/api/v1/funding/sizing", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "allowed_risk_pct" in data
    assert "position_notional" in data

def test_convergence_router() -> None:
    payload = {
        "direction": "LONG",
        "context": {
            "is_valid": True,
            "market_regime": "MELTDOWN",
            "global_factor": "0.0",
            "vix_level": 35.0,
            "spy_trend": "BEAR",
            "qqq_trend": "BEAR"
        }
    }
    response = client.post("/api/v1/funding/convergence", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["is_allowed"] is False
    assert data["conviction_multiplier"] == "0.0"
