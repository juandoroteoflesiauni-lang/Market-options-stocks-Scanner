import numpy as np

from backend.quant_engine.engines.options.delta_weighted_flow import (
    DeltaWeightedFlow_Engine,
    EngineConfig,
    MarketSignal,
)


def test_delta_weighted_flow_validation_errors():
    config = EngineConfig(
        contract_multiplier=100,
        rolling_window=5,
        panic_threshold=3.0,
        reset_threshold=1.0,
    )
    engine = DeltaWeightedFlow_Engine(config=config)

    # Empty chain data
    res = engine.analyze_flow(np.empty((0, 4)), np.array([1.0]), False)
    assert res.is_failure
    assert "chain_data is empty" in res.reason

    # Invalid columns shape
    res = engine.analyze_flow(np.ones((5, 3)), np.array([1.0]), False)
    assert res.is_failure
    assert "must be a 2D array of shape" in res.reason

    # NaN in inputs
    res = engine.analyze_flow(np.array([[1.0, 10, np.nan, 0.5]]), np.array([1.0]), False)
    assert res.is_failure
    assert "contains NaN values" in res.reason


def test_delta_weighted_flow_normal_and_hold():
    config = EngineConfig(
        contract_multiplier=100,
        rolling_window=5,
        panic_threshold=3.0,
        reset_threshold=1.0,
    )
    engine = DeltaWeightedFlow_Engine(config=config)

    # chain_data format: is_call, volume, mark_price, delta
    # Call-dominated flow: is_call=1.0, volume=10, mark_price=1.5, delta=0.5.
    # Put-dominated flow: is_call=0.0, volume=5, mark_price=1.2, delta=-0.4.
    # Call DW Flow = 10 * 1.5 * 100 * 0.5 = 750
    # Put DW Flow = 5 * 1.2 * 100 * 0.4 = 240
    # Call > Put -> MarketSignal.HOLD_STATE
    chain_data = np.array(
        [
            [1.0, 10.0, 1.5, 0.5],
            [0.0, 5.0, 1.2, -0.4],
        ]
    )
    ratio_history = np.array([1.0, 1.1, 1.2])
    res = engine.analyze_flow(chain_data, ratio_history, False)
    assert res.is_success
    snapshot = res.unwrap()
    assert snapshot.total_call_flow == 750.0
    assert snapshot.total_put_flow == 240.0
    assert snapshot.pc_flow_ratio == 240.0 / 750.0
    assert snapshot.signal == MarketSignal.HOLD_STATE
    assert not snapshot.is_in_exhaustion


def test_delta_weighted_flow_panic_and_trigger():
    config = EngineConfig(
        contract_multiplier=100,
        rolling_window=20,
        panic_threshold=3.0,
        reset_threshold=1.0,
    )
    engine = DeltaWeightedFlow_Engine(config=config)

    # Put-dominated flow to trigger panic
    # Call flow: is_call=1.0, volume=1, mark_price=1.0, delta=0.1
    # Call DW Flow = 1 * 1 * 100 * 0.1 = 10
    # Put flow: is_call=0.0, volume=100, mark_price=10.0, delta=-1.0
    # Put DW Flow = 100 * 10 * 100 * 1.0 = 100000
    # PC Flow Ratio = 100000 / 10 = 10000.0 (high PC ratio)
    chain_data_panic = np.array(
        [
            [1.0, 1.0, 1.0, 0.1],
            [0.0, 100.0, 10.0, -1.0],
        ]
    )

    # History of 19 calm ratios close to 1.0
    ratio_history = np.ones(19, dtype=np.float64)

    # 1. Test Panic Entrance
    res_panic = engine.analyze_flow(chain_data_panic, ratio_history, False)
    assert res_panic.is_success
    snap_panic = res_panic.unwrap()
    assert snap_panic.signal == MarketSignal.EXHAUSTION_WARNING
    assert snap_panic.is_in_exhaustion is True

    # 2. Test Setup Trigger (Reset)
    # The history now contains the panic ratio as the last element.
    ratio_history_recovery = np.append(ratio_history, 10000.0)
    # A calm snapshot (PC ratio close to 1.0)
    chain_data_recovery = np.array(
        [
            [1.0, 10.0, 1.0, 0.5],
            [0.0, 10.0, 1.0, -0.5],
        ]
    )
    # Call DW Flow = 10 * 1.0 * 100 * 0.5 = 500
    # Put DW Flow = 10 * 1.0 * 100 * 0.5 = 500
    # PC Ratio = 1.0

    res_trigger = engine.analyze_flow(chain_data_recovery, ratio_history_recovery, True)
    assert res_trigger.is_success
    snap_trigger = res_trigger.unwrap()
    assert snap_trigger.signal == MarketSignal.LONG_SETUP_TRIGGER
    assert snap_trigger.is_in_exhaustion is False
