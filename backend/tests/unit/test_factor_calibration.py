import numpy as np
import pytest

from backend.models.result import Result
from backend.quant_engine.math.predictive.factor_calibration import (
    CalibrationReport,
    FactorCalibrationEngine,
)


def test_factor_calibration_engine():
    engine = FactorCalibrationEngine()
    factor_names = [
        "momentum",
        "strength",
        "volatility",
        "put_call",
        "credit",
        "safe_haven",
        "event_risk",
    ]
    num_factors = len(factor_names)

    # Generate valid mock data
    np.random.seed(42)
    # 35 observations for 7 factors
    x_ok = np.random.randn(35, num_factors)
    # y target variable
    y_ok = np.random.randn(35)

    # 1. Test calculate_pca_weights
    res_pca = engine.calculate_pca_weights(x_ok, factor_names)
    assert isinstance(res_pca, Result)
    assert res_pca.is_success
    pca_weights = res_pca.unwrap()
    assert len(pca_weights) == num_factors
    for name in factor_names:
        assert name in pca_weights
        assert 0.0 <= pca_weights[name] <= 1.0
    assert pytest.approx(sum(pca_weights.values())) == 1.0

    # Test calculate_pca_weights with insufficient data (n < 30)
    res_pca_short = engine.calculate_pca_weights(x_ok[:20, :], factor_names)
    assert isinstance(res_pca_short, Result)
    assert res_pca_short.is_failure

    # 2. Test calculate_correlation_matrix
    res_corr = engine.calculate_correlation_matrix(x_ok, factor_names)
    assert isinstance(res_corr, Result)
    assert res_corr.is_success
    corr_matrix = res_corr.unwrap()
    assert len(corr_matrix) == num_factors
    for name1 in factor_names:
        assert name1 in corr_matrix
        for name2 in factor_names:
            assert name2 in corr_matrix[name1]
            assert -1.0 <= corr_matrix[name1][name2] <= 1.0

    # Test calculate_correlation_matrix with insufficient data (n < 10)
    res_corr_short = engine.calculate_correlation_matrix(x_ok[:5, :], factor_names)
    assert isinstance(res_corr_short, Result)
    assert res_corr_short.is_failure

    # 3. Test identify_redundant_factors
    res_redundant = engine.identify_redundant_factors(x_ok, factor_names, threshold=0.9)
    assert isinstance(res_redundant, Result)
    assert res_redundant.is_success
    assert isinstance(res_redundant.unwrap(), list)

    # 4. Test optimize_for_prediction
    res_opt = engine.optimize_for_prediction(x_ok, y_ok, factor_names)
    assert isinstance(res_opt, Result)
    assert res_opt.is_success
    opt_weights = res_opt.unwrap()
    assert len(opt_weights) == num_factors
    assert pytest.approx(sum(opt_weights.values())) == 1.0

    # Test optimize_for_prediction with insufficient data (n < 30)
    res_opt_short = engine.optimize_for_prediction(x_ok[:20, :], y_ok[:20], factor_names)
    assert isinstance(res_opt_short, Result)
    assert res_opt_short.is_failure

    # 5. Test get_calibration_report
    res_report = engine.get_calibration_report(x_ok, y_ok, factor_names)
    assert isinstance(res_report, Result)
    assert res_report.is_success
    report = res_report.unwrap()
    assert isinstance(report, CalibrationReport)
    assert report.observation_count == 35
    assert len(report.pca_weights) == num_factors
    assert len(report.optimized_weights) == num_factors
    assert len(report.equal_weights) == num_factors
    assert isinstance(report.recommendations, list)

    # Test get_calibration_report with insufficient data
    res_report_short = engine.get_calibration_report(x_ok[:20, :], y_ok[:20], factor_names)
    assert isinstance(res_report_short, Result)
    assert res_report_short.is_failure
