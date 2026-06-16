"""Tests dual-route Alpaca: R1 prioritaria vs R2 scan dinámico. # [TH][IM]"""

from __future__ import annotations

import pytest

from backend.config.alpaca_priority_route import (
    ALPACA_ROUTE1_WATCHLIST,
    ROUTE1_NOTIONAL_MULTIPLIER,
    ROUTE2_NOTIONAL_MULTIPLIER,
    is_route1_symbol,
    scan_pool_excluding_route1,
)
from backend.domain.alpaca_models import AlpacaCandidateAnalysis, AlpacaDecision
from backend.domain.probabilistic_models import PredictiveOptionsBundleReport
from backend.services.alpaca_dual_route_service import select_route2_symbols
from backend.services.alpaca_risk_desk import AlpacaRiskDesk, AlpacaRiskPolicy
from backend.services.alpaca_universe_funnel import FunnelConfig, SymbolBars
from backend.services.equity_options_gate_service import apply_equity_options_gate


def _bars(symbol: str, close: float = 100.0) -> SymbolBars:
    closes = tuple(close + i * 0.1 for i in range(40))
    highs = tuple(c + 1 for c in closes)
    lows = tuple(c - 1 for c in closes)
    volumes = tuple(1_000_000.0 for _ in closes)
    return SymbolBars(symbol=symbol, highs=highs, lows=lows, closes=closes, volumes=volumes)


def test_route1_watchlist_has_eleven_tickers() -> None:
    assert len(ALPACA_ROUTE1_WATCHLIST) == 11
    assert "META" in ALPACA_ROUTE1_WATCHLIST
    assert "AMZN" in ALPACA_ROUTE1_WATCHLIST


def test_scan_pool_excludes_route1_symbols() -> None:
    universe = (*ALPACA_ROUTE1_WATCHLIST, "PLTR", "AMD", "NFLX")
    pool = scan_pool_excluding_route1(universe, pool_size=10)
    assert not any(is_route1_symbol(s) for s in pool)
    assert "PLTR" in pool


def test_select_route2_returns_at_most_twenty() -> None:
    bars = [_bars(f"SYM{i}", close=50.0 + i) for i in range(30)]
    benchmark = tuple(100.0 + i * 0.05 for i in range(40))
    selected = select_route2_symbols(bars, benchmark, FunnelConfig(top_n=20))
    assert len(selected) <= 20


def test_route1_notional_multiplier_exceeds_route2() -> None:
    assert ROUTE1_NOTIONAL_MULTIPLIER > ROUTE2_NOTIONAL_MULTIPLIER


def test_risk_desk_applies_route1_sizing_boost() -> None:
    desk = AlpacaRiskDesk(policy=AlpacaRiskPolicy(notional_per_trade_usd=1000.0))
    analysis = AlpacaCandidateAnalysis(
        symbol="AAPL",
        timestamp="t",
        latest_close=100.0,
        atr=2.0,
        technical_ok=True,
        route="priority",
    )
    decision = AlpacaDecision(
        symbol="AAPL",
        decision="ALLOW",
        direction="LONG",
        score=0.8,
        route="priority",
    )
    scan_analysis = analysis.model_copy(update={"symbol": "PLTR", "route": "scan"})
    scan_decision = decision.model_copy(update={"symbol": "PLTR", "route": "scan"})

    r1 = desk.build_intent(decision, analysis, cycle_id="c1", buying_power=50_000.0)
    r2 = desk.build_intent(scan_decision, scan_analysis, cycle_id="c1", buying_power=50_000.0)
    assert r1 is not None and r2 is not None
    assert r1.notional_usd > r2.notional_usd


def test_options_gate_blocks_gamma_negative_regime() -> None:
    decision = AlpacaDecision(
        symbol="NVDA",
        decision="ALLOW",
        direction="LONG",
        score=0.7,
        route="priority",
    )
    bundle = PredictiveOptionsBundleReport(
        gamma_flip_level=500.0,
        is_gamma_negative_regime=True,
        shadow_delta_imbalance=0.0,
        zero_day_pinning_strike=0.0,
        speed_instability_warning=False,
        tail_risk_severity="LOW",
        zomma_risk_score=0.0,
    )
    gated = apply_equity_options_gate(decision, bundle)
    assert gated.decision == "BLOCK"
    assert gated.direction == "FLAT"
