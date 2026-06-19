"""Wiring test: BingXBotService forwards dark_pool_fn to build_candidate_analysis."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from backend.models.dark_pool_snapshot import DarkPoolSnapshot

MODULE = "backend.services.bingx_bot_service"


def _snapshot() -> DarkPoolSnapshot:
    return DarkPoolSnapshot(
        symbol="AAPL",
        print_count_1h=12,
        net_notional_usd=Decimal("2000000"),
        bias="BULLISH",
        confidence=0.7,
        fetched_at=datetime.now(UTC),
        source="unusual_whales",
    )


@pytest.mark.asyncio
async def test_bot_forwards_dark_pool_fn_to_candidate_analysis() -> None:
    from backend.services.bingx_bot_service import BingXBotService

    captured: dict[str, Any] = {}

    async def _dp_fn(_underlying: str) -> DarkPoolSnapshot:
        return _snapshot()

    async def _fake_build(symbol: str, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    service = BingXBotService(dark_pool_fn=_dp_fn)
    assert service._dark_pool_fn is _dp_fn
    service._open_position_underlying_roots = AsyncMock(return_value=set())  # type: ignore[method-assign]

    with patch(f"{MODULE}.build_candidate_analysis", new=AsyncMock(side_effect=_fake_build)):
        await service._candidate_analyses_for_symbols(("AAPL-USDT",))

    assert "dark_pool_fn" in captured
    assert captured["dark_pool_fn"] is _dp_fn


@pytest.mark.asyncio
async def test_bot_dark_pool_fn_defaults_none() -> None:
    from backend.services.bingx_bot_service import BingXBotService

    service = BingXBotService()
    assert service._dark_pool_fn is None
