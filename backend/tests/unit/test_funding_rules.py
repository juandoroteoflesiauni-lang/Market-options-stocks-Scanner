from decimal import Decimal

from backend.domain.portfolio_risk_models import AccountState
from backend.models.global_context_snapshot import GlobalContextSnapshot
from backend.services.consistency_rule_manager import ConsistencyRuleManager
from backend.services.convergence_gate import ConvergenceGate
from backend.services.daily_budget_guard import DailyBudgetGuard
from backend.services.pre_market_check import PreMarketCheck
from backend.services.trailing_mll_simulator import TrailingMLLSimulator
from backend.models.trade_record import TradeRecord
from datetime import datetime, timezone


def test_convergence_gate() -> None:
    gate = ConvergenceGate()
    context = GlobalContextSnapshot(is_valid=True, market_regime="MELTDOWN")
    decision = gate.evaluate("LONG", context)
    assert not decision.is_allowed
    assert decision.conviction_multiplier == Decimal("0.0")

    context_bull = GlobalContextSnapshot(is_valid=True, market_regime="BULL")
    decision_bull = gate.evaluate("SHORT", context_bull)
    assert decision_bull.is_allowed
    assert decision_bull.conviction_multiplier == Decimal("0.5")


def test_daily_budget_guard() -> None:
    guard = DailyBudgetGuard()
    account = AccountState(
        initial_capital=100000.0,
        current_equity=100000.0,
        start_of_day_balance=100000.0,
    )
    # Default is 5% -> $5000
    decision = guard.evaluate(account, -1000.0)
    assert decision.is_allowed
    # Remaining: $4000 -> 4.0%
    assert decision.remaining_daily_risk_pct == Decimal("4.0")

    decision_breach = guard.evaluate(account, -5001.0)
    assert not decision_breach.is_allowed
    assert decision_breach.remaining_daily_risk_pct == Decimal("0.0")


def test_trailing_mll_simulator() -> None:
    simulator = TrailingMLLSimulator()
    # Default is 10% -> $10000 max loss
    account = AccountState(
        initial_capital=100000.0,
        current_equity=95000.0,
        start_of_day_balance=95000.0,
    )
    # Limit is 90000. Equity is 95000. Remaining is 5000 -> 5.0%
    remaining_pct = simulator.get_remaining_max_risk_pct(account)
    assert remaining_pct == Decimal("5.0")


def test_consistency_rule_manager() -> None:
    manager = ConsistencyRuleManager()
    now = datetime.now(timezone.utc)
    
    trades = [
        TradeRecord(
            trade_id="T1", setup_type="VPIN", symbol="AAPL", direction="LONG",
            entry_price=Decimal("150"), exit_price=Decimal("160"), quantity=Decimal("10"),
            risk_r=Decimal("1.0"), realized_r=Decimal("2.0"), pnl=Decimal("600"),
            opened_at=now, closed_at=now, equity_after=Decimal("100600"), mode="paper"
        ),
        TradeRecord(
            trade_id="T2", setup_type="OFI", symbol="MSFT", direction="SHORT",
            entry_price=Decimal("300"), exit_price=Decimal("290"), quantity=Decimal("10"),
            risk_r=Decimal("1.0"), realized_r=Decimal("1.0"), pnl=Decimal("400"),
            opened_at=now, closed_at=now, equity_after=Decimal("101000"), mode="paper"
        )
    ]
    
    # Both trades on same day -> 1000 total. Best day is 1000 -> 100% (violates cap)
    decision = manager.evaluate(trades)
    assert not decision.is_allowed
    assert decision.best_day_ratio == Decimal("1.0")


def test_pre_market_check() -> None:
    checker = PreMarketCheck()
    
    # Monday
    dt_monday = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    decision = checker.evaluate(dt_monday)
    assert decision.is_allowed
    
    # Saturday
    dt_saturday = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    decision_sat = checker.evaluate(dt_saturday)
    assert not decision_sat.is_allowed
