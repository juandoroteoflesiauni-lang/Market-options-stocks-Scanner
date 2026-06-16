"""Integration tests for the funding WebSocket telemetry stream."""

from fastapi.testclient import TestClient

from backend.main import app


def test_funding_websocket_telemetry() -> None:
    """Test that the /ws/funding endpoint connects and sends the aggregated telemetry JSON."""
    client = TestClient(app)
    with client.websocket_connect("/api/v1/ws/funding") as websocket:
        data = websocket.receive_json()
        assert "globalContext" in data
        assert "riskMetrics" in data
        assert "builderMetrics" in data

        # Check sub-fields
        assert "market_regime" in data["globalContext"]
        assert "bur" in data["riskMetrics"]
        assert "account_id" in data["builderMetrics"]
