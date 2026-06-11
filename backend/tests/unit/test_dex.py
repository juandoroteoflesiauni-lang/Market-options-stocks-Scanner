import numpy as np
import pytest

from backend.models.result import Result
from backend.quant_engine.engines.options.dex import DEXReport, get_dex_analysis


def test_dex_success():
    # Columns mapping:
    # 0=strike, 1=is_call, 2=delta, 3=open_interest
    chain_data = np.array(
        [
            [190.0, 1.0, 0.50, 100.0],
            [195.0, 0.0, -0.40, 200.0],
            [200.0, 1.0, 0.30, 300.0],
        ]
    )
    spot_price = 192.5
    adtv = 1000000.0

    res = get_dex_analysis("AAPL", spot_price, adtv, chain_data)

    assert isinstance(res, Result)
    assert res.is_success

    report = res.unwrap()
    assert isinstance(report, DEXReport)

    assert report.ticker == "AAPL"
    assert report.spot_price == spot_price
    assert report.adtv == adtv

    # Calculate expected DEX values:
    # Row 0: call, delta = 0.50, oi = 100, spot = 192.5, multiplier = 100
    # call_dex = 0.5 * 100 * 100 * 192.5 = 962500.0
    # Row 1: put, delta = -0.40, oi = 200, spot = 192.5
    # put_dex = -0.4 * 200 * 100 * 192.5 = -1540000.0
    # Row 2: call, delta = 0.30, oi = 300, spot = 192.5
    # call_dex = 0.3 * 300 * 100 * 192.5 = 1732500.0
    # Total calls dex = 962500 + 1732500 = 2695000.0
    # Total puts dex = -1540000.0
    # Total nominal dex = 1155000.0
    assert report.dex_calls == pytest.approx(2695000.0)
    assert report.dex_puts == pytest.approx(-1540000.0)
    assert report.dex_total_nominal == pytest.approx(1155000.0)

    # DEX as % of ADTV = (1155000.0 / 1000000.0) * 100 = 115.5
    assert report.dex_as_pct_adtv == pytest.approx(115.5)

    # 3 strikes unique, so 3 profiles
    assert len(report.dex_profile) == 3


def test_dex_validation_errors():
    spot_price = 192.5

    # Not numpy array
    res = get_dex_analysis("AAPL", spot_price, None, [[100.0, 1.0, 0.5, 10.0]])  # type: ignore
    assert res.is_failure
    assert "ndarray" in res.reason

    # Incorrect shape
    res = get_dex_analysis("AAPL", spot_price, None, np.zeros((5, 3)))
    assert res.is_failure
    assert "shape" in res.reason

    # Empty array
    res = get_dex_analysis("AAPL", spot_price, None, np.empty((0, 4)))
    assert res.is_failure
    assert "empty" in res.reason

    # NaN values
    nan_data = np.zeros((5, 4))
    nan_data[0, 0] = np.nan
    res = get_dex_analysis("AAPL", spot_price, None, nan_data)
    assert res.is_failure
    assert "NaN values" in res.reason

    # Negative spot
    res = get_dex_analysis("AAPL", -1.0, None, np.zeros((5, 4)))
    assert res.is_failure
    assert "spot_price" in res.reason

    # Invalid is_call (contains 2.0)
    invalid_call = np.zeros((5, 4))
    invalid_call[:, 1] = 2.0
    res = get_dex_analysis("AAPL", spot_price, None, invalid_call)
    assert res.is_failure
    assert "is_call" in res.reason

    # Negative open interest
    neg_oi = np.zeros((5, 4))
    neg_oi[:, 3] = -5.0
    res = get_dex_analysis("AAPL", spot_price, None, neg_oi)
    assert res.is_failure
    assert "open_interest" in res.reason


def test_dex_gamma_flip_level():
    # Set up data that flips cumulative net DEX sign
    # cumulative values will flip sign
    chain_data = np.array(
        [
            # strike 100, call, delta 0.50, oi 1000 -> dex = 0.5 * 1000 * 100 * 10 = 500000
            # strike 110, put, delta -0.50, oi 3000 -> dex = -0.5 * 3000 * 100 * 10 = -1500000
            # strike 120, call, delta 0.50, oi 4000 -> dex = 0.5 * 4000 * 100 * 10 = 2000000
            [100.0, 1.0, 0.50, 1000.0],
            [110.0, 0.0, -0.50, 3000.0],
            [120.0, 1.0, 0.50, 4000.0],
        ]
    )
    # dex cumulative:
    # 100.0: 500k000
    # 110.0: 500k - 1.5M = -1.0M  (cross happens here, between index 0 and 1)
    # 120.0: -1.0M + 2.0M = +1.0M (another cross happens here, between index 1 and 2)
    # Flip level should be unique_strikes[0 + 1] = 110.0
    res = get_dex_analysis("AAPL", 10.0, None, chain_data)
    assert res.is_success
    assert res.unwrap().gamma_flip_level == 110.0


def test_dex_sign_correction():
    # If delta has incorrect sign, it must be corrected vectorially
    chain_data = np.array(
        [
            # call with negative delta
            [190.0, 1.0, -0.50, 100.0],
            # put with positive delta
            [195.0, 0.0, 0.40, 200.0],
        ]
    )
    res = get_dex_analysis("AAPL", 100.0, None, chain_data)
    assert res.is_success
    report = res.unwrap()
    # verify that calls have positive dex, puts negative dex
    assert report.dex_calls > 0.0
    assert report.dex_puts < 0.0


def test_dex_top_strikes_sorting():
    # 6 strikes, different exposure levels
    chain_data = np.array(
        [
            [100.0, 1.0, 0.10, 100.0],  # net dex = 1k
            [101.0, 1.0, 0.20, 100.0],  # net dex = 2k
            [102.0, 1.0, 0.80, 100.0],  # net dex = 8k (highest)
            [103.0, 0.0, -0.50, 100.0],  # net dex = -5k (second highest abs)
            [104.0, 0.0, -0.60, 100.0],  # net dex = -6k (third highest abs)
            [105.0, 1.0, 0.40, 100.0],  # net dex = 4k
        ]
    )
    res = get_dex_analysis("AAPL", 10.0, None, chain_data)
    assert res.is_success
    report = res.unwrap()
    # top strikes must be limited to 5
    assert len(report.top_strikes) == 5

    # Check order:
    # 1. strike 102 (net_dex = 800000)
    # 2. strike 104 (net_dex = -600000)
    # 3. strike 103 (net_dex = -500000)
    # 4. strike 105 (net_dex = 400000)
    # 5. strike 101 (net_dex = 200000)
    # Note: the exact nominal value includes spot (10) and multiplier (100)
    # -> net_dex = delta * oi * 1000
    assert report.top_strikes[0].strike == 102.0
    assert report.top_strikes[1].strike == 104.0
    assert report.top_strikes[2].strike == 103.0
    assert report.top_strikes[3].strike == 105.0
    assert report.top_strikes[4].strike == 101.0
