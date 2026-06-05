import numpy as np
import pytest

from backend.engine.metrics.vol_term_structure import (
    VolTermStructureReport,
    analyze_vol_term_structure,
)
from backend.models.result import Result


def test_vol_term_structure_cubic_spline():
    # 5 points to trigger CubicSpline
    # sorted by DTE
    atm_curve = np.array(
        [
            [10.0, 0.20],
            [30.0, 0.22],
            [60.0, 0.25],
            [90.0, 0.28],
            [120.0, 0.30],
        ]
    )

    res = analyze_vol_term_structure(atm_curve, standard_tenors=[10, 30, 60, 90, 120])

    assert isinstance(res, Result)
    assert res.is_success

    report = res.unwrap()
    assert isinstance(report, VolTermStructureReport)
    assert report.short_tenor == 30
    assert report.long_tenor == 90

    # For standard DTEs matching the exact input, CubicSpline should match closely
    assert report.interpolated_ivs[10] == pytest.approx(0.20)
    assert report.interpolated_ivs[30] == pytest.approx(0.22)
    assert report.interpolated_ivs[60] == pytest.approx(0.25)
    assert report.interpolated_ivs[90] == pytest.approx(0.28)
    assert report.interpolated_ivs[120] == pytest.approx(0.30)

    # Verify metrics
    assert report.iv_short == pytest.approx(0.22)
    assert report.iv_long == pytest.approx(0.28)
    assert report.ratio == pytest.approx(0.22 / 0.28, abs=1e-4)

    expected_slope = (0.28 - 0.22) / (90 - 30)
    assert report.slope == pytest.approx(expected_slope)
    assert report.slope_bps == pytest.approx(expected_slope * 10000.0, abs=1e-3)

    # Curvature: mid_tenor between 30 and 90 is 60.
    # curvature = iv_mid - (iv_short + iv_long) / 2
    assert report.curvature == pytest.approx(0.25 - (0.22 + 0.28) / 2.0)


def test_vol_term_structure_linear_fallback():
    # 3 points to trigger numpy linear interpolation fallback
    atm_curve = np.array(
        [
            [10.0, 0.20],
            [50.0, 0.24],
            [100.0, 0.30],
        ]
    )

    res = analyze_vol_term_structure(
        atm_curve,
        standard_tenors=[30, 90],
        short_tenor=30,
        long_tenor=90,
    )

    assert isinstance(res, Result)
    assert res.is_success

    report = res.unwrap()
    assert report.short_tenor == 30
    assert report.long_tenor == 90

    # Linear interp:
    # 30d is halfway between 10d (0.20) and 50d (0.24) -> 0.22
    assert report.iv_short == pytest.approx(0.22)
    # 90d is 4/5 of the way from 50d (0.24) to 100d (0.30) -> 0.24 + (90-50)/(100-50)*(0.30-0.24) = 0.24 + 0.8 * 0.06 = 0.288
    assert report.iv_long == pytest.approx(0.288)


def test_vol_term_structure_zscore():
    atm_curve = np.array(
        [
            [10.0, 0.20],
            [30.0, 0.22],
            [60.0, 0.25],
            [90.0, 0.28],
        ]
    )

    # Provide 4 historical slopes. The window will be size 5 after adding the current slope.
    historical_slopes = np.array([0.0005, 0.0007, 0.0006, 0.0008])
    res = analyze_vol_term_structure(
        atm_curve,
        historical_slopes=historical_slopes,
        short_tenor=30,
        long_tenor=90,
        zscore_window=10,
        min_periods=5,
    )

    assert res.is_success
    report = res.unwrap()

    # Current slope is (0.28 - 0.22) / 60 = 0.0010
    current_slope = (0.28 - 0.22) / 60
    combined = np.append(historical_slopes, current_slope)
    mean = np.mean(combined)
    std = np.std(combined, ddof=1)
    expected_zscore = (current_slope - mean) / std

    assert report.slope_zscore == pytest.approx(expected_zscore, abs=1e-3)


def test_vol_term_structure_validation():
    # Empty atm_curve
    res = analyze_vol_term_structure(np.empty((0, 2)))
    assert res.is_failure
    assert "empty" in res.reason

    # Incorrect shape
    res = analyze_vol_term_structure(np.zeros((5, 3)))
    assert res.is_failure
    assert "shape" in res.reason

    # NaN values
    nan_curve = np.array([[10.0, np.nan], [30.0, 0.22]])
    res = analyze_vol_term_structure(nan_curve)
    assert res.is_failure
    assert "NaN" in res.reason

    # No valid points after filter (e.g. IVs too high/low)
    invalid_iv_curve = np.array([[10.0, 0.01], [30.0, 2.50]])
    res = analyze_vol_term_structure(invalid_iv_curve)
    assert res.is_failure
    assert "No valid points" in res.reason
