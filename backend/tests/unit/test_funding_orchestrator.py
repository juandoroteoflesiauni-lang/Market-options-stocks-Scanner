
from backend.domain.portfolio_risk_models import (
    AccountState,
    FundingRulePreset,
    PortfolioRiskRequest,
    TradeCandidate,
)
from backend.services.consistency_rule_manager import ConsistencyRuleManager
from backend.services.convergence_gate import ConvergenceGate
from backend.services.funding_orchestrator import FundingOrchestrator
from backend.services.global_context_engine import GlobalContextEngine
from backend.services.performance_analytics_engine import PerformanceAnalyticsEngine
from backend.services.portfolio_risk_service import PortfolioRiskService
from backend.services.pre_market_check import PreMarketCheck
from backend.services.predictive_risk_gate import PredictiveRiskGate
from backend.services.sizing_engine import SizingEngine

# A mock context for testing
MOCK_CONTEXT = {
    "vix": 15.0,
    "spy": None,  # Will fallback to NEUTRAL
    "qqq": None,
}


def test_funding_orchestrator() -> None:
    # 1. Init all services
    portfolio_svc = PortfolioRiskService()
    perf_engine = PerformanceAnalyticsEngine()
    global_ctx = GlobalContextEngine()
    conv_gate = ConvergenceGate()
    pred_gate = PredictiveRiskGate()
    sizing_engine = SizingEngine()
    consist_mgr = ConsistencyRuleManager()
    pre_market = PreMarketCheck()

    # Stub pre-market to always allow for tests to run regardless of the day
    # Actually, let's just make it a real test, if it fails on weekend we mock it.
    # To be safe, we will pass a valid context if we could, but pre_market uses datetime.now
    # We will override the method for testing
    class MockPreMarket(PreMarketCheck):
        def evaluate(self, check_time=None):  # type: ignore[no-untyped-def]
            from backend.services.pre_market_check import PreMarketDecision
            return PreMarketDecision(is_allowed=True, reason="Mock allow")

    pre_market = MockPreMarket()

    orchestrator = FundingOrchestrator(
        portfolio_risk_svc=portfolio_svc,
        perf_engine=perf_engine,
        global_ctx_engine=global_ctx,
        convergence_gate=conv_gate,
        predictive_risk_gate=pred_gate,
        sizing_engine=sizing_engine,
        consistency_mgr=consist_mgr,
        pre_market_check=pre_market,
    )

    account = AccountState(
        initial_capital=100000.0,
        current_equity=100000.0,
        start_of_day_balance=100000.0,
    )

    candidate = TradeCandidate(
        symbol="AAPL",
        direction="LONG",
        entry=150.0,
        stop=145.0,
    )

    portfolio_req = PortfolioRiskRequest(
        account_state=account,
        preset=FundingRulePreset(id="ftmo_2_step"),
        trade_history=[],
        positions=[],
        realized_daily_pnl=0.0,
        unrealized_pnl=0.0,
        returns_pct=[],
        candidates=[candidate],
    )

    res = orchestrator.evaluate_candidate(
        candidate=candidate,
        account=account,
        trades=[],
        context_data=MOCK_CONTEXT,
        portfolio_request=portfolio_req,
    )

    # With no past trades, kelly applied will be 0.0, so the base risk becomes 0.0
    # and it gets capped by kelly. Let's see.
    assert not res.is_allowed
    assert "kelly" in res.reason or "invalid" in res.reason or "Capped" in res.reason
