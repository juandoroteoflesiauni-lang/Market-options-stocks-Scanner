import random
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.portfolio_risk_models import AccountState
from backend.infrastructure.repositories.trade_history_repository import (
    TradeHistoryRepository,
)
from backend.models.risk_metrics_snapshot import RiskMetricsSnapshot
from backend.models.trade_record import TradeRecord
from backend.services.performance_analytics_engine import PerformanceAnalyticsEngine

router = APIRouter(prefix="/api/v1/funding", tags=["funding", "risk-metrics"])


def get_performance_engine() -> PerformanceAnalyticsEngine:
    return PerformanceAnalyticsEngine()


def get_trade_repo() -> TradeHistoryRepository:
    return TradeHistoryRepository()


@router.get("/risk-metrics", response_model=RiskMetricsSnapshot)
def get_risk_metrics(
    window: int = 100,
    engine: PerformanceAnalyticsEngine = Depends(get_performance_engine),  # noqa: B008
    repo: TradeHistoryRepository = Depends(get_trade_repo),  # noqa: B008
) -> RiskMetricsSnapshot:
    """Retrieve the latest risk metrics snapshot."""
    thresholds = FundingThresholds()

    # Placeholder for AccountState until we integrate with live portfolio DB.
    account_state = AccountState(
        initial_capital=float(thresholds.ftmo_initial_capital),
        current_equity=float(thresholds.ftmo_initial_capital),
        start_of_day_balance=float(thresholds.ftmo_initial_capital),
    )

    trades = repo.get_recent(window=window)
    return engine.compute_snapshot(trades, account_state, window=window)

@router.post("/mock-trade", response_model=RiskMetricsSnapshot)
def insert_mock_trade(
    engine: PerformanceAnalyticsEngine = Depends(get_performance_engine),  # noqa: B008
    repo: TradeHistoryRepository = Depends(get_trade_repo),  # noqa: B008
) -> RiskMetricsSnapshot:
    """Insert a random mock trade to test the performance engine dashboard."""
    pnl = Decimal(str(round(random.uniform(-500, 1500), 2)))
    trade = TradeRecord(
        trade_id=str(uuid.uuid4()),
        setup_type=random.choice(["VPIN", "OFI", "SMC", "GEX_SQUEEZE"]),
        symbol=random.choice(["AAPL", "TSLA", "MSFT", "NVDA", "SPY"]),
        direction=random.choice(["LONG", "SHORT"]),
        entry_price=Decimal("100.00"),
        exit_price=Decimal("105.00") if pnl > Decimal("0.0") else Decimal("95.00"),
        quantity=Decimal("10"),
        risk_r=Decimal("1.0"),
        realized_r=Decimal("1.5") if pnl > Decimal("0.0") else Decimal("-1.0"),
        pnl=pnl,
        opened_at=datetime.now(UTC),
        closed_at=datetime.now(UTC),
        equity_after=Decimal("100000.00") + pnl,
        mode="paper",
    )
    repo.save(trade)

    # Synchronize with the BuilderStateStore
    from backend.services.builder_state_store import BuilderStateStore
    store = BuilderStateStore()
    state = store.load_state("default")

    new_equity = state.current_equity + pnl
    new_hwm = state.high_watermark_balance
    if new_hwm is None or new_equity > new_hwm:
        new_hwm = new_equity

    updated_state = state.model_copy(
        update={
            "current_equity": new_equity,
            "high_watermark_balance": new_hwm,
        }
    )
    store.save_state(updated_state)

    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    store.record_daily_pnl(today_str, pnl)

    # Return updated metrics using the fresh builder state
    account_state = AccountState(
        initial_capital=float(updated_state.initial_capital),
        current_equity=float(updated_state.current_equity),
        start_of_day_balance=float(updated_state.start_of_day_balance),
    )
    trades = repo.get_recent(window=100)
    return engine.compute_snapshot(trades, account_state, window=100)


