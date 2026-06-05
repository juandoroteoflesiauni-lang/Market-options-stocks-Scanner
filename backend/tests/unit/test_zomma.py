import numpy as np

from backend.engine.metrics.zomma import ZommaEngine, ZommaReport


def test_zomma_analysis_success():
    engine = ZommaEngine(contract_size=100)

    # Spot price at 100
    spot = 100.0
    tte = 0.5
    rate = 0.05

    # Strikes: 90, 95, 100, 105, 110
    # IVs: 0.20, 0.22, 0.25, 0.27, 0.30
    # Quantity: 10, 15, 20, 15, 10
    # IsCall: 0, 0, 1, 1, 1
    # Columns: [strike, iv, quantity, is_call]
    chain_data = np.array(
        [
            [90.0, 0.20, 10.0, 0.0],
            [95.0, 0.22, 15.0, 0.0],
            [100.0, 0.25, 20.0, 1.0],
            [105.0, 0.27, 15.0, 1.0],
            [110.0, 0.30, 10.0, 1.0],
        ],
        dtype=np.float64,
    )

    res = engine.analyze_zomma(
        chain_data=chain_data,
        spot=spot,
        tte=tte,
        rate=rate,
        vol_crush_pct=0.20,
        spot_range_pct=0.10,
        n_spot=10,
        n_iv=8,
    )

    assert res.is_success
    report = res.unwrap()
    assert isinstance(report, ZommaReport)
    assert len(report.spot_axis) == 10
    assert len(report.iv_axis) == 8
    assert len(report.heatmap_z) == 8
    assert len(report.heatmap_z[0]) == 10
    assert report.vol_crush_pct == 0.20
    assert report.current_iv > 0.0
    assert abs(report.post_crush_iv - report.current_iv * 0.8) < 1e-7
    assert len(report.top_strikes) <= 5
    assert report.gamma_vol_crush.atm_zomma_neg is not None
    assert report.gamma_vol_crush.otm_zomma_pos is not None


def test_zomma_leg_truncation():
    engine = ZommaEngine(contract_size=100)
    spot = 100.0
    tte = 0.5
    rate = 0.05

    # 6 options, but max_legs = 3
    # The 3 with highest quantities should be selected
    chain_data = np.array(
        [
            [90.0, 0.20, 1.0, 0.0],  # Q=1
            [95.0, 0.22, 10.0, 0.0],  # Q=10
            [100.0, 0.25, 20.0, 1.0],  # Q=20
            [105.0, 0.27, 30.0, 1.0],  # Q=30
            [110.0, 0.30, 2.0, 1.0],  # Q=2
            [115.0, 0.35, 3.0, 1.0],  # Q=3
        ],
        dtype=np.float64,
    )

    res = engine.analyze_zomma(chain_data=chain_data, spot=spot, tte=tte, rate=rate, max_legs=3)
    assert res.is_success
    report = res.unwrap()
    # Should only process top 3 by quantity: strikes 100, 105, 95 (quantities 20, 30, 10)
    top_strikes = [ts.strike for ts in report.top_strikes]
    assert 100.0 in top_strikes
    assert 105.0 in top_strikes
    assert 95.0 in top_strikes
    assert 90.0 not in top_strikes
    assert 110.0 not in top_strikes
    assert 115.0 not in top_strikes


def test_zomma_validations():
    engine = ZommaEngine(contract_size=100)
    spot = 100.0
    tte = 0.5
    rate = 0.05
    chain_data = np.array([[100.0, 0.25, 10.0, 1.0]], dtype=np.float64)

    # Invalid chain_data is None
    res = engine.analyze_zomma(None, spot, tte, rate)  # type: ignore
    assert res.is_failure
    assert "chain_data must not be None" in res.reason

    # Invalid shape
    res = engine.analyze_zomma(np.array([100.0, 0.25, 10.0]), spot, tte, rate)
    assert res.is_failure

    # Invalid spot
    res = engine.analyze_zomma(chain_data, -100.0, tte, rate)
    assert res.is_failure
    assert "spot price" in res.reason

    # Invalid T
    res = engine.analyze_zomma(chain_data, spot, -0.5, rate)
    assert res.is_failure
    assert "time to expiry" in res.reason

    # Zero quantity
    res = engine.analyze_zomma(np.array([[100.0, 0.25, 0.0, 1.0]]), spot, tte, rate)
    assert res.is_failure
    assert "zero_quantity" in res.reason
