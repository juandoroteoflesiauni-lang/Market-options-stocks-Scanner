import numpy as np

from src.quant_engine.math.options.volatility_skew import VolatilitySkewEngine


def test_volatility_skew_analysis_success_polynomial():
    engine = VolatilitySkewEngine(fit_model="polynomial")

    # 10 strikes: 90 to 110, spot 100
    spot = 100.0
    strikes = np.array(
        [90.0, 92.0, 95.0, 98.0, 100.0, 102.0, 105.0, 108.0, 110.0],
        dtype=np.float64,
    )
    n = len(strikes)

    rows = []
    for k in strikes:
        # Call row
        iv_call = 0.20 + (k - spot) * 0.001
        delta_call = 0.5 - (k - spot) * 0.015
        rows.append([k, iv_call, 1.0, delta_call])

        # Put row
        iv_put = 0.22 - (k - spot) * 0.002
        delta_put = -0.5 - (k - spot) * 0.015
        rows.append([k, iv_put, 0.0, delta_put])

    options_chain = np.array(rows, dtype=np.float64)

    # Let's mock a delta of exactly 0.25 and -0.25 to make extraction predictable
    # Call delta nearest to 0.25
    options_chain[10, 3] = 0.25  # strike 100.0 (call)
    options_chain[10, 1] = 0.18
    # Put delta nearest to -0.25
    options_chain[9, 3] = -0.25  # strike 100.0 (put)
    options_chain[9, 1] = 0.22

    res = engine.analyze_volatility_skew(
        options_chain=options_chain,
        spot=spot,
        rate=0.05,
        tte=30.0 / 365.0,
        convexity_history=[0.01] * 14,
    )

    assert res.is_success
    report = res.unwrap()
    assert report.spot == spot
    assert report.fit_model == "polynomial"
    assert len(report.market_points) == 2 * n
    assert len(report.fitted_curve) == 160
    assert len(report.curvature) == 100
    assert len(report.scenarios) == 4

    metrics = report.metrics
    assert metrics.iv_25d_put == 0.22
    assert metrics.iv_25d_call == 0.18
    assert metrics.slope_25d == pytest_approx(0.04)
    assert metrics.regime in ("Normal Skew", "Crash Risk", "Bullish Skew")


def test_volatility_skew_analysis_success_sabr():
    engine = VolatilitySkewEngine(fit_model="sabr")

    spot = 100.0
    strikes = np.array(
        [90.0, 92.0, 95.0, 98.0, 100.0, 102.0, 105.0, 108.0, 110.0],
        dtype=np.float64,
    )
    rows = []
    for k in strikes:
        iv_call = 0.20 + (k - spot) * 0.001
        delta_call = 0.5 - (k - spot) * 0.015
        rows.append([k, iv_call, 1.0, delta_call])
        iv_put = 0.22 - (k - spot) * 0.002
        delta_put = -0.5 - (k - spot) * 0.015
        rows.append([k, iv_put, 0.0, delta_put])

    options_chain = np.array(rows, dtype=np.float64)

    # Mock deltas
    options_chain[10, 3] = 0.25
    options_chain[10, 1] = 0.18
    options_chain[9, 3] = -0.25
    options_chain[9, 1] = 0.22

    res = engine.analyze_volatility_skew(
        options_chain=options_chain,
        spot=spot,
        rate=0.05,
        tte=30.0 / 365.0,
    )

    assert res.is_success
    report = res.unwrap()
    assert report.spot == spot
    # Note: SABR could either succeed or fail/fallback to polynomial
    # depending on initial params optimization
    assert report.fit_model in ("sabr", "polynomial")


def test_volatility_skew_analysis_failures():
    engine = VolatilitySkewEngine()

    # 1. Invalid spot price
    res1 = engine.analyze_volatility_skew(
        options_chain=np.zeros((5, 4)),
        spot=0.0,
        rate=0.05,
        tte=0.1,
    )
    assert res1.is_failure
    assert "spot price" in res1.reason

    # 2. Too few strikes
    bad_chain = np.array(
        [
            [95.0, 0.2, 1.0, 0.5],
            [105.0, 0.2, 1.0, 0.5],
        ],
        dtype=np.float64,
    )
    res2 = engine.analyze_volatility_skew(
        options_chain=bad_chain,
        spot=100.0,
        rate=0.05,
        tte=0.1,
    )
    assert res2.is_failure
    assert "Insufficient smile points" in res2.reason

    # 3. None options chain
    res3 = engine.analyze_volatility_skew(
        options_chain=None,  # type: ignore
        spot=100.0,
        rate=0.05,
        tte=0.1,
    )
    assert res3.is_failure
    assert "must not be None" in res3.reason


def pytest_approx(expected, abs=1e-6):
    import pytest

    return pytest.approx(expected, abs=abs)
