from __future__ import annotations

import pytest

from backend.services.bingx_symbol_linker import (
    classify_underlying,
    display_name_from_bingx_symbol,
    is_ncsk_vst_stock_perp_symbol,
    normalize_venue_symbol,
    underlying_from_bingx_symbol,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("GOOGL-USDT", "GOOGL-USDT"),
        ("googl-usdt", "GOOGL-USDT"),
        ("msfton/usdt", "MSFTON-USDT"),
        ("BTC-USDT", "BTC-USDT"),
        ("  eth-usdt  ", "ETH-USDT"),
        ("AAPL/USDT", "AAPL-USDT"),
    ],
)
def test_normalize_venue_symbol(raw: str, expected: str) -> None:
    assert normalize_venue_symbol(raw) == expected


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("GOOGL-USDT", "GOOGL"),
        ("AAPL-USDT", "AAPL"),
        ("MSFTON/USDT", "MSFT"),
        ("PLTRON-USDT", "PLTR"),
        ("NCSKPLTR2USD-USDT", "PLTR"),
        ("NCSKAAPL2USD-USDT", "AAPL"),
        ("ncskgoogl2usd-usdt", "GOOGL"),
        ("BTC-USDT", "BTC"),
        ("ETH-USDT", "ETH"),
        ("SOL-USDT", "SOL"),
        # lowercase passthrough
        ("btc-usdt", "BTC"),
    ],
)
def test_underlying_from_bingx_symbol(symbol: str, expected: str) -> None:
    assert underlying_from_bingx_symbol(symbol) == expected


def test_classify_underlying_stock_perp() -> None:
    assert classify_underlying("GOOGL-USDT") == "stock_perp"


def test_classify_underlying_crypto_standard() -> None:
    assert classify_underlying("BTC-USDT") == "excluded"


def test_classify_underlying_excluded_commodity() -> None:
    assert classify_underlying("GOLD-USDT") == "excluded"


def test_classify_underlying_excluded_stablecoin() -> None:
    assert classify_underlying("USDT-USDT") == "excluded"


def test_classify_underlying_on_suffix_stock_perp() -> None:
    assert classify_underlying("MSFTON/USDT") == "stock_perp"


def test_classify_underlying_ncsk_vst_api_symbol() -> None:
    assert classify_underlying("NCSKPLTR2USD-USDT") == "stock_perp"


@pytest.mark.parametrize(
    "symbol",
    [
        "NCSKPLTR2USD-USDT",
        "NCSKAAPL2USD-USDT",
    ],
)
def test_is_ncsk_vst_stock_perp_symbol_true(symbol: str) -> None:
    assert is_ncsk_vst_stock_perp_symbol(symbol) is True


def test_is_ncsk_vst_stock_perp_symbol_false_for_display_name() -> None:
    assert is_ncsk_vst_stock_perp_symbol("PLTR-USDT") is False


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("NCSKAAPL2USD-USDT", "AAPL-USDT"),
        ("NCSKGOOGL2USD-USDT", "GOOGL-USDT"),
        ("MSFT-USDT", "MSFT-USDT"),
        ("MSFTON-USDT", "MSFT-USDT"),
    ],
)
def test_display_name_from_bingx_symbol(symbol: str, expected: str) -> None:
    assert display_name_from_bingx_symbol(symbol) == expected


@pytest.mark.parametrize(
    "symbol",
    [
        "SPX-USDT",
        "NDX-USDT",
        "US500-USDT",
        "US100-USDT",
    ],
)
def test_classify_underlying_stock_index_perp(symbol: str) -> None:
    assert classify_underlying(symbol) == "stock_index_perp"
