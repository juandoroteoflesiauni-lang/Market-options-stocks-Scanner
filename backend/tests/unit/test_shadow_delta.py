import numpy as np

from src.quant_engine.engines.options.shadow_delta import (
    ShadowDeltaEngine,
    shadow_delta_position_multiplier,
)


def test_shadow_delta_analysis_success():
    engine = ShadowDeltaEngine(contract_size=100)

    # Spot price at 5000
    spot = 5000.0

    # 10 strikes around spot
    strikes = np.linspace(4600, 5400, 10)

    # Real skew scenario (puts OTM have higher IV, calls have lower IV)
    rows = []
    for k in strikes:
        iv = 0.25 - (k - spot) * 0.0001
        quantity = 5.0 if k > spot else -5.0
        # Columns: [strike, is_call, iv, quantity]
        is_call = 1.0 if k >= spot else 0.0
        rows.append([k, is_call, iv, quantity])

    chain_data = np.array(rows, dtype=np.float64)

    res = engine.analyze_shadow_delta(
        chain_data=chain_data,
        spot_price=spot,
        tte=0.25,
        rate=0.05,
    )

    assert res.is_success
    report = res.unwrap()
    assert report.spot_price == spot
    assert len(report.nodes) == len(strikes)

    net_port = report.net_portfolio
    assert net_port.n_options == len(strikes)
    assert net_port.net_bs_delta != 0.0
    assert net_port.net_shadow_delta != 0.0

    for node in report.nodes:
        assert node.strike in strikes
        assert node.bs_delta != 0.0
        assert node.shadow_delta != 0.0
        assert node.vanna is not None
        assert node.post_shock_bs_delta is not None
        assert node.post_shock_shadow_delta is not None
        assert node.multiplier_result.multiplier >= 1.0


def test_shadow_delta_position_multiplier():
    # 1. Normal alignment, divergence below threshold
    res1 = shadow_delta_position_multiplier(
        shadow_delta=0.52,
        bs_delta=0.50,
        vanna=0.02,
        option_type="CALL",
        skew_slope=-0.01,
    )
    assert res1.multiplier == 1.0
    assert res1.edge_signal == 0.0

    # 2. CALL amplified (shadow > BS and divergence above threshold)
    res2 = shadow_delta_position_multiplier(
        shadow_delta=0.65,
        bs_delta=0.50,
        vanna=0.08,
        option_type="CALL",
        skew_slope=-0.02,
    )
    assert res2.multiplier > 1.0
    assert res2.edge_signal > 0.0
    assert "amplified" in res2.reason

    # 3. PUT amplified (shadow < BS (more negative) and divergence above threshold)
    res3 = shadow_delta_position_multiplier(
        shadow_delta=-0.65,
        bs_delta=-0.50,
        vanna=0.08,
        option_type="PUT",
        skew_slope=-0.02,
    )
    assert res3.multiplier > 1.0
    assert res3.edge_signal > 0.0
    assert "amplified" in res3.reason


def test_shadow_delta_analysis_failures():
    engine = ShadowDeltaEngine()

    # 1. Invalid spot price
    res1 = engine.analyze_shadow_delta(
        chain_data=np.zeros((5, 4)),
        spot_price=0.0,
        tte=0.25,
        rate=0.05,
    )
    assert res1.is_failure
    assert "spot price" in res1.reason

    # 2. Invalid tte
    res2 = engine.analyze_shadow_delta(
        chain_data=np.zeros((5, 4)),
        spot_price=5000.0,
        tte=-0.25,
        rate=0.05,
    )
    assert res2.is_failure
    assert "time to expiry" in res2.reason

    # 3. None chain_data
    res3 = engine.analyze_shadow_delta(
        chain_data=None,  # type: ignore
        spot_price=5000.0,
        tte=0.25,
        rate=0.05,
    )
    assert res3.is_failure
    assert "must not be None" in res3.reason
