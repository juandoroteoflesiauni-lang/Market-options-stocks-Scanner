import numpy as np

from backend.models.result import Result
from backend.quant_engine.math.predictive.correlation_analyzer import (
    CorrelationAnalysis,
    CorrelationAnalyzer,
    get_correlation_analysis,
)


def test_correlation_analyzer_success():
    # Setup data where prices are positively correlated with lagged scores
    np.random.seed(42)
    n = 50
    # Create synthetic series
    fg_scores = np.random.uniform(10.0, 90.0, n)
    prices = 100.0 + 0.5 * np.cumsum(fg_scores - 50.0) + np.random.normal(0, 5, n)

    # Make sure prices are strictly positive
    prices = np.clip(prices, a_min=10.0, a_max=None)

    data = np.column_stack((fg_scores, prices))
    horizon = 5
    min_samples = 30

    res = get_correlation_analysis(data, horizon, min_samples)

    assert isinstance(res, Result)
    assert res.is_success

    report = res.unwrap()
    assert isinstance(report, CorrelationAnalysis)
    assert report.optimal_horizon == horizon
    assert report.sample_size == n - horizon
    assert -1.0 <= report.fgspy_correlation <= 1.0


def test_correlation_analyzer_validation_errors():
    # Empty data
    res = get_correlation_analysis(np.empty((0, 2)))
    assert res.is_failure
    assert "empty" in res.reason or "samples" in res.reason

    # Not numpy array
    res = get_correlation_analysis([[50.0, 100.0]])  # type: ignore
    assert res.is_failure
    assert "ndarray" in res.reason

    # Wrong dimensions
    res = get_correlation_analysis(np.zeros((100, 3)))
    assert res.is_failure
    assert "shape" in res.reason

    # NaN values
    nan_data = np.zeros((100, 2))
    nan_data[0, 0] = np.nan
    res = get_correlation_analysis(nan_data)
    assert res.is_failure
    assert "NaN values" in res.reason

    # Negative prices
    neg_data = np.ones((100, 2)) * 50.0
    neg_data[:, 1] = -10.0
    res = get_correlation_analysis(neg_data)
    assert res.is_failure
    assert "asset_price" in res.reason

    # Insufficient samples
    insuf_data = np.ones((10, 2)) * 50.0
    res = get_correlation_analysis(insuf_data, horizon=5, min_samples=30)
    assert res.is_failure
    assert "Insufficient data" in res.reason


def test_correlation_analyzer_optimal_horizon():
    # Create correlation profile where horizon=10 has perfect positive correlation
    # prices[t+10] = prices[t] * (1.0 + returns)
    n = 60
    fg_scores = np.arange(n, dtype=np.float64)
    # returns will align perfectly at horizon=10
    prices = np.ones(n, dtype=np.float64) * 100.0
    for i in range(10, n):
        # make future returns at horizon=10 perfectly linear to fg_scores
        prices[i] = prices[i - 10] * (1.0 + 0.01 * fg_scores[i - 10])

    data = np.column_stack((fg_scores, prices))
    analyzer = CorrelationAnalyzer()

    # Test optimal horizon search
    res = analyzer.find_optimal_horizon(data, horizons=[1, 5, 10, 20], min_samples=30)
    assert res.is_success
    assert res.unwrap() == 10


def test_correlation_analyzer_zero_variance():
    # If scores are constant, corrcoef returns NaN which should be corrected to 0.0
    n = 40
    fg_scores = np.ones(n) * 50.0
    prices = np.arange(1, n + 1, dtype=np.float64) * 10.0

    data = np.column_stack((fg_scores, prices))
    res = get_correlation_analysis(data, horizon=5, min_samples=30)
    assert res.is_success
    assert res.unwrap().fgspy_correlation == 0.0
