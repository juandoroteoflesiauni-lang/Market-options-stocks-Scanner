import numpy as np
import pytest

from backend.engine.metrics.cnn_fear_greed import (
    AlternativeFearGreedSource,
    CNNFearGreedFetcher,
)


def test_approximate_fg_success():
    source = AlternativeFearGreedSource()

    # spy_prices: SPY is slightly above its 125-day MA -> bullish/greed
    # 130 elements: element 0 (current) is 105.0, elements 1 to 129 are 100.0
    spy_prices = np.ones(130, dtype=np.float64) * 100.0
    spy_prices[0] = 105.0

    # vix_prices: VIX is slightly below its 50-day MA -> greed
    # 60 elements: element 0 is 18.0, elements 1 to 59 are 20.0
    vix_prices = np.ones(60, dtype=np.float64) * 20.0
    vix_prices[0] = 18.0

    res = source.calculate_approximate_fg(spy_prices, vix_prices)
    assert res.is_success

    report = res.unwrap()
    assert report.source == "approximated"
    assert "momentum" in report.factors
    assert "volatility" in report.factors
    assert report.score > 50.0  # Should be in greed territory
    assert report.label in ("Greed", "Extreme Greed")


def test_approximate_fg_insufficient_data():
    source = AlternativeFearGreedSource()

    # 1. SPY prices too short
    spy_prices_short = np.ones(120, dtype=np.float64)
    vix_prices = np.ones(60, dtype=np.float64)

    res1 = source.calculate_approximate_fg(spy_prices_short, vix_prices)
    assert res1.is_failure
    assert "spy_prices" in res1.reason

    # 2. VIX prices too short
    spy_prices = np.ones(130, dtype=np.float64)
    vix_prices_short = np.ones(40, dtype=np.float64)

    res2 = source.calculate_approximate_fg(spy_prices, vix_prices_short)
    assert res2.is_failure
    assert "vix_prices" in res2.reason


def test_compare_with_ours_available():
    fetcher = CNNFearGreedFetcher()

    cnn_data = {"score": 60.0, "label": "Greed"}
    our_score = 55.0
    our_factors = {"momentum": 60.0, "volatility": 50.0}

    res = fetcher.compare_with_ours(cnn_data, our_score, our_factors)
    assert res.is_success

    report = res.unwrap()
    assert report.available is True
    assert report.cnn_score == 60.0
    assert report.our_score == 55.0
    # difference = our_score - cnn_score = 55.0 - 60.0 = -5.0
    assert report.difference == -5.0
    # discrepancy = abs(-5.0) / 60.0 * 100.0 = 8.3333%
    assert report.discrepancy_pct == pytest.approx(8.3333, abs=1e-4)
    # abs(difference) == 5.0 -> agreement == "medium" (since it's not < 5)
    assert report.agreement == "medium"


def test_compare_with_ours_unavailable():
    fetcher = CNNFearGreedFetcher()

    our_score = 40.0
    our_factors = {"momentum": 30.0, "volatility": 50.0}

    res = fetcher.compare_with_ours(None, our_score, our_factors)
    assert res.is_success

    report = res.unwrap()
    assert report.available is False
    assert report.our_score == 40.0
    assert report.our_label == "Fear"
    assert report.cnn_score is None
    assert "not available" in report.message
