"""AAA unit tests for backend.domain.volatility. # [TH][IM]"""

from __future__ import annotations

from backend.domain.volatility import (
    MacdResult,
    compute_atr,
    compute_macd,
    compute_relative_strength,
)


def test_compute_atr_returns_value_for_sufficient_bars() -> None:
    # ARRANGE
    highs = [float(10 + i) for i in range(40)]
    lows = [float(8 + i) for i in range(40)]
    closes = [float(9 + i) for i in range(40)]
    # ACT
    atr = compute_atr(highs, lows, closes, period=14)
    # ASSERT
    assert atr is not None
    assert atr > 0.0


def test_compute_atr_rejects_short_series() -> None:
    # ARRANGE
    highs = [10.0, 11.0]
    lows = [9.0, 10.0]
    closes = [9.5, 10.5]
    # ACT
    atr = compute_atr(highs, lows, closes, period=14)
    # ASSERT
    assert atr is None


def test_compute_atr_rejects_mismatched_lengths() -> None:
    # ARRANGE
    highs = [float(i) for i in range(40)]
    lows = [float(i) for i in range(39)]
    closes = [float(i) for i in range(40)]
    # ACT
    atr = compute_atr(highs, lows, closes, period=14)
    # ASSERT
    assert atr is None


def test_compute_macd_returns_triplet_for_uptrend() -> None:
    # ARRANGE
    closes = [float(100 + i) for i in range(60)]
    # ACT
    result = compute_macd(closes)
    # ASSERT
    assert isinstance(result, MacdResult)
    assert result.macd > 0.0  # rising series → positive MACD line


def test_compute_macd_rejects_short_series() -> None:
    # ARRANGE
    closes = [float(i) for i in range(10)]
    # ACT
    result = compute_macd(closes)
    # ASSERT
    assert result is None


def test_compute_relative_strength_positive_when_symbol_outperforms() -> None:
    # ARRANGE
    symbol = [float(100 + 2 * i) for i in range(30)]  # +2/bar
    benchmark = [float(100 + i) for i in range(30)]  # +1/bar
    # ACT
    rs = compute_relative_strength(symbol, benchmark, lookback=20)
    # ASSERT
    assert rs is not None
    assert rs > 0.0


def test_compute_relative_strength_negative_when_symbol_underperforms() -> None:
    # ARRANGE
    symbol = [float(100 + i) for i in range(30)]
    benchmark = [float(100 + 3 * i) for i in range(30)]
    # ACT
    rs = compute_relative_strength(symbol, benchmark, lookback=20)
    # ASSERT
    assert rs is not None
    assert rs < 0.0


def test_compute_relative_strength_rejects_short_series() -> None:
    # ARRANGE
    symbol = [100.0]
    benchmark = [100.0]
    # ACT
    rs = compute_relative_strength(symbol, benchmark, lookback=20)
    # ASSERT
    assert rs is None
