from __future__ import annotations

from backend.layer_1_data.datos.bingx_ws_hub import TickAggregator


def test_tick_aggregator_emits_one_second_micro_bars() -> None:
    agg = TickAggregator("BTC-USDT")

    assert agg.add_trade({"p": "100", "q": "0.5", "T": 1_700_000_000_100}) == []
    assert agg.add_trade({"price": "102", "qty": "0.25", "time": 1_700_000_000_900}) == []
    emitted = agg.add_trade({"p": "101", "q": "0.1", "T": 1_700_000_001_050})
    flushed = agg.flush()

    assert len(emitted) == 1
    first = emitted[0]
    assert first.symbol == "BTC-USDT"
    assert first.open == 100.0
    assert first.high == 102.0
    assert first.low == 100.0
    assert first.close == 102.0
    assert first.volume == 0.75
    assert first.trade_count == 2
    assert len(flushed) == 1
    assert flushed[0].open_time_ms == 1_700_000_001_000
    assert flushed[0].close == 101.0
