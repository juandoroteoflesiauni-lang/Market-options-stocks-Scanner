from backend.services.bingx_market_data_router import build_market_data_route


def test_stock_perp_route_uses_bingx_venue_and_underlying_options() -> None:
    route = build_market_data_route("AAPL-USDT")

    assert route.venue_symbol == "AAPL-USDT"
    assert route.underlying_symbol == "AAPL"
    assert route.market_type == "stock_perp"
    assert route.analysis_ohlcv.primary == "bingx"
    assert route.analysis_volume.primary == "bingx"
    assert route.analysis_order_book.primary == "bingx"
    assert route.analysis_trades.primary == "bingx"
    assert route.execution_l2.primary == "bingx"
    assert route.options.primary == "equity_options"
    assert route.options.symbol == "AAPL"
    assert route.crypto_derivatives.primary is None


def test_crypto_route_uses_binance_analysis_bingx_execution_and_deribit_options(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.services.bingx_market_data_router.classify_underlying",
        lambda sym: "crypto_standard",
    )
    route = build_market_data_route("BTC-USDT")

    assert route.venue_symbol == "BTC-USDT"
    assert route.underlying_symbol == "BTC"
    assert route.market_type == "crypto_standard"
    assert route.analysis_ohlcv.primary == "binance"
    assert route.analysis_ohlcv.fallbacks == ("bingx",)
    assert route.analysis_volume.primary == "binance"
    assert route.analysis_order_book.primary == "binance"
    assert route.analysis_trades.primary == "binance"
    assert route.execution_l2.primary == "bingx"
    assert route.options.primary == "deribit"
    assert route.options.symbol == "BTC"
    assert route.crypto_derivatives.primary == "deribit"


def test_index_perp_route_uses_index_options_proxy() -> None:
    route = build_market_data_route("SPX-USDT")

    assert route.market_type == "stock_index_perp"
    assert route.options.primary == "equity_options_proxy"
    assert route.options.symbol == "SPY"
    assert route.analysis_ohlcv.primary == "bingx"
