import numpy as np
import pytest

from backend.models.result import Result
from src.quant_engine.engines.predictive.ml_optimizer import MLOptimizer, OptimizationResult


def test_ml_optimizer():
    optimizer = MLOptimizer()
    feature_names = [
        "momentum",
        "strength",
        "volatility",
        "put_call",
        "credit",
        "safe_haven",
        "event_risk",
    ]
    num_features = len(feature_names)

    # 1. Test optimize_ridge
    # needs n >= 10
    np.random.seed(42)
    x_15 = np.random.randn(15, num_features)
    y_15 = np.random.randn(15)

    res_ridge = optimizer.optimize_ridge(x_15, y_15, feature_names)
    assert isinstance(res_ridge, Result)
    assert res_ridge.is_success
    opt_ridge = res_ridge.unwrap()
    assert isinstance(opt_ridge, OptimizationResult)
    assert opt_ridge.method == "ridge"
    assert len(opt_ridge.weights) == num_features
    assert pytest.approx(sum(opt_ridge.weights.values())) == 1.0
    assert len(opt_ridge.feature_importance) == num_features

    # Test optimize_ridge with insufficient data (n < 10)
    res_ridge_short = optimizer.optimize_ridge(x_15[:5, :], y_15[:5], feature_names)
    assert isinstance(res_ridge_short, Result)
    assert res_ridge_short.is_failure

    # 2. Test optimize_random_forest
    # needs n >= 20
    x_25 = np.random.randn(25, num_features)
    y_25 = np.random.randn(25)

    res_rf = optimizer.optimize_random_forest(x_25, y_25, feature_names)
    assert isinstance(res_rf, Result)
    assert res_rf.is_success
    opt_rf = res_rf.unwrap()
    assert isinstance(opt_rf, OptimizationResult)
    assert opt_rf.method == "random_forest"
    assert len(opt_rf.weights) == num_features
    assert pytest.approx(sum(opt_rf.weights.values())) == 1.0

    # Test optimize_random_forest with insufficient data (n < 20)
    res_rf_short = optimizer.optimize_random_forest(x_15, y_15, feature_names)
    assert isinstance(res_rf_short, Result)
    assert res_rf_short.is_failure

    # 3. Test optimize_gradient_boosting
    # needs n >= 30
    x_35 = np.random.randn(35, num_features)
    y_35 = np.random.randn(35)

    res_gb = optimizer.optimize_gradient_boosting(x_35, y_35, feature_names)
    assert isinstance(res_gb, Result)
    assert res_gb.is_success
    opt_gb = res_gb.unwrap()
    assert isinstance(opt_gb, OptimizationResult)
    assert opt_gb.method == "gradient_boosting"
    assert len(opt_gb.weights) == num_features
    assert pytest.approx(sum(opt_gb.weights.values())) == 1.0

    # Test optimize_gradient_boosting with insufficient data (n < 30)
    res_gb_short = optimizer.optimize_gradient_boosting(x_25, y_25, feature_names)
    assert isinstance(res_gb_short, Result)
    assert res_gb_short.is_failure

    # 4. Test get_optimal_weights auto selection
    # N = 15 -> Ridge
    res_auto_ridge = optimizer.get_optimal_weights(x_15, y_15, feature_names, method="auto")
    assert res_auto_ridge.is_success
    assert res_auto_ridge.unwrap().method == "ridge"

    # N = 75 -> Random Forest
    x_75 = np.random.randn(75, num_features)
    y_75 = np.random.randn(75)
    res_auto_rf = optimizer.get_optimal_weights(x_75, y_75, feature_names, method="auto")
    assert res_auto_rf.is_success
    assert res_auto_rf.unwrap().method == "random_forest"

    # N = 250 -> Gradient Boosting
    x_250 = np.random.randn(250, num_features)
    y_250 = np.random.randn(250)
    res_auto_gb = optimizer.get_optimal_weights(x_250, y_250, feature_names, method="auto")
    assert res_auto_gb.is_success
    assert res_auto_gb.unwrap().method == "gradient_boosting"
