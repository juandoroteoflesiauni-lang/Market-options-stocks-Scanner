import numpy as np

from src.quant_engine.math.predictive.tail_risk import TailRiskEngine


def test_tail_risk_analysis_success():
    engine = TailRiskEngine()

    # Create dummy options chain data (10 strikes, 7 columns)
    # Columns: [strike, iv, is_call, delta, spot_price, call_price, put_price]
    spot = 100.0
    strikes = np.array([90.0, 95.0, 98.0, 100.0, 102.0, 105.0, 110.0], dtype=np.float64)
    n = len(strikes)

    # Let's create CALL options (is_call = 1.0) and PUT options (is_call = 0.0)
    # We will stack them
    call_rows = []
    put_rows = []

    for k in strikes:
        # Call row
        iv_call = 0.20 + (k - spot) * 0.002  # simple skew
        delta_call = 0.5 - (k - spot) * 0.01
        call_price = max(0.5, spot - k if k < spot else 0.5)
        call_rows.append([k, iv_call, 1.0, delta_call, spot, call_price, np.nan])

        # Put row
        iv_put = 0.22 - (k - spot) * 0.003
        delta_put = -0.5 - (k - spot) * 0.01
        put_price = max(0.5, k - spot if k > spot else 0.5)
        put_rows.append([k, iv_put, 0.0, delta_put, spot, np.nan, put_price])

    options_chain = np.vstack([call_rows, put_rows])

    res = engine.analyze_tail_risk(
        options_chain=options_chain,
        spot=spot,
        rate=0.04,
        tte=0.1,
    )

    assert res.is_success
    report = res.unwrap()
    assert report.spot == spot
    assert len(report.observed) == 2 * n
    assert len(report.smile_spline) == 140
    assert len(report.curvature) == 140

    # Test nested models properties
    assert report.metrics.skew_25d != 0
    assert report.metrics.convexity_25d != 0
    assert report.alert.level in ("NORMAL", "ELEVATED", "CATASTROPHE_IMMINENT")
    assert report.risk_reversal.direction in ("BAJISTA", "ALCISTA")
    assert report.directional_signal >= -1.0
    assert report.directional_signal <= 1.0


def test_tail_risk_analysis_failures():
    engine = TailRiskEngine()

    # 1. Invalid spot price
    res1 = engine.analyze_tail_risk(
        options_chain=np.zeros((5, 7)),
        spot=0.0,
        rate=0.04,
        tte=0.1,
    )
    assert res1.is_failure
    assert "spot price" in res1.reason

    # 2. Too few strikes
    # Just 2 strikes
    bad_chain = np.array(
        [
            [95.0, 0.2, 1.0, 0.5, 100.0, 5.0, np.nan],
            [105.0, 0.2, 1.0, 0.5, 100.0, 1.0, np.nan],
        ],
        dtype=np.float64,
    )
    res2 = engine.analyze_tail_risk(
        options_chain=bad_chain,
        spot=100.0,
        rate=0.04,
        tte=0.1,
    )
    assert res2.is_failure
    assert "At least 4 unique strikes" in res2.reason

    # 3. None inputs
    res3 = engine.analyze_tail_risk(
        options_chain=None,  # type: ignore
        spot=100.0,
        rate=0.04,
        tte=0.1,
    )
    assert res3.is_failure
    assert "must not be None" in res3.reason
