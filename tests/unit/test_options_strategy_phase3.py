"""Fase 3 — capa de opciones, selector de contratos y estructura. # [TH][IM]"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from backend.domain.alpaca_options_models import Route1OptionsSnapshotContext
from backend.models.market_snapshot import DataLineage, MarketSnapshot, OHLCVBar
from backend.models.options_strategy import (
    OptionsStrategyInput,
    OptionsStructure,
    merge_all_layer_features,
)
from backend.services.options_strategy.contract_selector import ContractSelector
from backend.services.options_strategy.options_layer import OptionsLayer
from backend.services.options_strategy.predictive_layer import PredictiveLayer
from backend.services.options_strategy.structure_selector import StructureSelector
from backend.services.options_strategy.technical_layer import TechnicalLayer


def _lineage() -> DataLineage:
    return DataLineage(source="test", ingestion_latency_ms=1, raw_field_count=3)


def _ohlcv_bars(n: int = 40, *, uptrend: bool = True) -> tuple[OHLCVBar, ...]:
    base = 175.0
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


def _googl_chain(*, expiry: date) -> list[dict]:
    spot = 175.0
    exp = expiry.isoformat()
    strikes = [165.0, 170.0, 175.0, 180.0, 185.0, 190.0]
    rows: list[dict] = []
    for strike in strikes:
        moneyness = (strike - spot) / spot
        call_delta = max(0.05, min(0.95, 0.5 - moneyness * 2.5))
        put_delta = call_delta - 1.0
        call_oi = 1200 if strike in {175.0, 180.0} else 800
        put_oi = 1100 if strike in {170.0, 175.0} else 700
        call_mark = max(0.5, (spot - strike) * 0.4 + 3.0) if strike <= spot else 2.5
        put_mark = max(0.5, (strike - spot) * 0.4 + 3.0) if strike >= spot else 2.5
        rows.append(
            {
                "strike": strike,
                "expiration": exp,
                "call_oi": call_oi,
                "put_oi": put_oi,
                "call_volume": 250,
                "put_volume": 180,
                "call_delta": round(call_delta, 3),
                "put_delta": round(put_delta, 3),
                "call_gamma": 0.02,
                "put_gamma": 0.02,
                "call_iv": 0.28,
                "put_iv": 0.29,
                "call_mark": round(call_mark, 2),
                "put_mark": round(put_mark, 2),
                "call_contract_ticker": f"GOOGL{exp.replace('-', '')}C{int(strike * 1000):08d}",
                "put_contract_ticker": f"GOOGL{exp.replace('-', '')}P{int(strike * 1000):08d}",
            }
        )
    return rows


def _make_googl_input(
    *,
    bars: int = 45,
    with_chain: bool = True,
    iv_atm: float = 0.28,
) -> OptionsStrategyInput:
    as_of = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)
    ohlcv = _ohlcv_bars(bars, uptrend=True)
    snap = MarketSnapshot(
        ticker="GOOGL",
        exchange="NASDAQ",
        price=ohlcv[-1].close,
        volume=2_000_000,
        exchange_timestamp=as_of,
        data_lineage=_lineage(),
        ohlcv=ohlcv,
    )
    expiry = (as_of.date() + timedelta(days=14))
    options_ctx = None
    if with_chain:
        options_ctx = Route1OptionsSnapshotContext(
            symbol="GOOGL",
            as_of=as_of.isoformat(),
            available=True,
            snapshot={
                "spot": float(snap.price),
                "iv_surface": {"atm_iv": iv_atm},
                "chain": _googl_chain(expiry=expiry),
            },
        )
    return OptionsStrategyInput(
        symbol="GOOGL",
        as_of=as_of,
        market_snapshot=snap,
        options_context=options_ctx,
    )


def test_options_layer_without_context_is_neutral() -> None:
    inp = _make_googl_input(with_chain=False)
    out = OptionsLayer.run(inp)
    assert out.insufficient_data is True
    assert out.options_direction_bias == 0.0
    assert out.structure_preference == OptionsStructure.NO_TRADE


def test_options_layer_with_chain_produces_scores() -> None:
    inp = _make_googl_input()
    out = OptionsLayer.run(inp)
    assert out.insufficient_data is False
    assert -1.0 <= out.options_direction_bias <= 1.0
    assert out.iv_state in {"cheap", "fair", "rich", "extreme", "unknown"}
    assert 0.0 <= out.flow_conviction_score <= 1.0
    assert 0.0 <= out.chain_liquidity_score <= 1.0
    assert out.engine_scores


def test_contract_selector_long_call_picks_delta_target() -> None:
    inp = _make_googl_input()
    legs = ContractSelector.select(inp, OptionsStructure.LONG_CALL)
    assert len(legs) == 1
    leg = legs[0]
    assert leg.right == "call"
    assert leg.side == "long"
    assert leg.open_interest >= 500
    assert leg.mark is not None and leg.mark > 0
    assert leg.dte >= 7


def test_contract_selector_call_debit_spread_has_two_legs() -> None:
    inp = _make_googl_input()
    legs = ContractSelector.select(inp, OptionsStructure.CALL_DEBIT_SPREAD)
    assert len(legs) == 2
    long_leg, short_leg = legs
    assert long_leg.side == "long" and short_leg.side == "short"
    assert long_leg.strike < short_leg.strike


def test_structure_selector_dry_run_googl_candidate() -> None:
    inp = _make_googl_input(iv_atm=0.42)
    tech = TechnicalLayer.run(inp)
    pred = PredictiveLayer.run(inp)
    options = OptionsLayer.run(inp)
    features = merge_all_layer_features(tech, pred, options)
    candidate = StructureSelector.build_candidate(inp, features, options)
    assert candidate.symbol == "GOOGL"
    assert candidate.selection.structure in {
        OptionsStructure.LONG_CALL,
        OptionsStructure.CALL_DEBIT_SPREAD,
        OptionsStructure.NO_TRADE,
    }
    if candidate.selection.structure != OptionsStructure.NO_TRADE:
        assert len(candidate.legs) >= 1
        assert candidate.max_loss is not None


def test_r2_config_uses_r2_basic_structure_profile() -> None:
    from backend.config.alpaca_options_route_config import get_options_config_for_route

    cfg = get_options_config_for_route("scan", r2_symbols=("COIN",))
    assert cfg.structure_profile == "r2_basic"


def test_contract_selector_short_put_and_put_credit_spread() -> None:
    inp = _make_googl_input()
    short_legs = ContractSelector.select(inp, OptionsStructure.SHORT_PUT)
    assert len(short_legs) == 1
    assert short_legs[0].right == "put"
    assert short_legs[0].side == "short"

    credit_legs = ContractSelector.select(inp, OptionsStructure.PUT_CREDIT_SPREAD)
    assert len(credit_legs) == 2
    short_leg, long_leg = credit_legs
    assert short_leg.side == "short" and long_leg.side == "long"
    assert short_leg.strike > long_leg.strike


def test_structure_selector_r2_basic_profile() -> None:
    from backend.config.options_strategy_loader import get_options_strategy_config
    from backend.services.options_strategy.pipeline import _neutral_options, _neutral_predictive

    inp = _make_googl_input()
    tech = TechnicalLayer.run(inp)
    pred = _neutral_predictive(inp)
    options = _neutral_options(inp)
    features = merge_all_layer_features(tech, pred, options)
    features = features.model_copy(
        update={
            "technical_direction_bias": 0.35,
            "trend_quality_score": 0.60,
            "structure_alignment_score": 0.50,
        }
    )
    cfg = get_options_strategy_config().model_copy(update={"structure_profile": "r2_basic"})
    candidate = StructureSelector.build_candidate(inp, features, options, config=cfg)
    assert candidate.selection.structure == OptionsStructure.SHORT_PUT
    assert len(candidate.legs) >= 1


def test_merge_all_layer_features_includes_options_fields() -> None:
    inp = _make_googl_input()
    tech = TechnicalLayer.run(inp)
    pred = PredictiveLayer.run(inp)
    options = OptionsLayer.run(inp)
    merged = merge_all_layer_features(tech, pred, options)
    assert merged.options_direction_bias == options.options_direction_bias
    assert merged.iv_state == options.iv_state
    assert merged.structure_preference == options.structure_preference


def test_merge_all_layer_features_rejects_symbol_mismatch() -> None:
    inp_g = _make_googl_input()
    inp_a = OptionsStrategyInput(
        symbol="AAPL",
        as_of=datetime.now(tz=UTC),
        market_snapshot=_make_googl_input().market_snapshot,
    )
    tech = TechnicalLayer.run(inp_g)
    pred = PredictiveLayer.run(inp_g)
    options = OptionsLayer.run(inp_g)
    options_bad = options.model_copy(update={"symbol": "AAPL"})
    with pytest.raises(ValueError, match="symbols must match"):
        merge_all_layer_features(tech, pred, options_bad)
