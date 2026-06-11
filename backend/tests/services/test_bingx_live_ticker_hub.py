from __future__ import annotations

from backend.services.bingx_live_ticker_hub import (
    _apply_account_update,
    _extract_ticker_price,
    _LiveTickerState,
    _state_to_payload,
)


def test_extract_ticker_price_from_data_row() -> None:
    parsed = _extract_ticker_price(
        {
            "dataType": "AAPL-USDT@ticker",
            "data": {"s": "AAPL-USDT", "c": "201.5", "markPrice": "201.6"},
        }
    )
    assert parsed == ("AAPL-USDT", 201.6)


def test_apply_account_update_mutates_positions() -> None:
    state = _LiveTickerState(total_equity=10_000.0, available_margin=9_000.0)
    changed = _apply_account_update(
        state,
        {
            "e": "ACCOUNT_UPDATE",
            "a": {
                "B": [{"a": "USDT", "wb": "10000", "cw": "9000"}],
                "P": [
                    {
                        "s": "AAPL-USDT",
                        "pa": "1",
                        "ps": "LONG",
                        "ep": "200",
                        "mp": "205",
                        "l": "5",
                    }
                ],
            },
        },
    )
    assert changed is True
    assert "AAPL-USDT" in state.positions
    assert state.positions["AAPL-USDT"].current_spot == 205.0


def test_state_to_payload_shape() -> None:
    state = _LiveTickerState(
        total_equity=99_999.0,
        available_margin=95_000.0,
        used_margin=4_999.0,
    )
    payload = _state_to_payload(state, event="tick")
    assert payload["type"] == "tick"
    assert payload["account"]["total_equity"] == 99_999.0
    assert payload["positions"] == []
