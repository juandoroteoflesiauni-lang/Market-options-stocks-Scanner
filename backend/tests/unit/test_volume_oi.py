import numpy as np
import pytest

from backend.models.result import Result
from src.quant_engine.engines.technical.volume_oi import (
    AnalyzerConfig,
    Signal,
    VolumeOIDynamicsReport,
    get_volume_oi_analysis,
)


def test_volume_oi_success():
    # Columns mapping:
    # 0=strike, 1=is_call, 2=volume, 3=open_interest, 4=prev_open_interest,
    # 5=delta, 6=bid, 7=ask, 8=last_price
    chain_data = np.array(
        [
            [190.0, 1.0, 15000.0, 85000.0, 72000.0, 0.45, 2.50, 2.60, 2.55],
            [195.0, 0.0, 12000.0, 50000.0, 50050.0, -0.30, 1.80, 1.90, 1.88],
            [250.0, 1.0, 4500.0, 18000.0, 22000.0, 0.15, 0.80, 0.85, 0.82],
            [420.0, 0.0, 300.0, 2100.0, 2050.0, -0.05, 0.10, 0.12, 0.11],
            [530.0, 1.0, 8200.0, 35000.0, 35000.0, 0.02, 0.05, 0.06, 0.05],
            [880.0, 1.0, 30.0, 1200.0, 1200.0, 0.01, 0.02, 0.03, 0.02],
        ]
    )
    spot = 192.5
    dotm_history = [0.1, 0.2, 0.3, 0.4, 0.5]

    res = get_volume_oi_analysis(
        chain_data,
        spot,
        analyzer_config=AnalyzerConfig(volume_noise_floor=50),
        dotm_ratio_history=dotm_history,
    )

    assert isinstance(res, Result)
    assert res.is_success

    report = res.unwrap()
    assert isinstance(report, VolumeOIDynamicsReport)

    assert len(report.classified_contracts) == 6
    assert "vol_high" in report.thresholds
    assert "vol_low" in report.thresholds

    # Verify classification of row 5 (below noise floor: volume = 30)
    assert report.classified_contracts[5].signal_type == Signal.NOISE

    # Verify summary counts are present
    assert report.summary[Signal.NOISE.value]["CALL"] == 1

    # Verify flow signals
    assert report.flow_signal is not None
    assert -1.0 <= report.flow_signal <= 1.0

    # Verify DOTM metrics
    assert report.dotm_put_oi is not None
    assert report.dotm_call_oi is not None
    assert report.dotm_ratio is not None
    assert report.dotm_signal is not None
    # Hist history: [0.1, 0.2, 0.3, 0.4, 0.5] -> 80th percentile is 0.46
    # Let's check if the ratio triggers alert
    # DOTM: delta < 0.20 -> strikes 250, 420, 530, 880
    # puts: 420 (oi=2100)
    # calls: 250 (oi=18000), 530 (oi=35000), 880 (oi=1200) -> sum = 54200
    # ratio = 2100 / 54201 = ~0.0387
    # 0.0387 <= 0.46 -> alert is False
    assert report.dotm_alert is False


def test_volume_oi_validation_errors():
    spot = 192.5
    # Empty data
    res = get_volume_oi_analysis(np.empty((0, 9)), spot)
    assert res.is_failure
    assert "empty" in res.reason

    # Incorrect shape
    res = get_volume_oi_analysis(np.zeros((5, 8)), spot)
    assert res.is_failure
    assert "shape" in res.reason

    # Not numpy array
    res = get_volume_oi_analysis([[0.0] * 9], spot)  # type: ignore[arg-type]
    assert res.is_failure
    assert "ndarray" in res.reason

    # Invalid is_call (contains 2.0)
    invalid_call = np.zeros((5, 9))
    invalid_call[:, 1] = 2.0
    res = get_volume_oi_analysis(invalid_call, spot)
    assert res.is_failure
    assert "is_call" in res.reason

    # Negative volume
    neg_vol = np.zeros((5, 9))
    neg_vol[:, 2] = -10.0
    res = get_volume_oi_analysis(neg_vol, spot)
    assert res.is_failure
    assert "non-negative" in res.reason

    # NaN in critical columns
    nan_strike = np.zeros((5, 9))
    nan_strike[0, 0] = np.nan
    res = get_volume_oi_analysis(nan_strike, spot)
    assert res.is_failure
    assert "NaN values" in res.reason


def test_volume_oi_nan_imputation():
    # prev_open_interest is NaN on index 1
    chain_data = np.array(
        [
            [190.0, 1.0, 100.0, 500.0, 400.0, 0.50, 1.0, 1.1, 1.05],
            [195.0, 0.0, 200.0, 600.0, np.nan, -0.40, 2.0, 2.1, 2.05],
        ]
    )
    res = get_volume_oi_analysis(chain_data, 190.0)
    assert res.is_success
    report = res.unwrap()

    # Index 1 should be imputed to prev_open_interest = current open_interest (600.0)
    # which leads to net_oi_change = 0.0
    assert report.classified_contracts[1].prev_open_interest == 600.0
    assert report.classified_contracts[1].net_oi_change == 0.0


def test_volume_oi_noise_floor():
    # Volume = 10, noise floor = 50
    chain_data = np.array(
        [
            [190.0, 1.0, 10.0, 500.0, 400.0, 0.50, 1.0, 1.1, 1.05],
        ]
    )
    res = get_volume_oi_analysis(
        chain_data, 190.0, analyzer_config=AnalyzerConfig(volume_noise_floor=50)
    )
    assert res.is_success
    assert res.unwrap().classified_contracts[0].signal_type == Signal.NOISE


def test_volume_oi_no_active_rows():
    # All volumes below noise floor
    chain_data = np.array(
        [
            [190.0, 1.0, 10.0, 500.0, 400.0, 0.50, 1.0, 1.1, 1.05],
            [195.0, 0.0, 20.0, 600.0, 550.0, -0.40, 2.0, 2.1, 2.05],
        ]
    )
    res = get_volume_oi_analysis(
        chain_data, 190.0, analyzer_config=AnalyzerConfig(volume_noise_floor=50)
    )
    assert res.is_success
    report = res.unwrap()
    # Thresholds should have been calculated using all rows
    assert report.thresholds["vol_high"] > 0.0
    assert report.thresholds["vol_low"] > 0.0


def test_volume_oi_premium_flow():
    # test premium flow
    chain_data = np.array(
        [
            # bid=1.0, ask=2.0, last=1.8 -> buyer-initiated (1.8 > 1.5) -> sign = 1.0
            # call premium = 1.8 * sign = 1.8
            [190.0, 1.0, 100.0, 500.0, 400.0, 0.50, 1.0, 2.0, 1.8],
            # bid=2.0, ask=3.0, last=2.1 -> seller-initiated (2.1 < 2.5) -> sign = -1.0
            # put premium = 2.1 * sign = -2.1
            [195.0, 0.0, 200.0, 600.0, 550.0, -0.40, 2.0, 3.0, 2.1],
        ]
    )
    res = get_volume_oi_analysis(chain_data, 190.0)
    assert res.is_success
    report = res.unwrap()
    # call_net = 1.8, put_net = -2.1
    assert report.call_net_premium == pytest.approx(1.8)
    assert report.put_net_premium == pytest.approx(-2.1)
    # flow_signal = (1.8 - (-2.1)) / (|1.8| + |-2.1| + 1) = 3.9 / 4.9 = ~0.7959
    assert report.flow_signal == pytest.approx(3.9 / 4.9)


def test_volume_oi_dotm_flow_with_history():
    chain_data = np.array(
        [
            # Calls DOTM: delta = 0.15 (dotm), oi = 1000
            [190.0, 1.0, 100.0, 1000.0, 900.0, 0.15, 1.0, 1.1, 1.05],
            # Puts DOTM: delta = -0.10 (dotm), oi = 3000
            [195.0, 0.0, 200.0, 3000.0, 2800.0, -0.10, 2.0, 2.1, 2.05],
        ]
    )
    # dotm_put_oi = 3000, dotm_call_oi = 1000
    # ratio = 3000 / (1000 + 1) = ~2.997
    # dotm_signal = clip(ratio / 10.0, 0, 1) = ~0.2997
    # history = [1.0, 1.5, 2.0, 2.5, 2.8] -> 80th percentile is 2.74
    # ratio (2.997) > threshold (2.74) -> dotm_alert = True
    res = get_volume_oi_analysis(chain_data, 190.0, dotm_ratio_history=[1.0, 1.5, 2.0, 2.5, 2.8])
    assert res.is_success
    report = res.unwrap()
    assert report.dotm_ratio == pytest.approx(3000.0 / 1001.0)
    assert report.dotm_alert is True


def test_volume_oi_uoa_strikes():
    chain_data = np.array(
        [
            # vol/oi = 400/100 = 4.0 (UOA)
            [190.0, 1.0, 400.0, 100.0, 90.0, 0.50, 1.0, 1.1, 1.05],
            # vol/oi = 10/100 = 0.1 (not UOA)
            [195.0, 0.0, 10.0, 100.0, 90.0, -0.40, 2.0, 2.1, 2.05],
            # vol/oi = 1000/200 = 5.0 (UOA)
            [200.0, 1.0, 1000.0, 200.0, 180.0, 0.30, 3.0, 3.1, 3.05],
        ]
    )
    res = get_volume_oi_analysis(chain_data, 190.0)
    assert res.is_success
    report = res.unwrap()
    assert len(report.uoa_strikes) == 2
    # Verify sorting: strike 200 (ratio 5.0) should be first, strike 190 (ratio 4.0) second
    assert report.uoa_strikes[0].strike == 200.0
    assert report.uoa_strikes[0].vol_oi_ratio == 5.0
    assert report.uoa_strikes[1].strike == 190.0
    assert report.uoa_strikes[1].vol_oi_ratio == 4.0
