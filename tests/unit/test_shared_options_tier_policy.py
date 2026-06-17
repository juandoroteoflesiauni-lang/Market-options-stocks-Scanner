"""Tests for shared options tier policy. # [PD-6][TH]"""

from __future__ import annotations

from backend.config.shared_options_tier_policy import (
    is_full_quant_tier,
    normalize_equity_root,
    options_query_symbol_for_root,
)


def test_normalize_equity_root_bingx_venue() -> None:
    assert normalize_equity_root("AAPL-USDT") == "AAPL"
    assert normalize_equity_root("NCSKAAPL2USD-USDT") == "AAPL"


def test_full_quant_tier_route1_watchlist() -> None:
    assert is_full_quant_tier("MSFT", open_position_roots=frozenset()) is True
    assert is_full_quant_tier("HOOD", open_position_roots=frozenset()) is False


def test_full_quant_tier_open_position() -> None:
    roots = frozenset({"HOOD", "MCD"})
    assert is_full_quant_tier("HOOD", open_position_roots=roots) is True
    assert is_full_quant_tier("NCSKHOOD2USD-USDT", open_position_roots=roots) is True
    assert is_full_quant_tier("COIN", open_position_roots=roots) is False


def test_full_quant_tier_spx_via_spy_proxy() -> None:
    assert is_full_quant_tier("SPX-USDT", open_position_roots=frozenset()) is True


def test_options_query_symbol_index_proxy() -> None:
    assert options_query_symbol_for_root("SPX") == "SPY"
