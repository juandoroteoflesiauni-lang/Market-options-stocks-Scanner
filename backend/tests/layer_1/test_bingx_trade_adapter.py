from __future__ import annotations
"""Tests for BingX trade tape adapter."""


from backend.layer_1_data.datos.bingx_trade_adapter import (
    build_microstructure_bundle,
    parse_bingx_trades,
)


def test_parse_bingx_trades_buy_sell() -> None:
    raw = [
        {"price": "100.0", "qty": "2", "isBuyerMaker": False},
        {"price": "100.5", "qty": "1", "isBuyerMaker": True},
    ]
    ticks = parse_bingx_trades(raw)
    assert len(ticks) == 2
    assert ticks[0].side == "buy"
    assert ticks[1].side == "sell"


def test_build_microstructure_bundle_from_trades() -> None:
    trades = [
        {"price": "100", "qty": "10", "isBuyerMaker": False},
        {"price": "100.1", "qty": "8", "isBuyerMaker": False},
        {"price": "100.2", "qty": "6", "isBuyerMaker": True},
        {"price": "100.1", "qty": "5", "isBuyerMaker": True},
        {"price": "100.0", "qty": "4", "isBuyerMaker": False},
    ]
    depth = {
        "bids": [["99.9", "100"]],
        "asks": [["100.1", "100"]],
    }
    bundle = build_microstructure_bundle(
        symbol="AAPL",
        venue_symbol="AAPL-USDT",
        raw_trades=trades,
        depth_payload=depth,
        market_type="stock_perp",
    )
    assert bundle.ok
    assert bundle.vpin is not None
    assert bundle.cvd is not None
    assert bundle.method_vpin == "vpin_trade_tape_v1"
