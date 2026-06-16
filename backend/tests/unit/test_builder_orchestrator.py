from decimal import Decimal

from backend.domain.builder_models import (
    BUILDER_DAILY_SOFT_PAUSE_THREAT,
    BUILDER_PHASE_MISMATCH,
    BUILDER_TRAILING_DD_CRITICAL,
    BuilderAccountState,
    MFFU_BUILDER_PROFILE_ID,
    mffu_builder_50k_profile,
)
from backend.domain.portfolio_risk_models import (
    AccountState,
    PortfolioRiskRequest,
    TradeCandidate,
)
from backend.services.builder_orchestrator import BuilderEvaluationInputs
from backend.services.consistency_rule_manager import ConsistencyRuleManager
from backend.services.convergence_gate import ConvergenceGate
from backend.services.funding_orchestrator import FundingOrchestrator
from backend.services.global_context_engine import GlobalContextEngine
from backend.services.performance_analytics_engine import PerformanceAnalyticsEngine
from backend.services.portfolio_risk_service import PortfolioRiskService
from backend.services.pre_market_check import PreMarketCheck, PreMarketDecision
from backend.services.predictive_risk_gate import PredictiveRiskGate
from backend.services.sizing_engine import SizingEngine

MOCK_CONTEXT = {"vix": 15.0, "spy": None, "qqq": None}


class MockPreMarket(PreMarketCheck):
    def evaluate(self, check_time=None):  # type: ignore[no-untyped-def]
        return PreMarketDecision(is_allowed=True, reason="Mock allow")


def _orchestrator() -> FundingOrchestrator:
    return FundingOrchestrator(
        portfolio_risk_svc=PortfolioRiskService(),
        perf_engine=PerformanceAnalyticsEngine(),
        global_ctx_engine=GlobalContextEngine(),
        convergence_gate=ConvergenceGate(),
        predictive_risk_gate=PredictiveRiskGate(),
        sizing_engine=SizingEngine(),
        consistency_mgr=ConsistencyRuleManager(),
        pre_market_check=MockPreMarket(),
    )


def _builder_request(candidate: TradeCandidate) -> PortfolioRiskRequest:
    account = AccountState(
        initial_capital=50000.0,
        current_equity=50000.0,
        start_of_day_balance=50000.0,
    )
    return PortfolioRiskRequest(
        account_state=account,
        preset=mffu_builder_50k_profile().to_funding_rule_preset(),
        candidates=[candidate],
    )


def test_builder_route_approves_candidate_with_contracts() -> None:
    orchestrator = _orchestrator()
    account = AccountState(
        initial_capital=50000.0,
        current_equity=50000.0,
        start_of_day_balance=50000.0,
    )
    candidate = TradeCandidate(
        symbol="MNQ",
        direction="LONG",
        entry=20000.0,
        stop=19997.5,
    )
    portfolio_req = _builder_request(candidate)

    result = orchestrator.evaluate_candidate(
        candidate=candidate,
        account=account,
        trades=[],
        context_data=MOCK_CONTEXT,
        portfolio_request=portfolio_req,
        builder_inputs=BuilderEvaluationInputs(prefer_micro=True),
    )

    assert result.is_allowed is True
    assert result.funding_profile == MFFU_BUILDER_PROFILE_ID
    assert result.contracts > 0
    assert result.position_notional == Decimal("0")


def test_builder_trailing_dd_critical_blocks_signal() -> None:
    orchestrator = _orchestrator()
    account = AccountState(
        initial_capital=50000.0,
        current_equity=47900.0,
        start_of_day_balance=50000.0,
        high_watermark_balance=50000.0,
    )
    candidate = TradeCandidate(symbol="MNQ", direction="LONG", entry=20000.0, stop=19997.5)
    state = BuilderAccountState(
        initial_capital=Decimal("50000"),
        current_equity=Decimal("47900"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
        phase="EVAL_ACTIVE",
    )

    result = orchestrator.evaluate_candidate(
        candidate=candidate,
        account=account,
        trades=[],
        context_data=MOCK_CONTEXT,
        portfolio_request=_builder_request(candidate),
        builder_inputs=BuilderEvaluationInputs(state=state, prefer_micro=True),
    )

    assert result.is_allowed is False
    assert BUILDER_TRAILING_DD_CRITICAL in result.reason_codes


def test_builder_soft_pause_blocks_new_entries() -> None:
    orchestrator = _orchestrator()
    account = AccountState(
        initial_capital=50000.0,
        current_equity=49000.0,
        start_of_day_balance=50000.0,
    )
    candidate = TradeCandidate(symbol="MNQ", direction="LONG", entry=20000.0, stop=19997.5)
    state = BuilderAccountState(
        initial_capital=Decimal("50000"),
        current_equity=Decimal("49000"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
        realized_daily_pnl=Decimal("-1000"),
        phase="EVAL_ACTIVE",
    )

    result = orchestrator.evaluate_candidate(
        candidate=candidate,
        account=account,
        trades=[],
        context_data=MOCK_CONTEXT,
        portfolio_request=_builder_request(candidate),
        builder_inputs=BuilderEvaluationInputs(state=state, prefer_micro=True),
    )

    assert result.is_allowed is False
    assert BUILDER_DAILY_SOFT_PAUSE_THREAT in result.reason_codes


def test_builder_phase_mismatch_blocks_signal() -> None:
    orchestrator = _orchestrator()
    account = AccountState(
        initial_capital=50000.0,
        current_equity=50000.0,
        start_of_day_balance=50000.0,
    )
    candidate = TradeCandidate(symbol="MNQ", direction="LONG", entry=20000.0, stop=19997.5)
    state = BuilderAccountState(phase="BREACHED")

    result = orchestrator.evaluate_candidate(
        candidate=candidate,
        account=account,
        trades=[],
        context_data=MOCK_CONTEXT,
        portfolio_request=_builder_request(candidate),
        builder_inputs=BuilderEvaluationInputs(state=state, prefer_micro=True),
    )

    assert result.is_allowed is False
    assert BUILDER_PHASE_MISMATCH in result.reason_codes


def test_ftmo_route_remains_default_for_non_builder_preset() -> None:
    orchestrator = _orchestrator()
    account = AccountState(
        initial_capital=100000.0,
        current_equity=100000.0,
        start_of_day_balance=100000.0,
    )
    candidate = TradeCandidate(symbol="AAPL", direction="LONG", entry=150.0, stop=145.0)
    from backend.domain.portfolio_risk_models import FundingRulePreset

    portfolio_req = PortfolioRiskRequest(
        account_state=account,
        preset=FundingRulePreset(id="ftmo_2_step"),
        candidates=[candidate],
    )

    result = orchestrator.evaluate_candidate(
        candidate=candidate,
        account=account,
        trades=[],
        context_data=MOCK_CONTEXT,
        portfolio_request=portfolio_req,
    )

    assert result.funding_profile == "ftmo"
    assert result.contracts == 0
