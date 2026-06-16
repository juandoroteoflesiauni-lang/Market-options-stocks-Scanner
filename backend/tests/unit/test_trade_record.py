from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.models.trade_record import TradeRecord


def test_trade_record_creation() -> None:
    """Test successful creation of a TradeRecord."""
    # ARRANGE
    now = datetime.now(UTC)

    # ACT
    record = TradeRecord(
        trade_id="T-001",
        setup_type="VPIN",
        symbol="AAPL",
        direction="LONG",
        entry_price=Decimal("150.0"),
        exit_price=Decimal("155.0"),
        quantity=Decimal("10.0"),
        risk_r=Decimal("1.0"),
        realized_r=Decimal("1.5"),
        pnl=Decimal("50.0"),
        opened_at=now,
        closed_at=now,
        equity_after=Decimal("100050.0"),
        mode="paper",
    )

    # ASSERT
    assert record.trade_id == "T-001"
    assert record.setup_type == "VPIN"
    assert record.realized_r == Decimal("1.5")


def test_trade_record_is_frozen() -> None:
    """Test that modifying a TradeRecord raises an error."""
    # ARRANGE
    now = datetime.now(UTC)
    record = TradeRecord(
        trade_id="T-001",
        setup_type="VPIN",
        symbol="AAPL",
        direction="LONG",
        entry_price=Decimal("150.0"),
        exit_price=None,
        quantity=Decimal("10.0"),
        risk_r=Decimal("1.0"),
        realized_r=None,
        pnl=None,
        opened_at=now,
        closed_at=None,
        equity_after=None,
        mode="live",
    )

    # ACT & ASSERT
    with pytest.raises(ValidationError):
        record.pnl = Decimal("10.0")  # type: ignore
