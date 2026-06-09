import numpy as np

from src.quant_engine.engines.options.zero_day import ZeroDayReport, analyze_zero_day


def test_zero_day_success():
    # Spot price at 5000.0, rate at 0.04
    spot = 5000.0
    rate = 0.04
    minutes_to_close = 120.0

    # Columns: [strike, is_call (1.0/0.0), bid, ask, last, vol, oi, delta, gamma, iv]
    # We construct 6 rows (options legs) around spot
    chain_data = np.array(
        [
            [4900.0, 0.0, 5.0, 5.5, 5.2, 500.0, 1000.0, -0.15, 0.0005, 0.25],
            [4950.0, 0.0, 12.0, 12.5, 12.2, 1200.0, 1500.0, -0.30, 0.0010, 0.25],
            [5000.0, 1.0, 25.0, 25.5, 25.2, 2000.0, 2000.0, 0.50, 0.0015, 0.25],
            [5000.0, 0.0, 20.0, 20.5, 20.2, 1800.0, 2000.0, -0.50, 0.0015, 0.25],
            [5050.0, 1.0, 10.0, 10.5, 10.2, 800.0, 1500.0, 0.25, 0.0008, 0.25],
            [5100.0, 1.0, 3.0, 3.5, 3.2, 300.0, 1000.0, 0.10, 0.0004, 0.25],
        ],
        dtype=np.float64,
    )

    res = analyze_zero_day(
        chain_data=chain_data,
        spot=spot,
        r=rate,
        minutes_to_close=minutes_to_close,
        spot_multiplier=100,
    )

    assert res.is_success
    report = res.unwrap()
    assert isinstance(report, ZeroDayReport)
    assert report.spot == spot
    assert report.minutes_to_close == minutes_to_close
    assert report.gamma_flip > 0.0
    assert report.call_wall > 0.0
    assert report.put_wall > 0.0
    assert len(report.gex_bars) == 5  # 5 unique strikes
    assert len(report.pin_curve) == 5
    assert len(report.gravity_map) == 5
    assert isinstance(report.vanna_pressure_bn, float)
    assert isinstance(report.charm_decay_mm, float)


def test_zero_day_validations():
    spot = 5000.0
    rate = 0.04
    minutes_to_close = 120.0
    chain_data = np.array(
        [[5000.0, 1.0, 25.0, 25.5, 25.2, 2000.0, 2000.0, 0.50, 0.0015, 0.25]],
        dtype=np.float64,
    )

    # 1. None chain_data
    res = analyze_zero_day(None, spot, rate, minutes_to_close)  # type: ignore
    assert res.is_failure
    assert "chain_data must not be None" in res.reason

    # 2. Invalid dimensions
    res = analyze_zero_day(np.zeros((5, 9)), spot, rate, minutes_to_close)
    assert res.is_failure
    assert "at least 10 columns" in res.reason

    # 3. Invalid spot price
    res = analyze_zero_day(chain_data, 0.0, rate, minutes_to_close)
    assert res.is_failure
    assert "spot price" in res.reason

    # 4. Invalid rate
    res = analyze_zero_day(chain_data, spot, -0.01, minutes_to_close)
    assert res.is_failure
    assert "interest rate" in res.reason

    # 5. Invalid minutes to close
    res = analyze_zero_day(chain_data, spot, rate, -10.0)
    assert res.is_failure
    assert "minutes to close" in res.reason

    # 6. Zero/Negative Open Interest
    res = analyze_zero_day(
        np.array([[5000.0, 1.0, 25.0, 25.5, 25.2, 2000.0, 0.0, 0.50, 0.0015, 0.25]]),
        spot,
        rate,
        minutes_to_close,
    )
    assert res.is_failure
    assert "zero_oi" in res.reason
