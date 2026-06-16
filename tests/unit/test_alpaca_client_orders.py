"""AAA unit tests for backend.layer_1_data.datos.alpaca_client. # [TH][IM]"""

from __future__ import annotations

import pytest

from backend.layer_1_data.datos.alpaca_client import (
    AlpacaClient,
    AlpacaOptionsLegRequest,
    AlpacaOptionsOrderRequest,
    AlpacaOrderRequest,
)


def test_build_options_order_payload_single_leg() -> None:
    order = AlpacaOptionsOrderRequest(
        underlying="GOOGL",
        legs=(AlpacaOptionsLegRequest(symbol="GOOGL260627C00180000", side="buy"),),
        order_type="limit",
        limit_price=3.50,
        client_order_id="opt-test-1",
    )
    payload = AlpacaClient._build_options_order_payload(order, "opt-test-1")
    assert payload["symbol"] == "GOOGL260627C00180000"
    assert payload["side"] == "buy"
    assert payload["limit_price"] == "3.5"
    assert "order_class" not in payload


def test_build_options_order_payload_mleg_spread() -> None:
    order = AlpacaOptionsOrderRequest(
        underlying="GOOGL",
        legs=(
            AlpacaOptionsLegRequest(symbol="GOOGL260627C00180000", side="buy"),
            AlpacaOptionsLegRequest(symbol="GOOGL260627C00190000", side="sell"),
        ),
        order_type="limit",
        limit_price=1.25,
        client_order_id="opt-spread-1",
    )
    payload = AlpacaClient._build_options_order_payload(order, "opt-spread-1")
    assert payload["order_class"] == "mleg"
    assert len(payload["legs"]) == 2
    assert payload["limit_price"] == "1.25"


def test_build_order_payload_emits_bracket_with_tp_and_sl() -> None:
    order = AlpacaOrderRequest(
        symbol="AAPL",
        side="buy",
        qty=10,
        take_profit={"limit_price": 110.0},
        stop_loss={"stop_price": 94.0},
    )
    payload = AlpacaClient._build_order_payload(order, "qa-AAPL-1")
    assert payload["order_class"] == "bracket"
    assert payload["take_profit"] == {"limit_price": 110.0}
    assert payload["stop_loss"] == {"stop_price": 94.0}


def test_build_order_payload_simple_market_has_no_order_class() -> None:
    # ARRANGE
    order = AlpacaOrderRequest(symbol="AAPL", side="buy", qty=10)
    # ACT
    payload = AlpacaClient._build_order_payload(order, "qa-AAPL-1")
    # ASSERT
    assert "order_class" not in payload
    assert payload["qty"] == "10"


def test_client_defaults_to_dry_run_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE
    monkeypatch.delenv("ALPACA_DRY_RUN", raising=False)
    # ACT
    client = AlpacaClient(api_key="k", secret_key="s")
    # ASSERT
    assert client.dry_run is True


@pytest.mark.asyncio
async def test_place_order_dry_run_intercepts(monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE
    monkeypatch.delenv("ALPACA_DRY_RUN", raising=False)
    client = AlpacaClient(api_key="k", secret_key="s", dry_run=True)
    order = AlpacaOrderRequest(symbol="AAPL", side="buy", qty=5)
    # ACT
    response = await client.place_order(order)
    # ASSERT
    assert response.ok is True
    assert response.dry_run is True
    assert response.venue_order_id is None


@pytest.mark.asyncio
async def test_get_clock_dry_run_reports_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE
    monkeypatch.delenv("ALPACA_DRY_RUN", raising=False)
    client = AlpacaClient(api_key="k", secret_key="s", dry_run=True)
    # ACT
    clock = await client.get_clock()
    # ASSERT
    assert clock["is_open"] is True
