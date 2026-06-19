"""Tests for CacheWarmUp."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.hub.warmup import CacheWarmUp
from backend.models.result import Result


@pytest.mark.asyncio
async def test_warm_populates_cache_for_successful_tickers() -> None:
    hub = MagicMock()
    hub.get_intraday_candles = AsyncMock(
        side_effect=lambda t, limit=60: Result.success(
            [{"time": 1, "close": 100.0, "open": 99.0, "high": 101.0, "low": 98.0}]
        )
    )
    stats = await CacheWarmUp.warm(hub, ["SPY", "AAPL"], periods=10)
    assert stats["ok"] == 2
    assert stats["failed"] == 0


@pytest.mark.asyncio
async def test_one_ticker_failure_does_not_abort() -> None:
    hub = MagicMock()

    async def _fetch(ticker: str, limit: int = 60) -> Result:
        if ticker == "BAD":
            return Result.failure(reason="down")
        return Result.success([{"time": 1, "close": 1.0}])

    hub.get_intraday_candles = _fetch
    stats = await CacheWarmUp.warm(hub, ["SPY", "BAD"], periods=5)
    assert stats["ok"] == 1
    assert stats["failed"] == 1


@pytest.mark.asyncio
async def test_empty_universe_noop() -> None:
    hub = MagicMock()
    stats = await CacheWarmUp.warm(hub, [])
    assert stats == {"ok": 0, "failed": 0, "skipped": 0}
