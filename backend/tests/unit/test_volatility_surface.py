import numpy as np

from backend.quant_engine.math.options.volatility_surface import VolatilitySurfaceEngine


def test_volatility_surface_validation_errors():
    engine = VolatilitySurfaceEngine()

    # Empty array
    res = engine.analyze("AAPL", np.empty((0, 2)))
    assert res.is_failure
    assert "iv_data must contain at least 2 rows" in res.reason

    # Too short (1 row)
    res = engine.analyze("AAPL", np.array([[0.5, 0.4]]))
    assert res.is_failure
    assert "iv_data must contain at least 2 rows" in res.reason

    # Invalid columns shape
    res = engine.analyze("AAPL", np.ones((5, 3)))
    assert res.is_failure
    assert "must be a 2D array of shape" in res.reason

    # NaN in inputs
    res = engine.analyze("AAPL", np.array([[0.5, np.nan], [0.4, 0.3]]))
    assert res.is_failure
    assert "contains NaN values" in res.reason

    # Negative IV value
    res = engine.analyze("AAPL", np.array([[0.5, -0.1], [0.4, 0.3]]))
    assert res.is_failure
    assert "contains negative implied volatilities" in res.reason

    # Current call_iv is zero
    res = engine.analyze("AAPL", np.array([[0.5, 0.0], [0.4, 0.3]]))
    assert res.is_failure
    assert "Current call_iv is zero" in res.reason


def test_volatility_surface_analysis():
    engine = VolatilitySurfaceEngine()

    # 1. Neutral regime test
    # History of 6 points. Latest skew is middle-ranked.
    # Put IV, Call IV:
    iv_data_neutral = np.array(
        [
            [0.45, 0.40],  # Latest: skew = 0.05
            [0.50, 0.40],  # skew = 0.10
            [0.40, 0.40],  # skew = 0.00
            [0.48, 0.42],  # skew = 0.06
            [0.38, 0.40],  # skew = -0.02
            [0.35, 0.40],  # skew = -0.05
        ]
    )
    # Skews: [0.05, 0.10, 0.00, 0.06, -0.02, -0.05]
    # Sorted skews: -0.05, -0.02, 0.00, 0.05, 0.06, 0.10
    # Latest skew is 0.05.
    # Count of skews <= 0.05: -0.05, -0.02, 0.00, 0.05 -> 4 skews.
    # Percentile: 4 / 6 ≈ 0.6667
    # Ratio: 0.45 / 0.40 = 1.125
    res = engine.analyze("AAPL", iv_data_neutral)
    assert res.is_success
    report = res.unwrap()
    assert report.fear_regime == "NEUTRAL"
    assert report.risk_signal == "NEUTRAL"
    assert report.current_skew == 0.05
    assert report.skew_percentile == 0.6667
    assert report.put_call_iv_ratio == 1.125
    assert len(report.historical_skew) == 6
    assert report.historical_skew[0].periods_ago == 0
    assert report.historical_skew[0].put_iv == 0.45
    assert report.historical_skew[0].call_iv == 0.40
    assert round(report.historical_skew[0].skew, 4) == 0.05

    # 2. High skew regime test (percentile > 0.85)
    # Latest skew is the highest.
    iv_data_high_skew = np.array(
        [
            [0.60, 0.40],  # Latest: skew = 0.20
            [0.45, 0.40],  # skew = 0.05
            [0.40, 0.40],  # skew = 0.00
            [0.35, 0.40],  # skew = -0.05
            [0.30, 0.40],  # skew = -0.10
            [0.25, 0.40],  # skew = -0.15
        ]
    )
    res = engine.analyze("AAPL", iv_data_high_skew)
    assert res.is_success
    report = res.unwrap()
    assert report.fear_regime == "HIGH_SKEW"
    assert report.risk_signal == "BEARISH_HEDGING"

    # 3. Extreme put demand ratio test (ratio > 1.5)
    iv_data_extreme = np.array(
        [
            [0.70, 0.40],  # Latest: skew = 0.30, ratio = 1.75 > 1.5
            [0.45, 0.40],  # skew = 0.05
            [0.40, 0.40],  # skew = 0.00
            [0.35, 0.40],  # skew = -0.05
            [0.30, 0.40],  # skew = -0.10
            [0.25, 0.40],  # skew = -0.15
        ]
    )
    res = engine.analyze("AAPL", iv_data_extreme)
    assert res.is_success
    report = res.unwrap()
    assert report.risk_signal == "EXTREME_PUT_DEMAND"
