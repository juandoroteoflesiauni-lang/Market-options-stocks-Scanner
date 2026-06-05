import numpy as np

from backend.engine.metrics.volume_profile import VolumeProfileEngine


def test_volume_profile_validation_errors():
    engine = VolumeProfileEngine()

    # Empty array
    res = engine.analyze("BTC", np.empty((0, 2)), bins=10)
    assert res.is_failure
    assert "price_volume_data is empty" in res.reason

    # Invalid columns shape
    res = engine.analyze("BTC", np.ones((5, 3)), bins=10)
    assert res.is_failure
    assert "must be a 2D array of shape" in res.reason

    # NaN in inputs
    res = engine.analyze("BTC", np.array([[100.0, np.nan], [101.0, 10.0]]), bins=10)
    assert res.is_failure
    assert "contains NaN values" in res.reason

    # Negative volume
    res = engine.analyze("BTC", np.array([[100.0, -10.0], [101.0, 10.0]]), bins=10)
    assert res.is_failure
    assert "must be non-negative" in res.reason


def test_volume_profile_short_and_constant():
    engine = VolumeProfileEngine()

    # Short array (< 5 elements) -> neutral report
    data_short = np.array([
        [100.0, 10.0],
        [101.0, 20.0],
        [102.0, 15.0],
        [103.0, 5.0],
    ])
    res_short = engine.analyze("BTC", data_short, bins=10)
    assert res_short.is_success
    report_short = res_short.unwrap()
    assert report_short.poc == 0.0
    assert report_short.vah == 0.0
    assert report_short.val == 0.0

    # Constant price (price_min == price_max) -> single POC level
    data_const = np.array([
        [100.0, 10.0],
        [100.0, 20.0],
        [100.0, 15.0],
        [100.0, 5.0],
        [100.0, 30.0],
    ])
    res_const = engine.analyze("BTC", data_const, bins=10)
    assert res_const.is_success
    report_const = res_const.unwrap()
    assert report_const.poc == 100.0
    assert report_const.vah == 100.0
    assert report_const.val == 100.0
    assert len(report_const.profile) == 1
    assert report_const.profile[0].price == 100.0
    assert report_const.profile[0].node_type == "POC"


def test_volume_profile_normal_analysis():
    engine = VolumeProfileEngine()

    # Create price and volume data representing 10 discrete points
    # Min price = 10.0, Max price = 20.0 (using 10 points)
    # We set volumes to create peaks at index 1 and 3, troughs at index 2 and 6.
    prices = np.linspace(10.0, 20.0, 10)
    volumes = np.array([10.0, 50.0, 10.0, 80.0, 20.0, 30.0, 15.0, 25.0, 20.0, 10.0])
    data = np.column_stack((prices, volumes))

    res = engine.analyze("AAPL", data, bins=10)
    assert res.is_success
    report = res.unwrap()
    assert report.symbol == "AAPL"

    # Check POC is the bin with volume 80.0 (around 13.5 or corresponding bin center)
    assert report.poc > 13.0 and report.poc < 14.5

    # POC must be in hvn_levels or represented as a peak
    assert len(report.hvn_levels) > 0
    assert len(report.lvn_levels) > 0
    assert len(report.profile) == 10

    # Verify nodes found count
    assert report.nodes_found == len(report.hvn_levels) + len(report.lvn_levels)
