import numpy as np

from backend.quant_engine.engines.options.gamma_flip import GammaFlipEngine


def test_gamma_flip_analysis_success():
    engine = GammaFlipEngine(contract_size=100)

    # Spot price at 5000
    spot = 5000.0

    # 10 strikes around spot
    strikes = np.linspace(4500, 5500, 10)

    # Let's mock calls and puts open interest
    rows = []
    for k in strikes:
        # Call row
        call_oi = 5000 if k > spot else 1000
        rows.append([k, 1.0, call_oi])

        # Put row
        put_oi = 8000 if k < spot else 2000
        rows.append([k, 0.0, put_oi])

    chain_data = np.array(rows, dtype=np.float64)

    res = engine.analyze_gamma_flip(
        chain_data=chain_data,
        spot_price=spot,
        tte=30.0 / 365.0,
        rate=0.05,
        sigma=0.20,
    )

    assert res.is_success
    report = res.unwrap()
    assert report.spot_price == spot
    assert report.current_gamma is not None
    assert report.flip_point is not None
    assert len(report.price_range) == 500
    assert len(report.gamma_profile) == 500

    regime = report.volatility_regime
    assert regime.regime in ("AT_FLIP", "GAMMA_POSITIVE", "GAMMA_NEGATIVE")
    assert regime.distance_pct is not None

    sensitivity = report.sensitivity
    assert sensitivity.shock_pct_applied == 10.0
    assert sensitivity.flip_shocked is not None


def test_gamma_flip_analysis_failures():
    engine = GammaFlipEngine()

    # 1. Invalid spot price
    res1 = engine.analyze_gamma_flip(
        chain_data=np.zeros((5, 3)),
        spot_price=0.0,
        tte=0.1,
        rate=0.05,
        sigma=0.2,
    )
    assert res1.is_failure
    assert "spot price" in res1.reason

    # 2. Invalid volatility
    res2 = engine.analyze_gamma_flip(
        chain_data=np.zeros((5, 3)),
        spot_price=5000.0,
        tte=0.1,
        rate=0.05,
        sigma=-0.2,
    )
    assert res2.is_failure
    assert "volatility" in res2.reason

    # 3. None chain_data
    res3 = engine.analyze_gamma_flip(
        chain_data=None,  # type: ignore
        spot_price=5000.0,
        tte=0.1,
        rate=0.05,
        sigma=0.2,
    )
    assert res3.is_failure
    assert "must not be None" in res3.reason
