from datetime import datetime, timezone
from decimal import Decimal
import pytest
from backend.domain.builder_models import MFFU_BUILDER_PROFILE_ID, mffu_builder_50k_profile
from backend.domain.portfolio_risk_models import AccountState, PortfolioRiskRequest
from backend.models import CanonicalLegSpec, CanonicalSignalPayload
from backend.services.builder_orchestrator import BuilderEvaluationInputs
from backend.services.consistency_rule_manager import ConsistencyRuleManager
from backend.services.convergence_gate import ConvergenceGate
from backend.services.funding_orchestrator import FundingOrchestrator, candidate_from_canonical
from backend.services.global_context_engine import GlobalContextEngine
from backend.services.performance_analytics_engine import PerformanceAnalyticsEngine
from backend.services.portfolio_risk_service import PortfolioRiskService
from backend.services.pre_market_check import PreMarketCheck, PreMarketDecision
from backend.services.predictive_risk_gate import PredictiveRiskGate
from backend.services.sizing_engine import SizingEngine

MOCK_CONTEXT = {
    "vix": 15.0,
    "spy": None,
    "qqq": None,
    "bid_ask_spread_pct": 2.0,
    "max_bid_ask_spread_pct": 8.0,
}


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


def test_candidate_from_canonical_conversion() -> None:
    # ARRANGE
    payload = CanonicalSignalPayload(
        symbol="SPY",
        asset_type="option",
        direction="bullish",
        confidence=0.75,
        entry_price=Decimal("400.00"),
        stop_loss_price=Decimal("390.00"),
        max_loss_usd=Decimal("200.00"),
        structure="long_call",
        legs=(),
        timestamp=datetime.now(timezone.utc),
        reason_codes=("smc_bullish_alignment",),
    )

    # ACT
    candidate = candidate_from_canonical(payload)

    # ASSERT
    assert candidate.symbol == "SPY"
    assert candidate.direction == "LONG"
    assert candidate.entry == 400.00
    assert candidate.stop == 390.00
    assert candidate.confidence == 0.75
    assert candidate.source_module == "omni_engine"
    assert "smc_bullish_alignment" in candidate.evidence_by_module.get("reason_codes", [])


def test_evaluate_canonical_future_signal() -> None:
    # ARRANGE
    orchestrator = _orchestrator()
    payload = CanonicalSignalPayload(
        symbol="MNQ",
        asset_type="future",
        direction="bullish",
        confidence=0.8,
        entry_price=Decimal("20000.00"),
        stop_loss_price=Decimal("19997.50"),
        max_loss_usd=Decimal("50.00"),
        structure="linear",
        timestamp=datetime.now(timezone.utc),
    )
    account = AccountState(
        initial_capital=50000.0,
        current_equity=50000.0,
        start_of_day_balance=50000.0,
    )
    portfolio_req = PortfolioRiskRequest(
        account_state=account,
        preset=mffu_builder_50k_profile().to_funding_rule_preset(),
        candidates=[candidate_from_canonical(payload)],
    )

    # ACT
    result = orchestrator.evaluate_canonical(
        payload=payload,
        account=account,
        trades=[],
        context_data=MOCK_CONTEXT,
        portfolio_request=portfolio_req,
        builder_inputs=BuilderEvaluationInputs(prefer_micro=True),
    )

    # ASSERT
    assert result.is_allowed is True
    assert result.funding_profile == MFFU_BUILDER_PROFILE_ID
    assert result.contracts > 0


def test_evaluate_canonical_option_signal() -> None:
    # ARRANGE
    orchestrator = _orchestrator()
    leg = CanonicalLegSpec(contract_symbol="SPY260619C00400000", side="buy", ratio=1)
    payload = CanonicalSignalPayload(
        symbol="SPY",
        asset_type="option",
        direction="bullish",
        confidence=0.8,
        entry_price=Decimal("415.50"),
        max_loss_usd=Decimal("100.00"),  # premium
        structure="long_call",
        legs=(leg,),
        timestamp=datetime.now(timezone.utc),
    )
    account = AccountState(
        initial_capital=50000.0,
        current_equity=50000.0,
        start_of_day_balance=50000.0,
    )
    portfolio_req = PortfolioRiskRequest(
        account_state=account,
        preset=mffu_builder_50k_profile().to_funding_rule_preset(),
        candidates=[candidate_from_canonical(payload)],
    )

    # ACT
    result = orchestrator.evaluate_canonical(
        payload=payload,
        account=account,
        trades=[],
        context_data=MOCK_CONTEXT,
        portfolio_request=portfolio_req,
    )

    # ASSERT
    assert result.is_allowed is True
    assert result.funding_profile == MFFU_BUILDER_PROFILE_ID
    # Premium is 100 USD. Bid-ask spread: 2.0% of max 8.0% -> penalty: 25% -> adjusted risk: 250 USD * 0.75 = 187.50 USD.
    # 187.50 / 100.00 = 1.875 -> floor is 1 contract. Available cap is 4.
    assert result.contracts == 1
    assert result.risk_used_usd == Decimal("100.00")
