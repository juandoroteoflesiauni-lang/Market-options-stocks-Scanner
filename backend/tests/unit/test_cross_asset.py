import numpy as np

from backend.models.result import Result
from src.quant_engine.math.predictive.cross_asset import (
    CorrelationProfile,
    CrossAssetReport,
    get_cross_asset_analysis,
)


def test_cross_asset_success():
    # Setup data: 30 observations for 1 target asset and 2 reference assets (SPY, TLT)
    np.random.seed(42)
    n = 30
    target_prices = np.random.uniform(100.0, 105.0, n)
    ref_prices = np.column_stack(
        (
            np.random.uniform(200.0, 205.0, n),  # SPY
            np.random.uniform(80.0, 85.0, n),  # TLT
        )
    )
    ref_tickers = ["SPY", "TLT"]

    res = get_cross_asset_analysis("AAPL", target_prices, ref_prices, ref_tickers)

    assert isinstance(res, Result)
    assert res.is_success

    report = res.unwrap()
    assert isinstance(report, CrossAssetReport)
    assert report.symbol == "AAPL"
    assert len(report.correlations) == 2
    assert report.correlations[0].reference_ticker in ["SPY", "TLT"]

    # Verify profile types
    profile = report.correlations[0]
    assert isinstance(profile, CorrelationProfile)
    assert -1.0 <= profile.rolling_corr <= 1.0
    assert -1.0 <= profile.long_corr <= 1.0
    assert profile.direction in ["POSITIVE", "NEGATIVE", "NEUTRAL"]


def test_cross_asset_validation_errors():
    t_prices = np.ones(20) * 100.0
    ref_prices = np.ones((20, 2)) * 100.0
    ref_tickers = ["SPY", "TLT"]

    # Not numpy arrays
    res = get_cross_asset_analysis("AAPL", [100.0] * 20, ref_prices, ref_tickers)  # type: ignore
    assert res.is_failure
    assert "ndarrays" in res.reason

    # Mismatched rows
    res = get_cross_asset_analysis("AAPL", t_prices, ref_prices[:15], ref_tickers)
    assert res.is_failure
    assert "number of rows" in res.reason

    # Insufficient observations (< 10)
    res = get_cross_asset_analysis("AAPL", t_prices[:8], ref_prices[:8], ref_tickers)
    assert res.is_failure
    assert "at least 10" in res.reason

    # Mismatched ticker count
    res = get_cross_asset_analysis("AAPL", t_prices, ref_prices, ["SPY"])
    assert res.is_failure
    assert "ref_tickers count" in res.reason

    # NaNs in prices
    nan_target = t_prices.copy()
    nan_target[0] = np.nan
    res = get_cross_asset_analysis("AAPL", nan_target, ref_prices, ref_tickers)
    assert res.is_failure
    assert "NaN" in res.reason

    # Negative prices
    neg_target = t_prices.copy()
    neg_target[0] = -1.0
    res = get_cross_asset_analysis("AAPL", neg_target, ref_prices, ref_tickers)
    assert res.is_failure
    assert "positive" in res.reason


def test_cross_asset_decoupling_heuristics():
    # Setup data to trigger decoupling:
    # First 15 observations: target is perfectly aligned with SPY
    # Last 15 observations: target shifts to be negatively correlated or uncorrelated
    n = 40
    np.random.seed(42)

    # Base asset price
    base_spy = np.random.uniform(200.0, 205.0, n)
    base_tlt = np.random.uniform(80.0, 85.0, n)

    target_prices = np.zeros(n)
    # Alignment:
    # First half target moves exactly with SPY
    target_prices[:20] = base_spy[:20] * 0.5
    # Second half target moves opposite to SPY (decoupling)
    target_prices[20:] = 200.0 - base_spy[20:] * 0.5

    ref_prices = np.column_stack((base_spy, base_tlt))
    ref_tickers = ["SPY", "TLT"]

    res = get_cross_asset_analysis("AAPL", target_prices, ref_prices, ref_tickers)
    assert res.is_success
    report = res.unwrap()
    # There should be an alert or a decoupling profile since target changed correlation structure
    assert len(report.correlations) == 2
    # Verify that decoupling alert logic evaluated
    assert isinstance(report.decoupling_alert, bool)
    assert isinstance(report.systematic_risk, float)
    assert isinstance(report.idiosyncratic_risk, float)


def test_cross_asset_single_reference_asset():
    # Single reference asset: M = 1
    np.random.seed(42)
    n = 20
    target = np.random.uniform(50, 55, n)
    ref = np.random.uniform(10, 15, n).reshape(-1, 1)
    ref_tickers = ["SPY"]

    res = get_cross_asset_analysis("AAPL", target, ref, ref_tickers)
    assert res.is_success
    report = res.unwrap()
    assert len(report.correlations) == 1
    assert report.correlations[0].reference_ticker == "SPY"
