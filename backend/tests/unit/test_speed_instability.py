import numpy as np

from backend.quant_engine.math.technical.speed_instability import (
    SpeedInstabilityReport,
    analyze_speed_instability,
)


def test_speed_instability_success():
    # Spot price at 100.0, rate at 0.05
    spot = 100.0
    rate = 0.05

    # Columns: [strike, is_call (1.0 or 0.0), iv (sigma), time_to_expiry, open_interest]
    # We construct 5 rows (options legs) around spot
    chain_data = np.array(
        [
            [90.0, 0.0, 0.20, 0.25, 1000.0],  # 90 Put
            [95.0, 0.0, 0.22, 0.25, 1500.0],  # 95 Put
            [100.0, 1.0, 0.25, 0.25, 2000.0],  # 100 Call
            [105.0, 1.0, 0.27, 0.25, 1500.0],  # 105 Call
            [110.0, 1.0, 0.30, 0.25, 1000.0],  # 110 Call
        ],
        dtype=np.float64,
    )

    res = analyze_speed_instability(
        chain_data=chain_data,
        spot=spot,
        r=rate,
        profile_points=20,
    )

    assert res.is_success
    report = res.unwrap()
    assert isinstance(report, SpeedInstabilityReport)
    assert report.spot == spot
    assert report.summary.total_net_swx != 0.0
    assert report.summary.max_abs_swx_single_strike >= 0.0
    assert len(report.zones) <= 3
    assert len(report.profile) == 20
    assert len(report.speed_by_strike) == 5
    assert len(report.speed_decay) <= 3
    assert len(report.scatter) == 5


def test_speed_instability_truncation():
    spot = 100.0
    rate = 0.05
    # 6 options but max_legs = 3.
    # The 3 with highest open interest should be selected: indices with OI = 2000, 1500, 1200
    chain_data = np.array(
        [
            [90.0, 0.0, 0.20, 0.25, 100.0],  # OI = 100
            [95.0, 0.0, 0.22, 0.25, 1200.0],  # OI = 1200
            [100.0, 1.0, 0.25, 0.25, 2000.0],  # OI = 2000
            [105.0, 1.0, 0.27, 0.25, 1500.0],  # OI = 1500
            [110.0, 1.0, 0.30, 0.25, 200.0],  # OI = 200
            [115.0, 1.0, 0.35, 0.25, 300.0],  # OI = 300
        ],
        dtype=np.float64,
    )

    res = analyze_speed_instability(
        chain_data=chain_data,
        spot=spot,
        r=rate,
        max_legs=3,
    )
    assert res.is_success
    report = res.unwrap()
    assert len(report.scatter) == 3

    strikes = [p.strike for p in report.scatter]
    # Top 3: 100.0, 105.0, 95.0
    assert 100.0 in strikes
    assert 105.0 in strikes
    assert 95.0 in strikes
    assert 90.0 not in strikes
    assert 110.0 not in strikes
    assert 115.0 not in strikes


def test_speed_instability_validations():
    spot = 100.0
    rate = 0.05
    chain_data = np.array(
        [
            [100.0, 1.0, 0.25, 0.25, 1000.0],
        ],
        dtype=np.float64,
    )

    # 1. None chain_data
    res = analyze_speed_instability(None, spot, rate)  # type: ignore
    assert res.is_failure
    assert "chain_data must not be None" in res.reason

    # 2. Invalid dimensions
    res = analyze_speed_instability(np.zeros((5, 4)), spot, rate)
    assert res.is_failure
    assert "at least 5 columns" in res.reason

    # 3. Invalid spot price
    res = analyze_speed_instability(chain_data, 0.0, rate)
    assert res.is_failure
    assert "spot price" in res.reason

    # 4. Invalid rate
    res = analyze_speed_instability(chain_data, spot, -0.01)
    assert res.is_failure
    assert "interest rate" in res.reason

    # 5. Zero/Negative Open Interest
    res = analyze_speed_instability(np.array([[100.0, 1.0, 0.25, 0.25, 0.0]]), spot, rate)
    assert res.is_failure
    assert "zero_oi" in res.reason
