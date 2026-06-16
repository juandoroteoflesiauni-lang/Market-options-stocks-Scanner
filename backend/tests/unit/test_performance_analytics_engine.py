from datetime import UTC, datetime
from decimal import Decimal

from backend.domain.portfolio_risk_models import AccountState
from backend.models.trade_record import TradeRecord
from backend.services.performance_analytics_engine import PerformanceAnalyticsEngine


def test_performance_analytics_engine_empty() -> None:
    """Test with fewer than MIN_TRADES."""
    engine = PerformanceAnalyticsEngine()
    account = AccountState(
        initial_capital=100000.0,
        current_equity=100000.0,
        start_of_day_balance=100000.0,
    )

    snapshot = engine.compute_snapshot(trades=[], account_state=account, window=100)
    assert snapshot.sample_size == 0
    assert snapshot.expectancy_r == Decimal("0.0")


def test_performance_analytics_engine_metrics() -> None:
    """Test full metrics computation with sufficient trades."""
    engine = PerformanceAnalyticsEngine()
    engine.min_trades = 2  # Override for test

    account = AccountState(
        initial_capital=100000.0,
        current_equity=95000.0,
        start_of_day_balance=95000.0,
    )
    now = datetime.now(UTC)

    trades = [
        TradeRecord(
            trade_id="T1", setup_type="VPIN", symbol="AAPL", direction="LONG",
            entry_price=Decimal("150"), exit_price=Decimal("155"), quantity=Decimal("10"),
            risk_r=Decimal("1.0"), realized_r=Decimal("2.0"), pnl=Decimal("50"),
            opened_at=now, closed_at=now, equity_after=Decimal("95050"), mode="paper"
        ),
        TradeRecord(
            trade_id="T2", setup_type="OFI", symbol="MSFT", direction="SHORT",
            entry_price=Decimal("300"), exit_price=Decimal("305"), quantity=Decimal("10"),
            risk_r=Decimal("1.0"), realized_r=Decimal("-1.0"), pnl=Decimal("-50"),
            opened_at=now, closed_at=now, equity_after=Decimal("95000"), mode="paper"
        ),
        TradeRecord(
            trade_id="T3", setup_type="VPIN", symbol="AAPL", direction="LONG",
            entry_price=Decimal("150"), exit_price=Decimal("160"), quantity=Decimal("10"),
            risk_r=Decimal("1.0"), realized_r=Decimal("4.0"), pnl=Decimal("100"),
            opened_at=now, closed_at=now, equity_after=Decimal("95100"), mode="paper"
        ),
    ]

    snapshot = engine.compute_snapshot(trades, account, 100)

    assert snapshot.sample_size == 3
    assert snapshot.profit_factor == 6.0  # 6 / 1
    assert "VPIN" in snapshot.expectancy_by_setup
    assert snapshot.expectancy_by_setup["VPIN"] == Decimal("3.0") # (2.0 + 4.0) / 2
    # BUR check: limit is 10k. Used is 5k. BUR = 0.5
    assert snapshot.bur == 0.5
    assert snapshot.buffer_zone == "YELLOW"
