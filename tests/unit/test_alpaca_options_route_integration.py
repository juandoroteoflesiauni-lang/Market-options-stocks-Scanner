"""Tests integración opciones Alpaca Fase A (R1/R2 en bot dual). # [PD-6][TH]"""

from __future__ import annotations

from backend.config.alpaca_options_route_config import (
    alpaca_options_enabled,
    get_options_config_for_route,
)
from backend.domain.alpaca_models import AlpacaDecision
from backend.services.bot.alpaca_options_cycle_mixin import _eligible_equity_decisions


def test_alpaca_options_enabled_defaults_true(monkeypatch):
    monkeypatch.delenv("ALPACA_OPTIONS_ENABLED", raising=False)
    assert alpaca_options_enabled() is True


def test_r2_config_uses_structure_profile():
    cfg = get_options_config_for_route("scan", r2_symbols=("COIN",))
    assert cfg.structure_profile == "r2_basic"
    cfg = get_options_config_for_route("scan", r2_symbols=("COIN", "PLTR"))
    assert cfg.omni_engine.enabled_layers == ("technical",)
    assert cfg.universe.enforce_route1_only is False
    assert "COIN" in cfg.resolved_symbols
    trend = cfg.playbooks.playbooks["trend_continuation"]
    assert set(trend.allowed_structures) == {
        "long_call",
        "long_put",
        "short_put",
        "put_credit_spread",
    }
    assert cfg.risk.max_risk_per_trade_pct < 1.0


def test_r1_config_keeps_full_layers():
    cfg = get_options_config_for_route("priority")
    assert "predictive" in cfg.omni_engine.enabled_layers
    assert "options" in cfg.omni_engine.enabled_layers


def test_eligible_equity_decisions_filters_route_and_block():
    decisions = [
        AlpacaDecision(
            symbol="AAPL",
            decision="ALLOW",
            direction="LONG",
            score=0.9,
            route="priority",
        ),
        AlpacaDecision(
            symbol="COIN",
            decision="BLOCK",
            direction="LONG",
            score=0.8,
            route="scan",
        ),
        AlpacaDecision(
            symbol="PLTR",
            decision="ALLOW",
            direction="LONG",
            score=0.7,
            route="scan",
        ),
    ]
    r1 = _eligible_equity_decisions(decisions, "priority")
    assert [d.symbol for d in r1] == ["AAPL"]
    r2 = _eligible_equity_decisions(decisions, "scan")
    assert [d.symbol for d in r2] == ["PLTR"]
