"""AAA unit tests for backend.services.alpaca_risk_desk. # [TH][IM]"""

from __future__ import annotations

import pytest

from backend.domain.alpaca_models import AlpacaCandidateAnalysis, AlpacaDecision
from backend.services.alpaca_pre_trade_risk_gate import PreTradeRiskGate
from backend.services.alpaca_risk_desk import (
    REASON_POSITION_ALREADY_OPEN,
    AlpacaRiskDesk,
    AlpacaRiskPolicy,
    compute_bracket_levels,
)


@pytest.fixture(autouse=True)
def _reset_pre_trade_gate() -> None:
    PreTradeRiskGate.reset_instance()
    gate = PreTradeRiskGate.instance()
    gate.update_bur(0.0)


def _analysis(price: float = 100.0, atr: float | None = 4.0) -> AlpacaCandidateAnalysis:
    return AlpacaCandidateAnalysis(
        symbol="AAPL",
        timestamp="2026-06-12T00:00:00Z",
        latest_close=price,
        atr=atr,
        technical_ok=True,
    )


def _decision(suitability: str = "ALLOW") -> AlpacaDecision:
    return AlpacaDecision(
        symbol="AAPL", decision=suitability, direction="LONG", score=0.8  # type: ignore[arg-type]
    )


def test_compute_bracket_levels_uses_atr_multiples() -> None:
    # ARRANGE / ACT
    stop, take = compute_bracket_levels(100.0, 4.0, 1.5, 2.5)
    # ASSERT
    assert stop == 94.0  # 100 - 1.5*4
    assert take == 110.0  # 100 + 2.5*4


def test_build_intent_sizes_whole_shares_by_notional() -> None:
    # ARRANGE
    desk = AlpacaRiskDesk(AlpacaRiskPolicy(notional_per_trade_usd=1000.0))
    # ACT
    intent = desk.build_intent(_decision(), _analysis(price=190.0), cycle_id="c1")
    # ASSERT
    assert intent is not None
    assert intent is not None
    assert intent is not None
    assert 4 <= intent.quantity <= 5
    assert intent.side == "BUY"


def test_build_intent_rejects_when_quantity_zero() -> None:
    # ARRANGE: price above the budget → floor(notional/price) == 0
    desk = AlpacaRiskDesk(AlpacaRiskPolicy(notional_per_trade_usd=100.0))
    # ACT
    intent = desk.build_intent(_decision(), _analysis(price=500.0), cycle_id="c1")
    # ASSERT
    assert intent is None


def test_authorize_intent_blocks_when_position_open() -> None:
    # ARRANGE
    desk = AlpacaRiskDesk(AlpacaRiskPolicy())
    desk.open_positions["AAPL"] = 10.0
    intent = desk.build_intent(_decision(), _analysis(price=190.0), cycle_id="c1")
    assert intent is not None
    # ACT
    decision = desk.authorize_intent(intent)
    # ASSERT
    assert decision.authorized is False
    assert REASON_POSITION_ALREADY_OPEN in decision.reason_codes


def test_authorize_intent_authorizes_fresh_symbol() -> None:
    # ARRANGE
    desk = AlpacaRiskDesk(AlpacaRiskPolicy())
    intent = desk.build_intent(_decision(), _analysis(price=190.0), cycle_id="c1")
    assert intent is not None
    # ACT
    decision = desk.authorize_intent(intent)
    # ASSERT
    assert decision.authorized is True
    assert decision.adjusted_quantity == intent.quantity
