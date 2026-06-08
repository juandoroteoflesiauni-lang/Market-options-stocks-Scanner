import pytest

from src.quant_engine.math.predictive.feedback_calibration import (
    FeedbackCalibration,
    FeedbackMetrics,
    ProjectionRecord,
)


def test_feedback_calibration_success_hit():
    engine = FeedbackCalibration()

    # Model is bullish (kelly_full > 0.1)
    history = [ProjectionRecord(context_price=100.0, kelly_full=0.5)]
    # Actual price went up (realized return > 0)
    current_price = 105.0

    res = engine.calculate_model_error(history, current_price)
    assert res.is_success

    metrics = res.unwrap()
    assert metrics.is_hit is True
    assert metrics.error_factor == 1.0
    assert metrics.realized_return == pytest.approx(0.05)
    # bias_adjustment = realized_return * 0.1 = 0.05 * 0.1 = 0.005
    assert metrics.bias == pytest.approx(0.005)


def test_feedback_calibration_success_miss():
    engine = FeedbackCalibration()

    # Model is bearish (kelly_full <= 0.1)
    history = [ProjectionRecord(context_price=100.0, kelly_full=0.0)]
    # Actual price went up (realized return > 0)
    current_price = 105.0

    res = engine.calculate_model_error(history, current_price)
    assert res.is_success

    metrics = res.unwrap()
    assert metrics.is_hit is False
    assert metrics.error_factor == 1.25
    assert metrics.realized_return == pytest.approx(0.05)
    assert metrics.bias == pytest.approx(0.005)


def test_feedback_calibration_empty_history():
    engine = FeedbackCalibration()

    res = engine.calculate_model_error([], 105.0)
    assert res.is_success

    metrics = res.unwrap()
    assert metrics.bias == 0.0
    assert metrics.is_hit is False
    assert metrics.error_factor == 1.0
    assert metrics.realized_return == 0.0


def test_feedback_calibration_invalid_prices():
    engine = FeedbackCalibration()

    # 1. Invalid current_price
    res = engine.calculate_model_error([], -5.0)
    assert res.is_failure
    assert "current_price" in res.reason

    # 2. Invalid context_price in history
    history = [ProjectionRecord(context_price=-10.0, kelly_full=0.5)]
    res2 = engine.calculate_model_error(history, 100.0)
    assert res2.is_failure
    assert "context_price" in res2.reason


def test_feedback_calibration_parameter_adaptation():
    engine = FeedbackCalibration()

    base_params = {"mu_target": 0.05, "vov": 0.20, "jump_intensity": 1.5}

    # Case A: Normal hit, return under threshold
    feedback_a = FeedbackMetrics(bias=0.01, is_hit=True, error_factor=1.0, realized_return=0.02)
    res_a = engine.adapt_parameters(base_params, feedback_a)
    assert res_a.is_success

    adj_a = res_a.unwrap()
    assert adj_a["mu_target"] == pytest.approx(0.06)
    assert adj_a["vov"] == pytest.approx(0.20)
    assert adj_a["jump_intensity"] == pytest.approx(1.5)

    # Case B: Miss, return over threshold (jump intensity should trigger)
    feedback_b = FeedbackMetrics(bias=-0.02, is_hit=False, error_factor=1.25, realized_return=0.10)
    res_b = engine.adapt_parameters(base_params, feedback_b)
    assert res_b.is_success

    adj_b = res_b.unwrap()
    assert adj_b["mu_target"] == pytest.approx(0.03)
    assert adj_b["vov"] == pytest.approx(0.25)
    # jump_intensity adjusts: 1.5 * 1.2 = 1.8
    assert adj_b["jump_intensity"] == pytest.approx(1.8)


def test_feedback_calibration_empty_params():
    engine = FeedbackCalibration()
    feedback = FeedbackMetrics(bias=0.01, is_hit=True, error_factor=1.0, realized_return=0.02)
    res = engine.adapt_parameters({}, feedback)
    assert res.is_failure
    assert "empty" in res.reason
