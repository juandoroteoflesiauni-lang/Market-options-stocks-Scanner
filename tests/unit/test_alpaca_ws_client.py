"""AAA unit tests for the canonical AlpacaWSClient. # [TH][IM]"""

from __future__ import annotations

import json

import pytest

from backend.layer_1_data.real_time_ws.alpaca_ws_client import AlpacaWSClient


def test_auth_succeeded_true_for_authenticated_frame() -> None:
    # ARRANGE
    frame = [{"T": "success", "msg": "authenticated"}]
    # ACT
    result = AlpacaWSClient._auth_succeeded(frame)
    # ASSERT
    assert result is True


def test_auth_succeeded_false_for_error_frame() -> None:
    # ARRANGE
    frame = [{"T": "error", "msg": "auth failed"}]
    # ACT
    result = AlpacaWSClient._auth_succeeded(frame)
    # ASSERT
    assert result is False


def test_map_message_trade_includes_symbol() -> None:
    # ARRANGE
    item = {"T": "t", "S": "AAPL", "p": 190.5, "s": 100, "t": "2026-06-12T14:00:00Z"}
    # ACT
    mapped = AlpacaWSClient.map_message(item)
    # ASSERT
    assert mapped is not None
    assert mapped["ev"] == "T"
    assert mapped["sym"] == "AAPL"
    assert mapped["price"] == 190.5


def test_map_message_bar_converts_timestamp_to_ms() -> None:
    # ARRANGE
    item = {"T": "b", "S": "MSFT", "t": "2026-06-12T14:00:00Z", "c": 410.0, "v": 1000}
    # ACT
    mapped = AlpacaWSClient.map_message(item)
    # ASSERT
    assert mapped is not None
    assert mapped["ev"] == "AM"
    assert mapped["sym"] == "MSFT"
    assert isinstance(mapped["s"], int) and mapped["s"] > 0


def test_map_message_ignores_unknown_type() -> None:
    # ARRANGE
    item = {"T": "subscription"}
    # ACT
    mapped = AlpacaWSClient.map_message(item)
    # ASSERT
    assert mapped is None


@pytest.mark.asyncio
async def test_send_subscriptions_builds_channel_payload() -> None:
    # ARRANGE
    sent: list[str] = []

    class _FakeWS:
        async def send(self, message: str) -> None:
            sent.append(message)

    client = AlpacaWSClient(api_key="k", secret_key="s")
    client.websocket = _FakeWS()
    client.subscriptions = {"trades": ["AAPL", "MSFT"], "bars": []}
    # ACT
    await client._send_subscriptions()
    # ASSERT
    payload = json.loads(sent[0])
    assert payload["action"] == "subscribe"
    assert payload["trades"] == ["AAPL", "MSFT"]
    assert "bars" not in payload


@pytest.mark.asyncio
async def test_dispatch_invokes_callback_for_mapped_messages() -> None:
    # ARRANGE
    received: list[dict[str, object]] = []

    async def _callback(msg: dict[str, object]) -> None:
        received.append(msg)

    client = AlpacaWSClient(api_key="k", secret_key="s")
    client.on_message_callback = _callback
    payload = [{"T": "t", "S": "AAPL", "p": 1.0}, {"T": "subscription"}]
    # ACT
    await client._dispatch(payload)
    # ASSERT
    assert len(received) == 1
    assert received[0]["sym"] == "AAPL"
