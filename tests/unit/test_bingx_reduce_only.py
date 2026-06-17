"""Tests for BingX reduceOnly and one-way position side. # [PD-6][TH]"""

from __future__ import annotations

import pytest

from backend.layer_1_data.datos.bingx_client import (
    BingXPerpOrderRequest,
    resolve_one_way_position_side,
)
from backend.tests.layer_1.test_bingx_client_extended import RecordingClient


def test_resolve_one_way_position_side_entry_long() -> None:
    order = BingXPerpOrderRequest(symbol="META-USDT", side="BUY", position_side="BOTH")
    assert resolve_one_way_position_side(order) == "LONG"


def test_resolve_one_way_position_side_entry_short() -> None:
    order = BingXPerpOrderRequest(symbol="META-USDT", side="SELL", position_side="BOTH")
    assert resolve_one_way_position_side(order) == "SHORT"


def test_resolve_one_way_position_side_close_long() -> None:
    order = BingXPerpOrderRequest(
        symbol="META-USDT",
        side="SELL",
        position_side="LONG",
        reduce_only=True,
    )
    assert resolve_one_way_position_side(order) == "LONG"


def test_resolve_one_way_position_side_close_short() -> None:
    order = BingXPerpOrderRequest(
        symbol="META-USDT",
        side="BUY",
        position_side="SHORT",
        reduce_only=True,
    )
    assert resolve_one_way_position_side(order) == "SHORT"


@pytest.mark.asyncio
async def test_place_order_perp_sends_reduce_only_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BINGX_OMIT_REDUCE_ONLY", raising=False)
    client = RecordingClient(dry_run=False)
    await client.place_order_perp(
        BingXPerpOrderRequest(
            symbol="AAPL-USDT",
            side="SELL",
            position_side="LONG",
            order_type="MARKET",
            quantity=1.0,
            reduce_only=True,
        )
    )
    params = client.signed_calls[-1][2]
    assert params["reduceOnly"] == "true"
    assert params["positionSide"] == "LONG"
    assert params["side"] == "SELL"


@pytest.mark.asyncio
async def test_place_order_perp_omits_reduce_only_when_env_flag_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BINGX_OMIT_REDUCE_ONLY", "true")
    client = RecordingClient(dry_run=False)
    await client.place_order_perp(
        BingXPerpOrderRequest(
            symbol="AAPL-USDT",
            side="SELL",
            position_side="LONG",
            order_type="MARKET",
            quantity=1.0,
            reduce_only=True,
        )
    )
    params = client.signed_calls[-1][2]
    assert "reduceOnly" not in params
