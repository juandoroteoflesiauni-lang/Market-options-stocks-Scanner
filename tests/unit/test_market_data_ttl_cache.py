"""Tests TTL cache de market data. # [TH]"""

from __future__ import annotations

from backend.hub.market_data_ttl_cache import (
    cache_metrics,
    get_intraday_bars,
    get_massive_options_chain,
    intraday_cache_key,
    put_intraday_bars,
    put_massive_options_chain,
)


def test_massive_options_chain_cache_hit() -> None:
    payload = ({"data": [{"expirationDate": "2026-06-22"}]}, "massive:test", {"n": 1})
    put_massive_options_chain("AAPL", payload)
    hit = get_massive_options_chain("AAPL")
    assert hit is not None
    shaped, src, meta = hit
    assert shaped is not None
    assert src == "massive:test"
    assert meta.get("cache_hit") is True


def test_massive_options_negative_cache() -> None:
    put_massive_options_chain("CRWV", (None, "", {}))
    hit = get_massive_options_chain("CRWV")
    assert hit is not None
    shaped, _src, _meta = hit
    assert shaped is None
    metrics = cache_metrics()
    assert metrics["negative_hits"] >= 1


def test_intraday_bars_cache_roundtrip() -> None:
    key = intraday_cache_key("MSFT", "5m", max_bars=500, lookback_days=180, accept_stale=True)
    payload = {"bars": [{"t": 1, "close": 400.0}], "interval": "5m", "source": "test", "count": 1}
    put_intraday_bars(key, payload)
    hit = get_intraday_bars(key)
    assert hit is not None
    assert hit.get("cache_hit") is True
    assert hit["count"] == 1
