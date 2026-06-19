"""Tests for RealisticOptionFillModel."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.backtesting.fill_models import RealisticOptionFillModel
from backend.models.market_snapshot import DataLineage
from backend.models.option_contract import OptionContract


def _contract(**kwargs: object) -> OptionContract:
    base: dict[str, object] = {
        "underlying_ticker": "SPY",
        "contract_symbol": "SPYC100",
        "strike": Decimal("100"),
        "expiry": date(2026, 7, 19),
        "option_type": "CALL",
        "bid": Decimal("2.0"),
        "ask": Decimal("2.2"),
        "volume": 100,
        "open_interest": 500,
        "implied_volatility": 0.25,
        "delta": 0.5,
        "gamma": 0.01,
        "theta": -0.05,
        "vega": 0.1,
        "rho": 0.02,
        "mid_price": Decimal("2.1"),
        "spread": Decimal("0.2"),
        "spread_pct": 0.05,
        "dte": 30,
        "data_lineage": DataLineage(source="t", ingestion_latency_ms=1, raw_field_count=1),
    }
    base.update(kwargs)
    return OptionContract(**base)  # type: ignore[arg-type]


def test_buy_fills_at_ask() -> None:
    fill = RealisticOptionFillModel.simulate(_contract(), Decimal("1"), "BUY")
    assert fill.fill_price == Decimal("2.20")


def test_sell_fills_at_bid() -> None:
    fill = RealisticOptionFillModel.simulate(_contract(), Decimal("1"), "SELL")
    assert fill.fill_price == Decimal("2.00")


def test_partial_when_size_exceeds_liquidity_cap() -> None:
    fill = RealisticOptionFillModel.simulate(_contract(volume=10), Decimal("100"), "BUY")
    assert fill.partial is True
    assert fill.filled_qty < fill.requested_qty
