"""Integration tests for MarketDataHub.fetch_dark_pool_prints (Motor ⑭)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

import backend.hub.market_data_hub as hub_mod
from backend.hub.market_data_hub import MarketDataHub


def _make_hub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uw_key: str | None,
    fmp_key: str,
) -> MarketDataHub:
    monkeypatch.setattr(hub_mod.api_consumption_monitor, "record", AsyncMock())
    settings = SimpleNamespace(
        unusual_whales_api_key=SecretStr(uw_key) if uw_key is not None else None,
        fmp_api_key=SecretStr(fmp_key),
    )
    return MarketDataHub(settings, SimpleNamespace())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_uw_success_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _make_hub(monkeypatch, uw_key="uw-key", fmp_key="fmp-key")
    monkeypatch.setattr(
        hub_mod,
        "fetch_uw_dark_pool_prints",
        AsyncMock(return_value={"data": [{"premium": 2_000_000.0, "side": "buy"}]}),
    )
    result = await hub.fetch_dark_pool_prints("AAPL")
    assert result.is_success
    snapshot = result.unwrap()
    assert snapshot.source == "unusual_whales"
    assert snapshot.bias == "BULLISH"
    await hub.close()


@pytest.mark.asyncio
async def test_falls_back_to_fmp_when_no_uw_key(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _make_hub(monkeypatch, uw_key=None, fmp_key="fmp-key")
    uw_spy = AsyncMock()
    monkeypatch.setattr(hub_mod, "fetch_uw_dark_pool_prints", uw_spy)
    monkeypatch.setattr(
        hub_mod,
        "fetch_fmp_dark_pool_prints",
        AsyncMock(return_value={"data": [{"premium": 3_000_000.0, "side": "sell"}]}),
    )
    result = await hub.fetch_dark_pool_prints("AAPL")
    assert result.is_success
    snapshot = result.unwrap()
    assert snapshot.source == "fmp_fallback"
    assert snapshot.bias == "BEARISH"
    uw_spy.assert_not_called()
    await hub.close()


@pytest.mark.asyncio
async def test_no_keys_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _make_hub(monkeypatch, uw_key=None, fmp_key="")
    result = await hub.fetch_dark_pool_prints("AAPL")
    assert result.is_failure
    assert result.reason == "dark_pool_unavailable"
    await hub.close()


@pytest.mark.asyncio
async def test_uw_breaker_open_skips_to_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _make_hub(monkeypatch, uw_key="uw-key", fmp_key="")
    monkeypatch.setattr(hub._uw_breaker, "can_execute", lambda: False)
    uw_spy = AsyncMock()
    monkeypatch.setattr(hub_mod, "fetch_uw_dark_pool_prints", uw_spy)
    result = await hub.fetch_dark_pool_prints("AAPL")
    assert result.is_failure
    assert result.reason == "dark_pool_unavailable"
    uw_spy.assert_not_called()
    await hub.close()


@pytest.mark.asyncio
async def test_uw_value_error_surfaces_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _make_hub(monkeypatch, uw_key="uw-key", fmp_key="fmp-key")
    monkeypatch.setattr(
        hub_mod,
        "fetch_uw_dark_pool_prints",
        AsyncMock(side_effect=ValueError("uw_dark_pool_invalid_format")),
    )
    fmp_spy = AsyncMock()
    monkeypatch.setattr(hub_mod, "fetch_fmp_dark_pool_prints", fmp_spy)
    result = await hub.fetch_dark_pool_prints("AAPL")
    assert result.is_failure
    assert result.reason == "uw_dark_pool_invalid_format"
    fmp_spy.assert_not_called()
    await hub.close()


@pytest.mark.asyncio
async def test_uw_transport_error_falls_back_to_fmp(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _make_hub(monkeypatch, uw_key="uw-key", fmp_key="fmp-key")
    monkeypatch.setattr(
        hub_mod,
        "fetch_uw_dark_pool_prints",
        AsyncMock(side_effect=RuntimeError("connection reset")),
    )
    monkeypatch.setattr(
        hub_mod,
        "fetch_fmp_dark_pool_prints",
        AsyncMock(return_value={"data": [{"premium": 2_000_000.0, "side": "buy"}]}),
    )
    result: Any = await hub.fetch_dark_pool_prints("AAPL")
    assert result.is_success
    assert result.unwrap().source == "fmp_fallback"
    await hub.close()
