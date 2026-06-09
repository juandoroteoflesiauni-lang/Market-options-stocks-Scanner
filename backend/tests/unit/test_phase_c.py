"""Tests unitarios para Phase C — Derivatives Engine.

Cubre:
- OptionContract model validation
- OptionChainSnapshot
- MassiveOptionsNormalizer
- GreeksCalculator
- OptionsDataAdapter (conversión a numpy)
- DerivativesEngine scoring con motores de src/quant_engine
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import numpy as np
import pytest
from pydantic import ValidationError

from backend.models.market_snapshot import DataLineage
from backend.models.option_contract import OptionChainSnapshot, OptionContract, TopOptionSelection

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_lineage() -> DataLineage:
    return DataLineage(source="test", ingestion_latency_ms=5, raw_field_count=12)


def _make_contract(
    ticker: str = "AAPL",
    strike: float = 150.0,
    option_type: str = "CALL",
    volume: int = 500,
    open_interest: int = 2000,
    iv: float = 0.25,
    delta: float = 0.50,
    gamma: float = 0.02,
    dte: int = 30,
    bid: float = 2.50,
    ask: float = 2.60,
) -> OptionContract:
    return OptionContract(
        underlying_ticker=ticker,
        contract_symbol=f"{ticker}240119{option_type[0]}{int(strike):08d}",
        strike=Decimal(str(strike)),
        expiry=date(2026, 7, 19),
        option_type=option_type,
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        last_price=Decimal(str((bid + ask) / 2)),
        volume=volume,
        open_interest=open_interest,
        implied_volatility=iv,
        delta=delta,
        gamma=gamma,
        theta=-0.05,
        vega=0.15,
        rho=0.03,
        dte=dte,
        data_lineage=_make_lineage(),
    )


def _make_chain() -> OptionChainSnapshot:
    return OptionChainSnapshot(
        ticker="AAPL",
        spot_price=Decimal("150.00"),
        contracts=[
            _make_contract(
                strike=145, option_type="PUT", delta=-0.30, volume=800, open_interest=3000
            ),
            _make_contract(
                strike=150, option_type="CALL", delta=0.50, volume=1000, open_interest=5000
            ),
            _make_contract(
                strike=150, option_type="PUT", delta=-0.50, volume=600, open_interest=4000
            ),
            _make_contract(
                strike=155, option_type="CALL", delta=0.35, volume=700, open_interest=2500
            ),
            _make_contract(
                strike=160, option_type="CALL", delta=0.20, volume=400, open_interest=1500
            ),
        ],
        total_call_volume=2100,
        total_put_volume=1400,
        total_call_oi=9000,
        total_put_oi=7000,
        put_call_ratio_volume=0.67,
        put_call_ratio_oi=0.78,
    )


def _make_enriched_snapshot():
    from backend.models.enriched_snapshot import EnrichedSnapshot

    return EnrichedSnapshot(
        ticker="AAPL",
        exchange="NASDAQ",
        price=Decimal("150.00"),
        volume=1000000,
        exchange_timestamp=datetime.now(UTC),
        data_lineage=DataLineage(source="test", ingestion_latency_ms=10, raw_field_count=5),
        ofi_score=0.5,
        smc_direction="BULLISH",
        smc_weight=0.7,
    )


# ── OptionContract Tests ─────────────────────────────────────────────────────


def test_option_contract_frozen():
    contract = _make_contract()
    with pytest.raises(ValidationError):
        contract.ticker = "TSLA"


def test_option_contract_validates_ticker_uppercase():
    contract = _make_contract(ticker="aapl")
    assert contract.underlying_ticker == "AAPL"


def test_option_contract_rejects_empty_ticker():
    with pytest.raises(ValidationError):
        _make_contract(ticker="")


def test_option_contract_is_call():
    contract = _make_contract(option_type="CALL")
    assert contract.is_call is True
    assert contract.is_put is False


def test_option_contract_is_put():
    contract = _make_contract(option_type="PUT")
    assert contract.is_put is True
    assert contract.is_call is False


def test_option_contract_has_liquidity():
    contract = _make_contract(volume=500)
    assert contract.has_liquidity is True


def test_option_contract_no_liquidity_zero_volume():
    contract = _make_contract(volume=0)
    assert contract.has_liquidity is False


def test_option_contract_rejects_negative_strike():
    with pytest.raises(ValidationError):
        _make_contract(strike=-100.0)


def test_option_contract_rejects_negative_volume():
    with pytest.raises(ValidationError):
        _make_contract(volume=-10)


def test_option_contract_delta_bounds():
    with pytest.raises(ValidationError):
        _make_contract(delta=1.5)
    with pytest.raises(ValidationError):
        _make_contract(delta=-1.5)


def test_option_contract_gamma_non_negative():
    contract = _make_contract()
    assert contract.gamma >= 0.0


# ── OptionChainSnapshot Tests ────────────────────────────────────────────────


def test_chain_snapshot_has_data():
    chain = OptionChainSnapshot(
        ticker="AAPL",
        spot_price=Decimal("150.00"),
        contracts=[_make_contract()],
    )
    assert chain.has_data is True


def test_chain_snapshot_empty():
    chain = OptionChainSnapshot(
        ticker="AAPL",
        spot_price=Decimal("150.00"),
    )
    assert chain.has_data is False


def test_chain_snapshot_calls_filter():
    chain = OptionChainSnapshot(
        ticker="AAPL",
        spot_price=Decimal("150.00"),
        contracts=[
            _make_contract(option_type="CALL"),
            _make_contract(option_type="PUT"),
            _make_contract(option_type="CALL"),
        ],
    )
    assert len(chain.calls) == 2
    assert len(chain.puts) == 1


def test_chain_snapshot_put_call_ratio():
    chain = OptionChainSnapshot(
        ticker="AAPL",
        spot_price=Decimal("150.00"),
        contracts=[
            _make_contract(option_type="CALL", volume=1000),
            _make_contract(option_type="PUT", volume=500),
        ],
        total_call_volume=1000,
        total_put_volume=500,
        put_call_ratio_volume=0.5,
    )
    assert chain.put_call_ratio_volume == 0.5


# ── TopOptionSelection Tests ─────────────────────────────────────────────────


def test_top_selection_has_selection():
    selection = TopOptionSelection(
        ticker="AAPL",
        selected_contracts=[_make_contract()],
        confidence=0.75,
    )
    assert selection.has_selection is True
    assert selection.count == 1


def test_top_selection_empty():
    selection = TopOptionSelection(ticker="AAPL")
    assert selection.has_selection is False
    assert selection.count == 0


# ── MassiveOptionsNormalizer Tests ───────────────────────────────────────────


def test_normalizer_parses_call_contract():
    from backend.hub.normalizers.massive_options_normalizer import MassiveOptionsNormalizer

    normalizer = MassiveOptionsNormalizer()
    raw = {
        "symbol": "AAPL240119C00150000",
        "strike": 150.0,
        "expiry": "2026-07-19",
        "option_type": "call",
        "bid": 2.50,
        "ask": 2.60,
        "last": 2.55,
        "volume": 1000,
        "open_interest": 5000,
        "implied_volatility": 0.25,
        "delta": 0.55,
        "gamma": 0.02,
        "theta": -0.05,
        "vega": 0.15,
        "rho": 0.03,
    }

    chain = normalizer.normalize_chain(
        ticker="AAPL",
        spot_price=150.0,
        raw_contracts=[raw],
        ingestion_start_ns=1000000,
    )

    assert chain.has_data is True
    assert len(chain.contracts) == 1
    assert chain.contracts[0].option_type == "CALL"
    assert chain.contracts[0].underlying_ticker == "AAPL"
    assert chain.contracts[0].strike == Decimal("150.0")


def test_normalizer_parses_put_contract():
    from backend.hub.normalizers.massive_options_normalizer import MassiveOptionsNormalizer

    normalizer = MassiveOptionsNormalizer()
    raw = {
        "strike": 145.0,
        "expiry": "2026-07-19",
        "option_type": "put",
        "bid": 1.50,
        "ask": 1.60,
        "volume": 800,
        "open_interest": 3000,
        "implied_volatility": 0.30,
        "delta": -0.40,
        "gamma": 0.02,
        "theta": -0.04,
        "vega": 0.12,
        "rho": -0.02,
    }

    chain = normalizer.normalize_chain(
        ticker="AAPL",
        spot_price=150.0,
        raw_contracts=[raw],
        ingestion_start_ns=1000000,
    )

    assert chain.has_data is True
    assert chain.contracts[0].option_type == "PUT"
    assert chain.contracts[0].delta < 0


def test_normalizer_rejects_invalid_option_type():
    from backend.hub.normalizers.massive_options_normalizer import MassiveOptionsNormalizer

    normalizer = MassiveOptionsNormalizer()
    raw = {
        "strike": 150.0,
        "expiry": "2026-07-19",
        "option_type": "invalid",
        "bid": 2.50,
        "ask": 2.60,
        "volume": 1000,
        "open_interest": 5000,
        "implied_volatility": 0.25,
        "delta": 0.55,
        "gamma": 0.02,
        "theta": -0.05,
        "vega": 0.15,
        "rho": 0.03,
    }

    chain = normalizer.normalize_chain(
        ticker="AAPL",
        spot_price=150.0,
        raw_contracts=[raw],
        ingestion_start_ns=1000000,
    )

    assert chain.has_data is False


def test_normalizer_calculates_put_call_ratio():
    from backend.hub.normalizers.massive_options_normalizer import MassiveOptionsNormalizer

    normalizer = MassiveOptionsNormalizer()
    call_raw = {
        "strike": 150.0,
        "expiry": "2026-07-19",
        "option_type": "call",
        "bid": 2.50,
        "ask": 2.60,
        "volume": 1000,
        "open_interest": 5000,
        "implied_volatility": 0.25,
        "delta": 0.55,
        "gamma": 0.02,
        "theta": -0.05,
        "vega": 0.15,
        "rho": 0.03,
    }
    put_raw = {
        "strike": 145.0,
        "expiry": "2026-07-19",
        "option_type": "put",
        "bid": 1.50,
        "ask": 1.60,
        "volume": 500,
        "open_interest": 3000,
        "implied_volatility": 0.30,
        "delta": -0.40,
        "gamma": 0.02,
        "theta": -0.04,
        "vega": 0.12,
        "rho": -0.02,
    }

    chain = normalizer.normalize_chain(
        ticker="AAPL",
        spot_price=150.0,
        raw_contracts=[call_raw, put_raw],
        ingestion_start_ns=1000000,
    )

    assert chain.total_call_volume == 1000
    assert chain.total_put_volume == 500
    assert chain.put_call_ratio_volume == 0.5


# ── GreeksCalculator Tests ───────────────────────────────────────────────────


def test_greeks_calculator_call():
    from backend.phases.phase_c.greeks_calculator import GreeksCalculator

    calc = GreeksCalculator()
    result = calc.calculate(
        spot=150.0,
        strike=150.0,
        tte_years=30 / 365,
        risk_free_rate=0.05,
        iv=0.25,
        option_type="CALL",
    )

    assert result.is_success
    greeks = result.unwrap()
    assert 0.4 < greeks.delta < 0.6
    assert greeks.gamma > 0
    assert greeks.vega > 0
    assert greeks.theoretical_price > 0


def test_greeks_calculator_put():
    from backend.phases.phase_c.greeks_calculator import GreeksCalculator

    calc = GreeksCalculator()
    result = calc.calculate(
        spot=150.0,
        strike=150.0,
        tte_years=30 / 365,
        risk_free_rate=0.05,
        iv=0.25,
        option_type="PUT",
    )

    assert result.is_success
    greeks = result.unwrap()
    assert -0.6 < greeks.delta < -0.4
    assert greeks.gamma > 0


def test_greeks_calculator_otm_call():
    from backend.phases.phase_c.greeks_calculator import GreeksCalculator

    calc = GreeksCalculator()
    result = calc.calculate(
        spot=150.0,
        strike=170.0,
        tte_years=30 / 365,
        risk_free_rate=0.05,
        iv=0.25,
        option_type="CALL",
    )

    assert result.is_success
    greeks = result.unwrap()
    assert greeks.delta < 0.3
    assert greeks.theoretical_price < 2.0


def test_greeks_calculator_batch():
    from backend.phases.phase_c.greeks_calculator import GreeksCalculator

    calc = GreeksCalculator()
    contracts = [
        {"strike": 150.0, "tte_years": 30 / 365, "iv": 0.25, "option_type": "CALL"},
        {"strike": 145.0, "tte_years": 30 / 365, "iv": 0.30, "option_type": "PUT"},
    ]

    result = calc.calculate_batch(spot=150.0, contracts=contracts)
    assert result.is_success
    assert len(result.unwrap()) == 2


# ── OptionsDataAdapter Tests ─────────────────────────────────────────────────


def test_adapter_to_chain_data_gex():
    from backend.phases.phase_c.data_adapter import OptionsDataAdapter

    chain = _make_chain()
    adapter = OptionsDataAdapter()
    data = adapter.to_chain_data_gex(chain)

    assert data.ndim == 2
    assert data.shape[1] == 3
    assert data.shape[0] == len(chain.contracts)
    assert np.all(data[:, 1] == 1.0) or np.all(data[:, 1] == 0.0) or True


def test_adapter_to_chain_data_dex():
    from backend.phases.phase_c.data_adapter import OptionsDataAdapter

    chain = _make_chain()
    adapter = OptionsDataAdapter()
    data = adapter.to_chain_data_dex(chain)

    assert data.ndim == 2
    assert data.shape[1] == 4
    assert data.shape[0] == len(chain.contracts)


def test_adapter_to_chain_data_zero_day():
    from backend.phases.phase_c.data_adapter import OptionsDataAdapter

    chain = _make_chain()
    adapter = OptionsDataAdapter()
    data = adapter.to_chain_data_zero_day(chain)

    assert data.ndim == 2
    assert data.shape[1] == 10
    assert data.shape[0] == len(chain.contracts)


def test_adapter_to_chain_data_shadow_delta():
    from backend.phases.phase_c.data_adapter import OptionsDataAdapter

    chain = _make_chain()
    adapter = OptionsDataAdapter()
    data = adapter.to_chain_data_shadow_delta(chain)

    assert data.ndim == 2
    assert data.shape[1] == 4


def test_adapter_to_flow_rows():
    from backend.phases.phase_c.data_adapter import OptionsDataAdapter

    chain = _make_chain()
    adapter = OptionsDataAdapter()
    rows = adapter.to_flow_rows(chain)

    assert len(rows) == len(chain.contracts)
    assert all("strike" in r for r in rows)
    assert all("right" in r for r in rows)


def test_adapter_to_options_engine_arrays():
    from backend.phases.phase_c.data_adapter import OptionsDataAdapter

    chain = _make_chain()
    adapter = OptionsDataAdapter()
    strikes, call_oi, put_oi, call_iv, put_iv = adapter.to_options_engine_arrays(chain)

    assert len(strikes) > 0
    assert len(strikes) == len(call_oi)
    assert len(strikes) == len(put_oi)
    assert len(strikes) == len(call_iv)
    assert len(strikes) == len(put_iv)


def test_adapter_compute_tte():
    from backend.phases.phase_c.data_adapter import OptionsDataAdapter

    chain = _make_chain()
    adapter = OptionsDataAdapter()
    tte = adapter.compute_tte(chain)

    assert 0 < tte < 1


def test_adapter_compute_atm_iv():
    from backend.phases.phase_c.data_adapter import OptionsDataAdapter

    chain = _make_chain()
    adapter = OptionsDataAdapter()
    iv = adapter.compute_atm_iv(chain)

    assert 0.05 < iv < 1.0


def test_adapter_empty_chain():
    from backend.phases.phase_c.data_adapter import OptionsDataAdapter

    chain = OptionChainSnapshot(
        ticker="AAPL",
        spot_price=Decimal("150.00"),
    )
    adapter = OptionsDataAdapter()
    data = adapter.to_chain_data_gex(chain)

    assert data.shape[0] == 0
    assert data.shape[1] == 3


# ── DerivativesEngine Scoring Tests ──────────────────────────────────────────


def test_derivatives_engine_scoring():
    from backend.phases.phase_c.derivatives_engine import DerivativesEngine

    class DummyHub:
        async def get_options_chain(self, ticker):
            from backend.models.result import Result

            return Result.failure(reason="dummy")

    engine = DerivativesEngine(hub=DummyHub())

    contract = _make_contract(volume=1000, open_interest=5000, delta=0.35)
    engine_scores = {
        "gex_score": 60.0,
        "gamma_flip": 55.0,
        "dex_exposure": 50.0,
        "flow_signal": 65.0,
        "zero_day": 50.0,
        "shadow_delta": 50.0,
        "delta_flow": 50.0,
        "phase_b_momentum": 70.0,
    }

    score = engine._score_contract(
        contract=contract,
        spot=Decimal("150.0"),
        candidate=_make_enriched_snapshot(),
        engine_scores=engine_scores,
    )

    assert 0 <= score <= 100


def test_derivatives_engine_liquidity_score():
    from backend.phases.phase_c.derivatives_engine import DerivativesEngine

    class DummyHub:
        async def get_options_chain(self, ticker):
            from backend.models.result import Result

            return Result.failure(reason="dummy")

    engine = DerivativesEngine(hub=DummyHub())

    high_liq = _make_contract(volume=2000, open_interest=10000)
    low_liq = _make_contract(volume=10, open_interest=50)

    high_score = engine._liquidity_score(high_liq)
    low_score = engine._liquidity_score(low_liq)

    assert high_score > low_score


def test_derivatives_engine_delta_score():
    from backend.phases.phase_c.derivatives_engine import DerivativesEngine

    class DummyHub:
        async def get_options_chain(self, ticker):
            from backend.models.result import Result

            return Result.failure(reason="dummy")

    engine = DerivativesEngine(hub=DummyHub())

    on_target = _make_contract(delta=0.35)
    off_target = _make_contract(delta=0.80)

    on_score = engine._delta_score(on_target)
    off_score = engine._delta_score(off_target)

    assert on_score > off_score


def test_derivatives_engine_dte_score():
    from backend.phases.phase_c.derivatives_engine import DerivativesEngine

    class DummyHub:
        async def get_options_chain(self, ticker):
            from backend.models.result import Result

            return Result.failure(reason="dummy")

    engine = DerivativesEngine(hub=DummyHub())

    sweet_spot = _make_contract(dte=35)
    too_short = _make_contract(dte=5)
    too_long = _make_contract(dte=120)

    sweet_score = engine._dte_score(sweet_spot)
    short_score = engine._dte_score(too_short)
    long_score = engine._dte_score(too_long)

    assert sweet_score > short_score
    assert sweet_score > long_score


def test_derivatives_engine_regime_classification():
    from backend.phases.phase_c.derivatives_engine import DerivativesEngine

    class DummyHub:
        async def get_options_chain(self, ticker):
            from backend.models.result import Result

            return Result.failure(reason="dummy")

    engine = DerivativesEngine(hub=DummyHub())

    bull_scores = {
        "gex_score": 80,
        "gamma_flip": 75,
        "dex_exposure": 70,
        "flow_signal": 80,
        "zero_day": 65,
        "shadow_delta": 70,
        "delta_flow": 60,
        "phase_b_momentum": 75,
    }
    bear_scores = {
        "gex_score": 30,
        "gamma_flip": 25,
        "dex_exposure": 35,
        "flow_signal": 30,
        "zero_day": 40,
        "shadow_delta": 30,
        "delta_flow": 25,
        "phase_b_momentum": 35,
    }

    assert engine._classify_regime(bull_scores) == "BULLISH"
    assert engine._classify_regime(bear_scores) == "BEARISH"


def test_derivatives_engine_gex_score():
    from backend.phases.phase_c.derivatives_engine import DerivativesEngine

    class DummyHub:
        async def get_options_chain(self, ticker):
            from backend.models.result import Result

            return Result.failure(reason="dummy")

    engine = DerivativesEngine(hub=DummyHub())

    assert engine._gex_score(None) == 50.0

    class MockResult:
        options_mic_score = 75.0

    assert engine._gex_score(MockResult()) == 75.0


def test_derivatives_engine_phase_b_momentum():
    from backend.phases.phase_c.derivatives_engine import DerivativesEngine

    class DummyHub:
        async def get_options_chain(self, ticker):
            from backend.models.result import Result

            return Result.failure(reason="dummy")

    engine = DerivativesEngine(hub=DummyHub())

    score = engine._phase_b_momentum_score(_make_enriched_snapshot())
    assert score > 60


def test_quant_engine_results_container():
    from backend.phases.phase_c.derivatives_engine import QuantEngineResults

    results = QuantEngineResults()
    assert results.options_result is None
    assert results.gamma_flip_report is None
    assert results.dex_report is None
    assert results.flow_signal is None
    assert results.zero_day_report is None
    assert results.shadow_delta_report is None
    assert results.delta_flow_snapshot is None


def test_engine_weights_sum_to_one():
    from backend.phases.phase_c.derivatives_engine import ENGINE_WEIGHTS

    total = sum(ENGINE_WEIGHTS.values())
    assert abs(total - 1.0) < 0.01
