import numpy as np
import pytest

from backend.engine.metrics.portfolio_optimizer import (
    BlackLittermanOptimizer,
    calculate_covariance,
)


def test_calculate_covariance_success():
    # 2 assets, 5 periods of returns
    returns_matrix = np.array(
        [[0.01, 0.02, -0.01, 0.03, 0.01], [-0.02, 0.01, 0.03, -0.01, 0.02]],
        dtype=np.float64,
    )
    tickers = ["AAPL", "MSFT"]

    res = calculate_covariance(returns_matrix, tickers)
    assert res.is_success

    ticks, cov = res.unwrap()
    assert ticks == tickers
    assert cov.shape == (2, 2)
    # Annualized diagonal element (variance) should be positive
    assert cov[0, 0] > 0.0
    assert cov[1, 1] > 0.0


def test_calculate_covariance_failures():
    # 1. Dimension mismatch
    returns_matrix = np.array([[0.01, 0.02]], dtype=np.float64)
    tickers = ["AAPL", "MSFT"]  # 2 tickers, but returns has 1 row
    res1 = calculate_covariance(returns_matrix, tickers)
    assert res1.is_failure
    assert "dimension" in res1.reason

    # 2. Too few periods of history
    returns_matrix_short = np.array([[0.01], [0.02]], dtype=np.float64)
    res2 = calculate_covariance(returns_matrix_short, tickers)
    assert res2.is_failure
    assert "periods" in res2.reason


def test_black_litterman_optimization_success():
    optimizer = BlackLittermanOptimizer(risk_aversion=3.0, tau=0.05)

    tickers = ["AAPL", "MSFT", "GOOG"]
    # Positive definite 3x3 covariance matrix
    cov_matrix = np.array(
        [[0.1, 0.02, 0.01], [0.02, 0.15, 0.03], [0.01, 0.03, 0.12]],
        dtype=np.float64,
    )
    prior_returns = np.array([0.08, 0.12, 0.10], dtype=np.float64)
    views = np.array([0.10, 0.14, 0.11], dtype=np.float64)
    confidences = np.array([0.8, 0.9, 0.85], dtype=np.float64)

    res = optimizer.optimize(
        tickers=tickers,
        cov_matrix=cov_matrix,
        prior_returns=prior_returns,
        views=views,
        confidences=confidences,
    )
    assert res.is_success

    report = res.unwrap()
    assert report.tickers == tickers
    assert len(report.weights) == 3
    # Verify SLSQP constraints: weights must sum to 1.0 (or extremely close)
    total_weight = sum(report.weights.values())
    assert total_weight == pytest.approx(1.0, abs=1e-5)

    # Weights must not exceed 40% upper bound (0.4) for either asset
    for w in report.weights.values():
        assert w <= 0.40001
        assert w >= -1e-9

    assert report.expected_return > 0.0
    assert report.expected_volatility > 0.0


def test_black_litterman_optimization_failures():
    optimizer = BlackLittermanOptimizer(risk_aversion=0.0, tau=0.05)
    tickers = ["AAPL", "MSFT"]
    cov_matrix = np.array([[0.1, 0.02], [0.02, 0.15]], dtype=np.float64)
    prior_returns = np.array([0.08, 0.12], dtype=np.float64)
    views = np.array([0.10, 0.14], dtype=np.float64)
    confidences = np.array([0.8, 0.9], dtype=np.float64)

    # 1. Invalid risk_aversion
    res1 = optimizer.optimize(
        tickers=tickers,
        cov_matrix=cov_matrix,
        prior_returns=prior_returns,
        views=views,
        confidences=confidences,
    )
    assert res1.is_failure
    assert "risk_aversion" in res1.reason

    # 2. Mismatched shapes
    optimizer_valid = BlackLittermanOptimizer(risk_aversion=2.5, tau=0.05)
    confidences_short = np.array([0.8], dtype=np.float64)
    res2 = optimizer_valid.optimize(
        tickers=tickers,
        cov_matrix=cov_matrix,
        prior_returns=prior_returns,
        views=views,
        confidences=confidences_short,
    )
    assert res2.is_failure
    assert "confidences" in res2.reason
