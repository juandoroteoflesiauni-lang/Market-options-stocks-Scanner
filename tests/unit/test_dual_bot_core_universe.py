"""Tests universo fijo dual-bot core (20 tickers)."""

from __future__ import annotations

import pytest

from backend.config.alpaca_priority_route import resolve_route1_watchlist, route1_symbols_set
from backend.config.dual_bot_core_universe import (
    DUAL_BOT_CORE_UNIVERSE,
    core_bingx_venue_symbols,
    core_symbol_has_full_quant,
    core_to_bingx_venue_symbol,
    dual_bot_core_env_flags,
    dual_bot_fixed_universe_enabled,
    dual_bot_route2_enabled,
    resolve_active_equity_universe,
)


def test_core_universe_has_twenty_unique_tickers() -> None:
    assert len(DUAL_BOT_CORE_UNIVERSE) == 20
    assert len(set(DUAL_BOT_CORE_UNIVERSE)) == 20


def test_bingx_venue_symbol_mapping() -> None:
    assert core_to_bingx_venue_symbol("aapl") == "AAPL-USDT"
    assert core_to_bingx_venue_symbol("amd") == "AMDUS-USDT"
    assert len(core_bingx_venue_symbols()) == 20


def test_fixed_universe_env_flags() -> None:
    flags = dual_bot_core_env_flags()
    assert flags["DUAL_BOT_FIXED_UNIVERSE"] == "true"
    assert flags["DUAL_BOT_ROUTE2_ENABLED"] == "false"
    assert flags["SHARED_OPTIONS_TIER_ENABLED"] == "true"
    assert flags["TECHNICAL_ENABLE_HMM_ENGINE"] == "true"
    assert flags["BINGX_SKIP_OPTIONS_SNAPSHOT"] == "false"
    assert "AAPL" in flags["BINGX_PRIORITY_STOCKS"]


def test_core_symbol_has_full_quant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUAL_BOT_FIXED_UNIVERSE", "true")
    assert core_symbol_has_full_quant("AAPL-USDT")
    assert core_symbol_has_full_quant("HOOD")
    assert not core_symbol_has_full_quant("GME")


def test_resolve_route1_uses_core_when_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUAL_BOT_FIXED_UNIVERSE", "true")
    assert dual_bot_fixed_universe_enabled()
    assert resolve_route1_watchlist() == DUAL_BOT_CORE_UNIVERSE
    assert "AAPL" in route1_symbols_set()
    assert not dual_bot_route2_enabled()


def test_resolve_route1_legacy_when_unfixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUAL_BOT_FIXED_UNIVERSE", "false")
    watchlist = resolve_route1_watchlist()
    assert len(watchlist) == 11
    assert "MSFT" in watchlist
    universe = resolve_active_equity_universe()
    assert len(universe) >= 11
