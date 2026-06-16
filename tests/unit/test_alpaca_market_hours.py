"""AAA unit tests for backend.services.alpaca_market_hours. # [TH][IM]"""

from __future__ import annotations

import pytest

from backend.services.alpaca_market_hours import AlpacaMarketHoursGuard


class _FakeClock:
    def __init__(self, payload: dict[str, object], *, raises: bool = False) -> None:
        self._payload = payload
        self._raises = raises

    async def get_clock(self) -> dict[str, object]:
        if self._raises:
            raise RuntimeError("network down")
        return self._payload


@pytest.mark.asyncio
async def test_is_market_open_true_when_session_open() -> None:
    # ARRANGE
    guard = AlpacaMarketHoursGuard(_FakeClock({"is_open": True}))
    # ACT
    result = await guard.is_market_open()
    # ASSERT
    assert result is True


@pytest.mark.asyncio
async def test_is_market_open_false_when_session_closed() -> None:
    # ARRANGE
    guard = AlpacaMarketHoursGuard(_FakeClock({"is_open": False}))
    # ACT
    result = await guard.is_market_open()
    # ASSERT
    assert result is False


@pytest.mark.asyncio
async def test_is_market_open_false_on_clock_error() -> None:
    # ARRANGE
    guard = AlpacaMarketHoursGuard(_FakeClock({}, raises=True))
    # ACT
    result = await guard.is_market_open()
    # ASSERT
    assert result is False
