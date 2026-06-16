"""Fase 2 — capas técnica y predictiva Options Strategy. # [TH][IM]"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.models.market_snapshot import DataLineage, MarketSnapshot, OHLCVBar
from backend.models.options_strategy import (
    OptionsStrategyInput,
    merge_layer_features,
)
from backend.services.options_strategy.predictive_layer import PredictiveLayer
from backend.services.options_strategy.technical_layer import TechnicalLayer


def _lineage() -> DataLineage:
    return DataLineage(source="test", ingestion_latency_ms=1, raw_field_count=3)


def _ohlcv_bars(n: int = 40, *, uptrend: bool = True) -> tuple[OHLCVBar, ...]:
    base = 180.0
    bars: list[OHLCVBar] = []
    for i in range(n):
        close = Decimal(str(base + i * 0.35 if uptrend else base - i * 0.35))
        open_ = close - Decimal("0.10")
        high = close + Decimal("0.30")
        low = close - Decimal("0.30")
        bars.append(
            OHLCVBar(
                time=f"2026-06-13T14:{i:02d}:00Z",
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=Decimal("100000") + Decimal(str(i * 1000)),
            )
        )
    return tuple(bars)


def _make_input(
    symbol: str = "AAPL",
    *,
    bars: int = 40,
    uptrend: bool = True,
) -> OptionsStrategyInput:
    ohlcv = _ohlcv_bars(bars, uptrend=uptrend)
    snap = MarketSnapshot(
        ticker=symbol,
        exchange="NASDAQ",
        price=ohlcv[-1].close,
        volume=1_000_000,
        exchange_timestamp=datetime.now(tz=UTC),
        data_lineage=_lineage(),
        ohlcv=ohlcv,
    )
    return OptionsStrategyInput(
        symbol=symbol,
        as_of=datetime.now(tz=UTC),
        market_snapshot=snap,
    )


def test_technical_layer_insufficient_bars_returns_neutral() -> None:
    inp = _make_input(bars=5)
    out = TechnicalLayer.run(inp)
    assert out.insufficient_data is True
    assert out.technical_direction_bias == 0.0


def test_technical_layer_uptrend_produces_scores_in_range() -> None:
    inp = _make_input(bars=45, uptrend=True)
    out = TechnicalLayer.run(inp)
    assert out.insufficient_data is False
    assert -1.0 <= out.technical_direction_bias <= 1.0
    assert 0.0 <= out.trend_quality_score <= 1.0
    assert 0.0 <= out.liquidity_location_score <= 1.0
    assert 0.0 <= out.reversal_risk_score <= 1.0
    assert 0.0 <= out.structure_alignment_score <= 1.0
    assert out.engine_scores


def test_predictive_layer_insufficient_bars_returns_neutral() -> None:
    inp = _make_input(bars=5)
    out = PredictiveLayer.run(inp)
    assert out.insufficient_data is True
    assert out.predictive_direction_bias == 0.0


def test_predictive_layer_uptrend_produces_scores_in_range() -> None:
    inp = _make_input(bars=45, uptrend=True)
    out = PredictiveLayer.run(inp)
    assert out.insufficient_data is False
    assert -1.0 <= out.predictive_direction_bias <= 1.0
    assert -1.0 <= out.macro_alignment_score <= 1.0
    assert 0.0 <= out.expected_move_confidence <= 1.0
    assert 0.0 <= out.left_tail_risk_score <= 1.0
    assert 0.0 <= out.right_tail_risk_score <= 1.0
    assert 0.0 <= out.forecast_dispersion_score <= 1.0
    assert out.expected_move_pct >= 0.0
    assert "markov_regime_engine" in out.engine_scores
    assert "expected_move_engine" in out.engine_scores
    assert "fear_greed_engine" in out.engine_scores


def test_merge_layer_features_combines_outputs() -> None:
    inp = _make_input()
    tech = TechnicalLayer.run(inp)
    pred = PredictiveLayer.run(inp)
    merged = merge_layer_features(tech, pred)
    assert merged.symbol == "AAPL"
    assert merged.technical_direction_bias == tech.technical_direction_bias
    assert merged.predictive_direction_bias == pred.predictive_direction_bias
    assert merged.regime_class == pred.regime_class
    assert merged.breakout_state == tech.breakout_state


def test_merge_layer_features_rejects_symbol_mismatch() -> None:
    inp_a = _make_input(symbol="AAPL")
    inp_q = _make_input(symbol="QQQ")
    tech = TechnicalLayer.run(inp_a)
    pred = PredictiveLayer.run(inp_q)
    with pytest.raises(ValueError, match="symbols must match"):
        merge_layer_features(tech, pred)


def test_layers_only_accept_route1_symbols() -> None:
    with pytest.raises(ValueError, match="symbol_not_in_route1_universe"):
        OptionsStrategyInput(symbol="AMD", as_of=datetime.now(tz=UTC))
