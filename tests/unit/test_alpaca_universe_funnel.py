"""AAA unit tests for backend.services.alpaca_universe_funnel. # [TH][IM]"""

from __future__ import annotations

from backend.services.alpaca_universe_funnel import FunnelConfig, SymbolBars, run_funnel

_BARS = 60


def _benchmark() -> list[float]:
    return [float(100 + i) for i in range(_BARS)]  # +1/bar


def _strong_symbol(symbol: str, slope: float, base_vol: float) -> SymbolBars:
    closes = tuple(float(100 + slope * i) for i in range(_BARS))
    highs = tuple(c + 1.0 for c in closes)
    lows = tuple(c - 1.0 for c in closes)
    volumes = tuple(base_vol for _ in range(_BARS))
    return SymbolBars(symbol=symbol, highs=highs, lows=lows, closes=closes, volumes=volumes)


def test_run_funnel_drops_illiquid_symbol() -> None:
    # ARRANGE: volume below the liquidity floor
    illiquid = _strong_symbol("ILLQ", slope=2.0, base_vol=1_000.0)
    config = FunnelConfig(min_avg_volume=500_000.0)
    # ACT
    selected = run_funnel([illiquid], _benchmark(), config)
    # ASSERT
    assert selected == []


def test_run_funnel_ranks_outperformer_first() -> None:
    # ARRANGE: strong RS+MACD vs a flat laggard, both liquid
    leader = _strong_symbol("LEAD", slope=3.0, base_vol=5_000_000.0)
    laggard = _strong_symbol("LAGG", slope=0.2, base_vol=5_000_000.0)
    # ACT
    selected = run_funnel([laggard, leader], _benchmark())
    # ASSERT
    assert selected[0] == "LEAD"


def test_run_funnel_respects_top_n() -> None:
    # ARRANGE
    candidates = [
        _strong_symbol(f"S{i}", slope=2.0 + i * 0.01, base_vol=5_000_000.0) for i in range(10)
    ]
    config = FunnelConfig(top_n=3)
    # ACT
    selected = run_funnel(candidates, _benchmark(), config)
    # ASSERT
    assert len(selected) == 3


def test_run_funnel_skips_short_series() -> None:
    # ARRANGE
    short = SymbolBars(
        symbol="SHRT",
        highs=(1.0, 2.0),
        lows=(0.5, 1.5),
        closes=(1.0, 1.8),
        volumes=(5_000_000.0, 5_000_000.0),
    )
    # ACT
    selected = run_funnel([short], _benchmark())
    # ASSERT
    assert selected == []
