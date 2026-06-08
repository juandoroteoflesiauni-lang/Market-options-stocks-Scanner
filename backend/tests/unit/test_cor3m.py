from datetime import datetime, UTC
import numpy as np
import pytest


from src.quant_engine.engines.technical.cor3m import (
    COR3M_Signal_Engine,
    EngineConfig,
    MarketState,
    SignalType,
)

from backend.models.result import Result


def test_cor3m_engine_insufficient_history():
    config = EngineConfig(
        percentile_window=10,
        panic_threshold=0.90,
        signal_threshold=0.80,
        memory_window=3,
    )
    engine = COR3M_Signal_Engine(config=config)
    
    # Needs at least 10 (min_periods) + 3 (memory_window) = 13 bars
    history = np.ones(12, dtype=np.float64)
    res = engine.analyze_current_state(history)
    assert isinstance(res, Result)
    assert res.is_failure
    assert "Insufficient history" in res.reason


def test_cor3m_engine_nan_values():
    config = EngineConfig(
        percentile_window=10,
        panic_threshold=0.90,
        signal_threshold=0.80,
        memory_window=3,
    )
    engine = COR3M_Signal_Engine(config=config)
    history = np.ones(15, dtype=np.float64)
    history[5] = np.nan
    res = engine.analyze_current_state(history)
    assert res.is_failure
    assert "contains NaN values" in res.reason


def test_cor3m_engine_normal_state():
    config = EngineConfig(
        percentile_window=10,
        panic_threshold=0.90,
        signal_threshold=0.80,
        memory_window=3,
    )
    engine = COR3M_Signal_Engine(config=config)
    # 15 constant values. The last value will have a percentile rank around 0.5 (depending on tie breaking)
    # Since it never breached the panic threshold, it should be NORMAL and NEUTRAL signal.
    history = np.array([10.0] * 15, dtype=np.float64)
    res = engine.analyze_current_state(history)
    assert res.is_success
    bar = res.unwrap()
    assert bar.market_state == MarketState.NORMAL
    assert bar.signal == SignalType.NEUTRAL
    assert bar.bars_since_panic == 0


def test_cor3m_engine_panic_and_buy_trigger():
    config = EngineConfig(
        percentile_window=10,
        panic_threshold=0.90,
        signal_threshold=0.80,
        memory_window=3,
    )
    engine = COR3M_Signal_Engine(config=config)
    
    # We want a 10-bar rolling window for each step.
    # We create an array of size 13.
    # index 0: 9.0
    # index 1..9: background values: [10, 11, 12, 13, 14, 15, 16, 17, 18]
    # index 10: 19.0
    # index 11: 30.0 (panic! >= 0.90 percentile rank)
    # index 12: 12.0 (drops below signal threshold (< 0.80) within 3 bars)
    history = np.array([9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 30.0, 12.0], dtype=np.float64)
    
    res = engine.analyze_current_state(history)
    assert res.is_success
    bar = res.unwrap()
    assert bar.market_state == MarketState.LONG_LIQUIDITY_RALLY
    assert bar.signal == SignalType.BUY
    assert bar.bars_since_panic == 0  # reset after trigger
