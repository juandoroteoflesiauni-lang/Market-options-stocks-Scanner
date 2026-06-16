from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backend.infrastructure.repositories.trade_history_repository import (
    TradeHistoryRepository,
)
from backend.models.trade_record import TradeRecord


def test_trade_history_repository_save_and_get(tmp_path: Path) -> None:
    """Test saving and retrieving trade records from the SQLite repository."""
    repo = TradeHistoryRepository(db_path=str(tmp_path / "test.db"))

    now = datetime.now(UTC)

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

    repo.save(record)

    records = repo.get_all(mode="paper")
    assert len(records) == 1

    fetched = records[0]
    assert fetched.trade_id == "T-001"
    assert fetched.setup_type == "VPIN"
    assert fetched.entry_price == Decimal("150.0")
    assert fetched.realized_r == Decimal("1.5")


def test_trade_history_repository_get_recent(tmp_path: Path) -> None:
    """Test retrieving the N most recent records."""
    repo = TradeHistoryRepository(db_path=str(tmp_path / "test.db"))

    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    for i in range(5):
        # Opened 1 hour apart
        opened = base_time.replace(hour=i)
        record = TradeRecord(
            trade_id=f"T-00{i}",
            setup_type="VPIN",
            symbol="AAPL",
            direction="LONG",
            entry_price=Decimal("150.0"),
            exit_price=None,
            quantity=Decimal("10.0"),
            risk_r=Decimal("1.0"),
            realized_r=None,
            pnl=None,
            opened_at=opened,
            closed_at=None,
            equity_after=None,
            mode="paper",
        )
        repo.save(record)

    recent = repo.get_recent(window=3, mode="paper")
    assert len(recent) == 3
    # Should be sorted chronological: T-002, T-003, T-004
    assert recent[0].trade_id == "T-002"
    assert recent[-1].trade_id == "T-004"
