from backend.quant_engine.engines.technical.squeeze_ignition import (
    OptionChainData,
    SignalType,
    SqueezeIgnitionEngine,
    SqueezeState,
    UnderlyingData,
)


def test_squeeze_ignition_validation_errors():
    engine = SqueezeIgnitionEngine(ticker="XYZ")

    # Invalid spot price
    u = UnderlyingData(
        ticker="XYZ",
        spot_price=-10.0,
        prev_spot_price=10.0,
        volume=1000.0,
        volume_sma_20=1000.0,
        short_interest_ratio=10.0,
        days_to_cover=2.0,
    )
    o = OptionChainData(
        call_volume=100.0,
        call_volume_sma_20=100.0,
        call_open_interest=500.0,
        put_call_ratio_volume=0.8,
        dealer_net_gamma=-50000.0,
        call_wall_level=12.0,
        gamma_zero_level=11.0,
    )
    res = engine.analyze(u, o, SqueezeState.MONITORING, 0, None)
    assert res.is_failure
    assert "Spot price must be positive" in res.reason

    # NaN value
    u_nan = u.model_copy(update={"spot_price": float("nan")})
    res_nan = engine.analyze(u_nan, o, SqueezeState.MONITORING, 0, None)
    assert res_nan.is_failure
    assert "UnderlyingData contains NaN" in res_nan.reason


def test_squeeze_ignition_fsm_transitions():
    engine = SqueezeIgnitionEngine(ticker="GME")

    # 1. Monitoring -> Vulnerable (Period 3 of simulation: SVS >= 65)
    u_p1 = UnderlyingData(
        ticker="GME",
        spot_price=35.50,
        prev_spot_price=22.40,
        volume=25_000_000.0,
        volume_sma_20=6_000_000.0,
        short_interest_ratio=140.0,
        days_to_cover=9.5,
    )
    o_p1 = OptionChainData(
        call_volume=210_000.0,
        call_volume_sma_20=30_000.0,
        call_open_interest=520_000.0,
        put_call_ratio_volume=1.20,
        dealer_net_gamma=-900_000.0,
        call_wall_level=40.0,
        gamma_zero_level=38.0,
    )
    res_p1 = engine.analyze(u_p1, o_p1, SqueezeState.MONITORING, 0, None)
    assert res_p1.is_success
    sig_p1 = res_p1.unwrap()
    assert sig_p1.state == SqueezeState.VULNERABLE
    assert sig_p1.signal_type == SignalType.ALERT_VULNERABLE
    assert sig_p1.squeeze_vulnerability_score >= 65.0

    # 2. Vulnerable -> Ignition (Period 4 of simulation: SVS >= 85 + Wall cross + Vol Accel)
    # spot price crosses the Call Wall (40.0) -> spot=43.03, prev_spot=35.50
    u_p2 = UnderlyingData(
        ticker="GME",
        spot_price=43.03,
        prev_spot_price=35.50,
        volume=89_000_000.0,
        volume_sma_20=25_000_000.0,
        short_interest_ratio=140.0,
        days_to_cover=12.0,
    )
    o_p2 = OptionChainData(
        call_volume=280_000.0,
        call_volume_sma_20=30_000.0,
        call_open_interest=680_000.0,
        put_call_ratio_volume=2.85,
        dealer_net_gamma=-1_100_000.0,
        call_wall_level=40.0,
        gamma_zero_level=38.0,
    )
    res_p2 = engine.analyze(u_p2, o_p2, SqueezeState.VULNERABLE, 0, None)
    assert res_p2.is_success
    sig_p2 = res_p2.unwrap()
    assert sig_p2.state == SqueezeState.IGNITION
    assert sig_p2.signal_type == SignalType.LONG_MOMENTUM_IGNITION
    assert sig_p2.new_ignition_price == 43.03
    assert len(sig_p2.take_profit_levels) == 4

    # 3. Ignition -> Cooling
    # In next step, from IGNITION state, FSM automatically transitions to COOLING
    u_p3 = UnderlyingData(
        ticker="GME",
        spot_price=45.0,
        prev_spot_price=35.0,
        volume=50_000_000.0,
        volume_sma_20=25_000_000.0,
        short_interest_ratio=130.0,
        days_to_cover=10.0,
    )
    o_p3 = OptionChainData(
        call_volume=150_000.0,
        call_volume_sma_20=30_000.0,
        call_open_interest=600_000.0,
        put_call_ratio_volume=2.0,
        dealer_net_gamma=-900_000.0,
        call_wall_level=30.0,
        gamma_zero_level=25.0,
    )
    res_p3 = engine.analyze(u_p3, o_p3, SqueezeState.IGNITION, 0, 43.03)
    assert res_p3.is_success
    sig_p3 = res_p3.unwrap()
    assert sig_p3.state == SqueezeState.COOLING
    assert sig_p3.new_cooling_counter == 0
    assert sig_p3.signal_type == SignalType.TAKE_PROFIT_SCALING

    # 4. Cooling steps 0 -> 1 -> 2 -> 3 -> 4 (reaches 4 -> reset to MONITORING)
    # cooling_counter = 0 -> next new_cooling_counter = 1
    res_c1 = engine.analyze(u_p3, o_p3, SqueezeState.COOLING, 0, 43.03)
    sig_c1 = res_c1.unwrap()
    assert sig_c1.state == SqueezeState.COOLING
    assert sig_c1.new_cooling_counter == 1
    assert sig_c1.signal_type == SignalType.TAKE_PROFIT_SCALING

    # cooling_counter = 3 -> next new_cooling_counter = 4 (transitions to MONITORING)
    res_c4 = engine.analyze(u_p3, o_p3, SqueezeState.COOLING, 3, 43.03)
    sig_c4 = res_c4.unwrap()
    assert sig_c4.state == SqueezeState.MONITORING
    assert sig_c4.new_cooling_counter == 0
    assert sig_c4.new_ignition_price is None
    assert sig_c4.signal_type == SignalType.NONE
