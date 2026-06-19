"""Unit tests for Motor ⑭ — dark pool fetcher + normalizer (no real network)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.hub.fetchers.unusual_whales_dark_pool import fetch_uw_dark_pool_prints
from backend.hub.normalizers.dark_pool_normalizer import DarkPoolNormalizer
from backend.models.dark_pool_snapshot import DarkPoolSnapshot


def _mock_client(payload: Any) -> AsyncMock:
    response = MagicMock()
    response.raise_for_status = MagicMock(return_value=None)
    response.json = MagicMock(return_value=payload)
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    return client


# ── Fetcher ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_missing_api_key_raises() -> None:
    client = _mock_client({"data": []})
    with pytest.raises(ValueError, match="uw_api_key_missing"):
        await fetch_uw_dark_pool_prints(client, "", "AAPL")
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_wraps_list_payload() -> None:
    client = _mock_client([{"premium": 1000.0, "side": "buy"}])
    result = await fetch_uw_dark_pool_prints(client, "key-123", "aapl")
    assert result == {"data": [{"premium": 1000.0, "side": "buy"}]}
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_passes_through_dict_payload() -> None:
    client = _mock_client({"data": [{"premium": 5.0}], "ticker": "AAPL"})
    result = await fetch_uw_dark_pool_prints(client, "key-123", "AAPL")
    assert result["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_fetch_invalid_payload_raises() -> None:
    client = _mock_client("not-json")
    with pytest.raises(ValueError, match="uw_dark_pool_invalid_format"):
        await fetch_uw_dark_pool_prints(client, "key-123", "AAPL")


# ── Normalizer ───────────────────────────────────────────────────────────────


def test_normalize_bullish_net_notional() -> None:
    raw = {
        "data": [
            {"premium": 2_000_000.0, "side": "buy"},
            {"premium": 500_000.0, "side": "sell"},
        ]
    }
    snap = DarkPoolNormalizer().normalize(raw, symbol="aapl")
    assert isinstance(snap, DarkPoolSnapshot)
    assert snap.symbol == "AAPL"
    assert snap.print_count_1h == 2
    # net = +2,000,000 - 500,000 = 1,500,000 > 1,000,000 threshold → BULLISH
    assert snap.net_notional_usd == Decimal("1500000.0")
    assert snap.bias == "BULLISH"
    assert 0.0 < snap.confidence <= 1.0


def test_normalize_bearish_net_notional() -> None:
    raw = {"data": [{"premium": 3_000_000.0, "side": "sell"}]}
    snap = DarkPoolNormalizer().normalize(raw, symbol="AAPL")
    assert snap.net_notional_usd == Decimal("-3000000.0")
    assert snap.bias == "BEARISH"


def test_normalize_price_size_fallback() -> None:
    raw = {"data": [{"price": 100.0, "size": 50, "side": "buy"}]}
    snap = DarkPoolNormalizer().normalize(raw, symbol="AAPL")
    assert snap.print_count_1h == 1
    assert snap.net_notional_usd == Decimal("5000")


def test_normalize_zero_prints_is_neutral() -> None:
    snap = DarkPoolNormalizer().normalize({"data": []}, symbol="AAPL")
    assert snap.print_count_1h == 0
    assert snap.net_notional_usd == Decimal("0")
    assert snap.bias == "NEUTRAL"
    assert snap.confidence == 0.0


def test_normalize_unsigned_prints_stay_neutral() -> None:
    # No side info → net notional 0 → NEUTRAL regardless of size.
    raw = {"data": [{"premium": 9_000_000.0}, {"premium": 9_000_000.0}]}
    snap = DarkPoolNormalizer().normalize(raw, symbol="AAPL")
    assert snap.net_notional_usd == Decimal("0")
    assert snap.bias == "NEUTRAL"
    assert snap.print_count_1h == 2


def test_net_notional_is_decimal_type() -> None:
    raw = {"data": [{"premium": 1234.56, "side": "buy"}]}
    snap = DarkPoolNormalizer().normalize(raw, symbol="AAPL")
    assert isinstance(snap.net_notional_usd, Decimal)
