"""AAA unit tests for backend.services.alpaca_universe_fetcher. # [TH][IM]"""

from __future__ import annotations

from backend.services.alpaca_universe_fetcher import _rank_by_volume


def test_rank_by_volume_orders_descending_and_limits() -> None:
    # ARRANGE
    snapshots = {
        "AAA": {"prevDailyBar": {"v": 100}},
        "BBB": {"prevDailyBar": {"v": 900}},
        "CCC": {"prevDailyBar": {"v": 500}},
    }
    # ACT
    ranked = _rank_by_volume(snapshots, limit=2)
    # ASSERT
    assert ranked == ["BBB", "CCC"]


def test_rank_by_volume_handles_missing_prev_bar() -> None:
    # ARRANGE
    snapshots = {
        "AAA": {"prevDailyBar": {"v": 10}},
        "BBB": {},  # no prevDailyBar → volume 0
    }
    # ACT
    ranked = _rank_by_volume(snapshots, limit=5)
    # ASSERT
    assert ranked[0] == "AAA"
    assert "BBB" in ranked


def test_universe_fetcher_has_no_bingx_dependency() -> None:
    # ARRANGE
    import backend.services.alpaca_universe_fetcher as mod

    # ACT
    source = mod.__file__
    with open(source, encoding="utf-8") as fh:
        content = fh.read()
    # ASSERT
    assert "bingx" not in content.lower()
