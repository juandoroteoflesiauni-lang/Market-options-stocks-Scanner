"""AAA unit tests for backend.services.alpaca_decision_engine. # [TH][IM]"""

from __future__ import annotations

from backend.domain.alpaca_models import AlpacaCandidateAnalysis
from backend.services.alpaca_decision_engine import AlpacaDecisionConfig, decide


def _analysis(**overrides: object) -> AlpacaCandidateAnalysis:
    base: dict[str, object] = {
        "symbol": "AAPL",
        "timestamp": "2026-06-12T00:00:00Z",
        "latest_close": 190.0,
        "atr": 3.0,
        "macd_histogram": 0.5,
        "relative_strength": 2.0,
        "volume_z_score": 2.5,
        "close_position_in_range": 0.85,
        "technical_ok": True,
    }
    base.update(overrides)
    return AlpacaCandidateAnalysis(**base)  # type: ignore[arg-type]


def test_decide_allows_long_on_strong_bullish_confluence() -> None:
    # ARRANGE
    analysis = _analysis()
    # ACT
    decision = decide(analysis, AlpacaDecisionConfig())
    # ASSERT
    assert decision.decision == "ALLOW"
    assert decision.direction == "LONG"


def test_decide_blocks_when_not_bullish() -> None:
    # ARRANGE
    analysis = _analysis(macd_histogram=-0.5, close_position_in_range=0.3)
    # ACT
    decision = decide(analysis, AlpacaDecisionConfig())
    # ASSERT
    assert decision.decision == "BLOCK"
    assert decision.direction == "FLAT"


def test_decide_returns_insufficient_data_without_technical() -> None:
    # ARRANGE
    analysis = _analysis(technical_ok=False, latest_close=None)
    # ACT
    decision = decide(analysis, AlpacaDecisionConfig())
    # ASSERT
    assert decision.decision == "INSUFFICIENT_DATA"


def test_decide_size_down_on_marginal_probability() -> None:
    # ARRANGE: bullish but weak features → probability lands in size-down band
    analysis = _analysis(
        volume_z_score=1.0,
        close_position_in_range=0.60,
        relative_strength=-1.0,
        macd_histogram=0.01,
    )
    config = AlpacaDecisionConfig(prob_floor=0.72, size_down_band=0.03)
    # ACT
    decision = decide(analysis, config)
    # ASSERT
    assert decision.decision == "SIZE_DOWN"
    assert decision.direction == "LONG"
