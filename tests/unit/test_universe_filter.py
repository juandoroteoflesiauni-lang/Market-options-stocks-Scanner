"""Tests for OptionUniverseFilter."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from backend.models.market_snapshot import DataLineage
from backend.models.option_contract import OptionChainSnapshot, OptionContract
from backend.models.strategy_weights import PhaseCContractFilters
from backend.phases.phase_c.universe_filter import OptionUniverseFilter


def _lineage() -> DataLineage:
    return DataLineage(source="test", ingestion_latency_ms=1, raw_field_count=1)


def _contract(
    *,
    strike: str = "100",
    dte: int = 30,
    volume: int = 500,
    oi: int = 1000,
    opt: str = "CALL",
) -> OptionContract:
    return OptionContract(
        underlying_ticker="SPY",
        contract_symbol=f"SPY{strike}{opt[0]}",
        strike=Decimal(strike),
        expiry=date(2026, 7, 19),
        option_type=opt,  # type: ignore[arg-type]
        bid=Decimal("1.0"),
        ask=Decimal("1.1"),
        volume=volume,
        open_interest=oi,
        implied_volatility=0.25,
        delta=0.5,
        gamma=0.01,
        theta=-0.05,
        vega=0.1,
        rho=0.02,
        mid_price=Decimal("1.05"),
        spread=Decimal("0.1"),
        spread_pct=0.05,
        dte=dte,
        data_lineage=_lineage(),
    )


def test_sd_filter_drops_far_strikes() -> None:
    filters = PhaseCContractFilters(use_sd_strikes=True, strike_sd_range=1.0)
    chain = OptionChainSnapshot(
        ticker="SPY",
        spot_price=Decimal("100"),
        contracts=[_contract(strike="100"), _contract(strike="200")],
        fetch_timestamp=datetime.now(),
    )
    out = OptionUniverseFilter.filter_chain(chain, filters, atm_iv=0.25)
    strikes = {float(c.strike) for c in out.contracts}
    assert 100.0 in strikes
    assert 200.0 not in strikes


def test_oi_veto() -> None:
    filters = PhaseCContractFilters(min_open_interest=500)
    chain = OptionChainSnapshot(
        ticker="SPY",
        spot_price=Decimal("100"),
        contracts=[_contract(oi=100), _contract(oi=600)],
        fetch_timestamp=datetime.now(),
    )
    out = OptionUniverseFilter.filter_chain(chain, filters)
    assert len(out.contracts) == 1


def test_dte_window() -> None:
    filters = PhaseCContractFilters(min_dte=14, max_dte=60)
    chain = OptionChainSnapshot(
        ticker="SPY",
        spot_price=Decimal("100"),
        contracts=[_contract(dte=5), _contract(dte=30)],
        fetch_timestamp=datetime.now(),
    )
    out = OptionUniverseFilter.filter_chain(chain, filters)
    assert len(out.contracts) == 1
    assert out.contracts[0].dte == 30
