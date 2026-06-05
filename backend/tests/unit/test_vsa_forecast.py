import numpy as np
import pytest

from backend.engine.metrics.vsa_forecast import (
    VSAForecastEngine,
    VSAForecastResult,
)
from backend.models.result import Result


def test_vsa_forecast_engine():
    # Initialize engine
    engine = VSAForecastEngine(vol_avg_period=20, climax_threshold=1.5)

    # 1. Test calculate_vfi
    # period = 14, need at least 2 * 14 - 2 + 5 = 31 elements
    high = np.random.rand(35) + 2.0
    low = high - np.random.rand(35) * 0.5
    close = (high + low) / 2.0
    open_p = (high + low) / 2.0
    volume = np.random.rand(35) + 1.0
    ohlcv_ok = np.column_stack([open_p, high, low, close, volume])

    res_vfi = engine.calculate_vfi(ohlcv_ok, period=14)
    assert isinstance(res_vfi, Result)
    assert res_vfi.is_success
    vfi_dict = res_vfi.unwrap()
    assert "vfi" in vfi_dict
    assert "slope" in vfi_dict
    assert isinstance(vfi_dict["vfi"], float)
    assert isinstance(vfi_dict["slope"], float)

    # Test calculate_vfi with insufficient data
    ohlcv_short = ohlcv_ok[:20, :]
    res_vfi_short = engine.calculate_vfi(ohlcv_short, period=14)
    assert isinstance(res_vfi_short, Result)
    assert res_vfi_short.is_failure

    # Test calculate_vfi with negative volume
    ohlcv_neg_vol = ohlcv_ok.copy()
    ohlcv_neg_vol[10, 4] = -1.0
    res_vfi_neg = engine.calculate_vfi(ohlcv_neg_vol, period=14)
    assert isinstance(res_vfi_neg, Result)
    assert res_vfi_neg.is_failure
    assert "Volume contains zero or negative" in res_vfi_neg.reason

    # 2. Test predict_current_bar
    res_pred = engine.predict_current_bar(
        ohlcv=ohlcv_ok,
        seconds_elapsed=150,
        total_seconds=300,
        ticker="AAPL",
    )
    assert isinstance(res_pred, Result)
    assert res_pred.is_success
    pred_result = res_pred.unwrap()
    assert isinstance(pred_result, VSAForecastResult)
    assert pred_result.ticker == "AAPL"
    assert pred_result.estimated_volume >= 0.0
    assert pred_result.estimated_spread >= 0.0
    assert 0.0 <= pred_result.confidence <= 1.0
    assert isinstance(pred_result.is_climax_likely, bool)

    # Test predict_current_bar with insufficient data
    res_pred_short = engine.predict_current_bar(
        ohlcv=np.random.rand(1, 5) + 1.0,
        seconds_elapsed=150,
        total_seconds=300,
        ticker="AAPL",
    )
    assert isinstance(res_pred_short, Result)
    assert res_pred_short.is_failure

    # 3. Test detect_footprint_clusters
    res_footprint = engine.detect_footprint_clusters(ohlcv_ok)
    assert isinstance(res_footprint, Result)
    assert res_footprint.is_success
    support, resistance = res_footprint.unwrap()
    assert support <= resistance
    assert support > 0.0

    # Test detect_footprint_clusters with empty array
    res_footprint_empty = engine.detect_footprint_clusters(np.empty((0, 5)))
    assert isinstance(res_footprint_empty, Result)
    assert res_footprint_empty.is_failure

    # 4. Test generate_vp_forecast
    res_vp = engine.generate_vp_forecast(ohlcv_ok)
    assert isinstance(res_vp, Result)
    assert res_vp.is_success
    assert isinstance(res_vp.unwrap(), bool)

    # Test generate_vp_forecast with insufficient data (len < 20)
    res_vp_short = engine.generate_vp_forecast(np.random.rand(10, 5) + 1.0)
    assert isinstance(res_vp_short, Result)
    assert res_vp_short.is_failure
