from __future__ import annotations
from typing import Any
"""Tests for the BingX L2 → LOB adapter (Layer 1).

Covers the pure adapter (``build_l2_snapshot_from_bingx_depth``) and the async
wrapper (``fetch_bingx_l2_snapshot``). Network access is mocked via a
minimal stub object exposing ``fetch_order_book_perp``.
"""



import pytest

from backend.layer_1_data.datos.bingx_l2_adapter import (
    L2_SOURCE_PERP_REST,
    L2_SOURCE_UNAVAILABLE,
    BingXL2AdapterResult,
    build_l2_snapshot_from_bingx_depth,
    fetch_bingx_l2_snapshot,
)


class _StubOrderBookClient:
    """Records calls and returns a configurable payload (or raises)."""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch_order_book_perp(
        self,
        symbol: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        self.calls.append((symbol, {"limit": limit}))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._payload or {}


# ── Pure adapter ─────────────────────────────────────────────────────────────


def test_adapter_valid_book_produces_ok_result_with_metrics() -> None:
    payload = {
        "bids": [["100.0", "1.0"], ["99.5", "2.0"]],
        "asks": [["100.5", "0.5"], ["101.0", "1.5"]],
    }

    result = build_l2_snapshot_from_bingx_depth(
        "GOOGL-USDT",
        payload,
        market_type="stock_perp",
        timestamp_ms=1_700_000_000_000,
    )

    assert isinstance(result, BingXL2AdapterResult)
    assert result.ok is True
    assert result.reason == "ok"
    assert result.symbol == "GOOGL-USDT"
    assert result.source == L2_SOURCE_PERP_REST
    assert result.market_type == "stock_perp"
    assert result.timestamp_ms == 1_700_000_000_000
    assert [(lvl.price, lvl.quantity) for lvl in result.bids] == [
        (100.0, 1.0),
        (99.5, 2.0),
    ]
    assert [(lvl.price, lvl.quantity) for lvl in result.asks] == [
        (100.5, 0.5),
        (101.0, 1.5),
    ]
    # best_ask 100.5 − best_bid 100.0 = 0.5
    assert result.metrics.spread == pytest.approx(0.5)
    assert result.metrics.bid_depth == pytest.approx(3.0)
    assert result.metrics.ask_depth == pytest.approx(2.0)
    # 3.0 / (3.0 + 2.0) = 0.6
    assert result.metrics.imbalance == pytest.approx(0.6)


def test_adapter_empty_book_returns_not_ok_with_descriptive_reason() -> None:
    payload = {"bids": [], "asks": []}

    result = build_l2_snapshot_from_bingx_depth(
        "GOOGL-USDT",
        payload,
        market_type="stock_perp",
    )

    assert result.ok is False
    assert result.reason == "empty_book"
    assert result.reason  # non-empty by construction
    assert result.symbol == "GOOGL-USDT"
    # Empty book is still a REST-source response, not "unavailable".
    assert result.source == L2_SOURCE_PERP_REST
    assert result.bids == ()
    assert result.asks == ()
    assert result.metrics.spread == 0.0
    assert result.metrics.imbalance == 0.0


def test_adapter_unsupported_market_type_returns_l2_unavailable() -> None:
    payload = {"bids": [["100", "1"]], "asks": [["101", "1"]]}

    result = build_l2_snapshot_from_bingx_depth(
        "EURUSD",
        payload,
        market_type="excluded",
    )

    assert result.ok is False
    assert result.reason == "l2_unavailable"
    assert result.source == L2_SOURCE_UNAVAILABLE
    # Even though the payload had data, unsupported instruments produce no book.
    assert result.bids == ()
    assert result.asks == ()


def test_adapter_invalid_payload_shape_is_handled() -> None:
    result = build_l2_snapshot_from_bingx_depth(
        "GOOGL-USDT",
        None,

        market_type="stock_perp",
    )

    assert result.ok is False
    assert result.reason == "invalid_payload"
    assert result.source == L2_SOURCE_UNAVAILABLE


def test_adapter_skips_malformed_rows_but_keeps_valid_ones() -> None:
    payload = {
        "bids": [["bogus", "1"], ["100", "1"], ["99", "-1"]],
        "asks": [["101", "1"], ["0", "5"], None],
    }

    result = build_l2_snapshot_from_bingx_depth(
        "AAPL-USDT",
        payload,
        market_type="stock_perp",
    )

    assert result.ok is True
    assert [(lvl.price, lvl.quantity) for lvl in result.bids] == [(100.0, 1.0)]
    assert [(lvl.price, lvl.quantity) for lvl in result.asks] == [(101.0, 1.0)]


def test_adapter_missing_symbol_returns_explicit_reason() -> None:
    result = build_l2_snapshot_from_bingx_depth(
        "   ",
        {"bids": [["100", "1"]], "asks": [["101", "1"]]},
        market_type="stock_perp",
    )

    assert result.ok is False
    assert result.reason == "missing_symbol"
    assert result.source == L2_SOURCE_UNAVAILABLE


def test_adapter_dict_rows_are_supported() -> None:
    payload = {
        "bids": [{"price": "100", "quantity": "1"}],
        "asks": [{"p": "101", "q": "2"}],
    }

    result = build_l2_snapshot_from_bingx_depth(
        "GOOGL-USDT",
        payload,
        market_type="stock_index_perp",
    )

    assert result.ok is True
    assert result.market_type == "stock_index_perp"
    assert [(lvl.price, lvl.quantity) for lvl in result.bids] == [(100.0, 1.0)]
    assert [(lvl.price, lvl.quantity) for lvl in result.asks] == [(101.0, 2.0)]


def test_adapter_to_dict_is_json_safe_and_complete() -> None:
    result = build_l2_snapshot_from_bingx_depth(
        "GOOGL-USDT",
        {"bids": [["100", "1"]], "asks": [["101", "2"]]},
        market_type="stock_perp",
        timestamp_ms=1_700_000_000_000,
    )

    payload = result.to_dict()

    assert payload["symbol"] == "GOOGL-USDT"
    assert payload["source"] == L2_SOURCE_PERP_REST
    assert payload["ok"] is True
    assert payload["reason"] == "ok"
    assert payload["timestamp_ms"] == 1_700_000_000_000
    assert payload["market_type"] == "stock_perp"
    assert payload["bids"] == [{"price": 100.0, "quantity": 1.0}]
    assert payload["asks"] == [{"price": 101.0, "quantity": 2.0}]
    assert set(payload["metrics"].keys()) == {
        "spread",
        "bid_depth",
        "ask_depth",
        "imbalance",
    }


# ── Async fetch wrapper ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_calls_fetch_order_book_perp_for_googl_stock_perp() -> None:
    """Verifies that the GOOGL-USDT flow routes through fetch_order_book_perp."""

    client = _StubOrderBookClient(payload={"bids": [["100", "1"]], "asks": [["101", "1"]]})

    result = await fetch_bingx_l2_snapshot(
        client,
        "GOOGL-USDT",
        market_type="stock_perp",
        limit=15,
    )

    assert result.ok is True
    assert result.symbol == "GOOGL-USDT"
    assert client.calls == [("GOOGL-USDT", {"limit": 15})]


@pytest.mark.asyncio
async def test_fetch_skips_network_for_unsupported_market_type() -> None:
    client = _StubOrderBookClient(payload={"bids": [["100", "1"]], "asks": [["101", "1"]]})

    result = await fetch_bingx_l2_snapshot(
        client,
        "EURUSD",
        market_type="excluded",
    )

    assert result.ok is False
    assert result.reason == "l2_unavailable"
    assert client.calls == []  # no network round-trip


@pytest.mark.asyncio
async def test_fetch_network_error_surfaces_as_fetch_error_reason() -> None:
    client = _StubOrderBookClient(raise_exc=RuntimeError("boom"))

    result = await fetch_bingx_l2_snapshot(
        client,
        "GOOGL-USDT",
        market_type="stock_perp",
    )

    assert result.ok is False
    assert result.reason == "fetch_error"
    assert result.source == L2_SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_fetch_empty_book_returns_empty_book_reason() -> None:
    client = _StubOrderBookClient(payload={"bids": [], "asks": []})

    result = await fetch_bingx_l2_snapshot(
        client,
        "GOOGL-USDT",
        market_type="stock_perp",
    )

    assert result.ok is False
    assert result.reason == "empty_book"
    assert result.reason  # non-empty descriptive code


@pytest.mark.asyncio
async def test_fetch_without_market_type_does_not_short_circuit() -> None:
    """When the caller does not classify the instrument, we still attempt
    the depth fetch — the adapter then judges by payload shape alone."""
    client = _StubOrderBookClient(payload={"bids": [["100", "1"]], "asks": [["101", "1"]]})

    result = await fetch_bingx_l2_snapshot(client, "BTC-USDT")

    assert result.ok is True
    assert result.market_type is None
    assert client.calls == [("BTC-USDT", {"limit": 20})]
