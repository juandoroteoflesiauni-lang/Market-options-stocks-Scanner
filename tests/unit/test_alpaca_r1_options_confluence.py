"""Tests confluencia de opciones Ruta 1 Alpaca. # [TH][IM]"""

from __future__ import annotations

from backend.domain.alpaca_models import AlpacaCandidateAnalysis, AlpacaDecision
from backend.domain.alpaca_options_models import (
    OptionsConfluence,
    OptionsEngineSignal,
)
from backend.services.alpaca_decision_engine import decide
from backend.services.alpaca_r1_options_confluence import (
    OptionsConfluenceScorer,
    apply_equity_options_confluence_gate,
)
from backend.services.alpaca_r1_options_replay import (
    AlpacaR1OptionsReplay,
    _merge_signals,
    _motor_direction,
    _signal_from_result,
)
from backend.config.alpaca_r1_options_scoring_config import (
    REASON_OPTIONS_CONFLUENCE_DISTRIBUTION,
)


def _bullish_signal(engine: str, family: str) -> OptionsEngineSignal:
    return OptionsEngineSignal(
        engine=engine,
        family=family,  # type: ignore[arg-type]
        direction="BULL",
        score=0.85,
        detail={},
    )


def _bearish_signal(engine: str, family: str) -> OptionsEngineSignal:
    return OptionsEngineSignal(
        engine=engine,
        family=family,  # type: ignore[arg-type]
        direction="BEAR",
        score=0.80,
        detail={},
    )


def test_options_engine_signal_frozen() -> None:
  sig = OptionsEngineSignal(
      engine="delta_rsi",
      family="momentum",
      direction="BULL",
      score=0.7,
  )
  copy = sig.model_copy(update={"score": 0.8})
  assert copy.score == 0.8
  assert sig.score == 0.7


def test_motor_direction_maps_long_and_short() -> None:
    assert _motor_direction("LONG") == "BULL"
    assert _motor_direction("REVERSAL_SHORT") == "BEAR"
    assert _motor_direction("NEUTRAL") == "NEUTRAL"


def test_signal_from_result_neutral_when_missing() -> None:
    sig = _signal_from_result("bb_gex", None)
    assert sig.direction == "NEUTRAL"
    assert sig.score == 0.0


def test_merge_signals_averages_scores() -> None:
    merged = _merge_signals(
        "vidya_iv_gamma",
        [{"signal": "LONG", "score": 0.8}, {"signal": "LONG", "score": 0.6}],
    )
    assert merged.direction == "BULL"
    assert merged.score == 0.7


def test_scorer_bullish_confluence_high_score() -> None:
    signals = [
        _bullish_signal("delta_rsi", "momentum"),
        _bullish_signal("shadow_macd", "momentum"),
        _bullish_signal("vidya_iv_gamma", "momentum"),
        _bullish_signal("cvd_ndde_gamma", "volume"),
        _bullish_signal("volume_profile_oi", "volume"),
        _bullish_signal("bb_gex", "structure"),
        _bullish_signal("sma_gamma", "structure"),
        _bullish_signal("hybrid_ribbon", "structure"),
    ]
    conf = OptionsConfluenceScorer.score(signals)
    assert conf is not None
    assert conf.score >= 0.75
    assert conf.dominant_direction == "BULL"
    assert not conf.moderate


def test_scorer_bearish_distribution_flags_moderate() -> None:
    signals = [
        _bearish_signal("delta_rsi", "momentum"),
        _bearish_signal("shadow_macd", "momentum"),
        _bearish_signal("vidya_iv_gamma", "momentum"),
        _bearish_signal("cvd_ndde_gamma", "volume"),
        _bearish_signal("volume_profile_oi", "volume"),
        _bearish_signal("bb_gex", "structure"),
        _bearish_signal("sma_gamma", "structure"),
        _bearish_signal("hybrid_ribbon", "structure"),
    ]
    conf = OptionsConfluenceScorer.score(signals)
    assert conf is not None
    assert conf.dominant_direction == "BEAR"
    assert conf.moderate


def test_confluence_gate_size_down_on_moderate() -> None:
    conf = OptionsConfluence(
        score=0.2,
        by_family={"momentum": 0.1, "volume": 0.1, "structure": 0.1},
        by_engine={},
        dominant_direction="BEAR",
        moderate=True,
        reason_codes=("options_confluence_bear",),
    )
    decision = AlpacaDecision(
        symbol="NVDA",
        decision="ALLOW",
        direction="LONG",
        score=0.8,
        probability=0.75,
        route="priority",
    )
    gated = apply_equity_options_confluence_gate(decision, conf)
    assert gated.decision == "SIZE_DOWN"
    assert REASON_OPTIONS_CONFLUENCE_DISTRIBUTION in gated.reason_codes


def test_confluence_gate_passthrough_when_no_data() -> None:
    decision = AlpacaDecision(
        symbol="NVDA",
        decision="ALLOW",
        direction="LONG",
        score=0.8,
        probability=0.75,
        route="priority",
    )
    assert apply_equity_options_confluence_gate(decision, None) is decision


def test_replay_empty_without_context() -> None:
    klines = [{"open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 100}] * 10
    assert AlpacaR1OptionsReplay.run(klines, None) == []


def test_decide_blends_route1_options_confluence() -> None:
    conf = OptionsConfluence(
        score=0.9,
        by_family={"momentum": 0.9, "volume": 0.9, "structure": 0.9},
        by_engine={},
        dominant_direction="BULL",
        reason_codes=("options_confluence_bull",),
    )
    analysis = AlpacaCandidateAnalysis(
        symbol="AAPL",
        timestamp="2026-06-12T12:00:00Z",
        latest_close=150.0,
        atr=2.0,
        macd_histogram=0.5,
        relative_strength=0.02,
        volume_z_score=2.0,
        close_position_in_range=0.85,
        technical_ok=True,
        route="priority",
        options_confluence=conf,
    )
    decision = decide(analysis)
    assert decision.route == "priority"
    assert decision.score > 0.7
    assert "options_confluence_bull" in decision.reason_codes
