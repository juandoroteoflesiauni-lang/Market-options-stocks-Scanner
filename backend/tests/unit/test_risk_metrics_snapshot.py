from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.models.risk_metrics_snapshot import RiskMetricsSnapshot


def test_risk_metrics_snapshot_creation() -> None:
    """Test successful creation of RiskMetricsSnapshot."""
    snapshot = RiskMetricsSnapshot(
        sample_size=100,
        expectancy_r=Decimal("0.5"),
        expectancy_by_setup={"VPIN": Decimal("0.6"), "OFI": Decimal("0.4")},
        profit_factor=1.8,
        sharpe=1.6,
        sortino=2.1,
        calmar=2.5,
        bur=0.4,
        buffer_zone="GREEN",
        ulcer=1.2,
        var95=Decimal("0.02"),
        cvar95=Decimal("0.03"),
        cvar99=Decimal("0.05"),
        kelly_applied=0.15,
        risk_of_ruin_pct=0.0005,
    )

    assert snapshot.sample_size == 100
    assert snapshot.expectancy_r == Decimal("0.5")
    assert snapshot.buffer_zone == "GREEN"
    assert snapshot.profit_factor == 1.8


def test_risk_metrics_snapshot_is_frozen() -> None:
    """Test that modifying RiskMetricsSnapshot raises ValidationError."""
    snapshot = RiskMetricsSnapshot(
        sample_size=10,
        expectancy_r=Decimal("0.1"),
        expectancy_by_setup={},
        profit_factor=1.0,
        sharpe=0.0,
        sortino=0.0,
        calmar=0.0,
        bur=0.0,
        buffer_zone="GREEN",
        ulcer=0.0,
        var95=Decimal("0.0"),
        cvar95=Decimal("0.0"),
        cvar99=Decimal("0.0"),
        kelly_applied=0.0,
        risk_of_ruin_pct=0.0,
    )

    with pytest.raises(ValidationError):
        snapshot.profit_factor = 2.0  # type: ignore
