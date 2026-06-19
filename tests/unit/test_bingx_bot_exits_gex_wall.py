"""Unit tests for Motor ④ GEX wall stop wired into the BingX exits mixin."""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.bot.bingx_bot_exits_mixin import BingXBotExitsMixin


class _FakeResp:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.error: str | None = None


class _Harness(BingXBotExitsMixin):
    """Minimal harness exposing the GEX exit helper with mocked order calls."""

    def __init__(self) -> None:
        self.full_close_calls: list[dict[str, Any]] = []
        self.reduce_calls: list[dict[str, Any]] = []

    async def _place_full_close(
        self,
        *,
        symbol: str,
        position_side: str,
        quantity: float,
        reason: str,
        pnl_pct: float | None = None,
        pnl_usd: float | None = None,
    ) -> _FakeResp:
        self.full_close_calls.append({"symbol": symbol, "reason": reason, "quantity": quantity})
        return _FakeResp(True)

    async def _place_reduce_market(
        self,
        *,
        symbol: str,
        position_side: str,
        quantity: float,
        reason: str,
        pnl_pct: float | None = None,
        pnl_usd: float | None = None,
    ) -> _FakeResp:
        self.reduce_calls.append({"symbol": symbol, "reason": reason, "quantity": quantity})
        return _FakeResp(True)


@pytest.mark.asyncio
async def test_proximity_long_trims_20pct() -> None:
    # ARRANGE — LONG, call wall 1% above spot (positive GEX, no erosion).
    harness = _Harness()
    executions: list[Any] = []
    # ACT
    new_size = await harness._apply_gex_wall_exit(
        symbol="AAPL-USDT",
        side="LONG",
        cycle_mode="slow",
        current_spot=100.0,
        call_wall=101.0,
        put_wall=None,
        zero_gamma=None,
        net_gex_total=500_000.0,
        position_size=10.0,
        entry_price=99.0,
        pnl_pct=1.0,
        executions=executions,
    )
    # ASSERT
    assert len(harness.reduce_calls) == 1
    assert harness.reduce_calls[0]["reason"] == "gex_wall_proximity_close"
    assert harness.reduce_calls[0]["quantity"] == pytest.approx(2.0)
    assert new_size == pytest.approx(8.0)
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_breach_long_full_close_invalidation() -> None:
    # ARRANGE — spot already above the call wall → wall breached.
    harness = _Harness()
    executions: list[Any] = []
    # ACT
    new_size = await harness._apply_gex_wall_exit(
        symbol="AAPL-USDT",
        side="LONG",
        cycle_mode="slow",
        current_spot=102.0,
        call_wall=101.0,
        put_wall=None,
        zero_gamma=None,
        net_gex_total=100.0,
        position_size=10.0,
        entry_price=99.0,
        pnl_pct=2.0,
        executions=executions,
    )
    # ASSERT
    assert len(harness.full_close_calls) == 1
    assert harness.full_close_calls[0]["reason"] == "gex_wall_invalidation"
    assert new_size == pytest.approx(0.0)
    assert len(harness.reduce_calls) == 0


@pytest.mark.asyncio
async def test_non_slow_cycle_skips_gex_exit() -> None:
    # ARRANGE — same breach geometry but fast cycle → no GEX action.
    harness = _Harness()
    executions: list[Any] = []
    # ACT
    new_size = await harness._apply_gex_wall_exit(
        symbol="AAPL-USDT",
        side="LONG",
        cycle_mode="fast",
        current_spot=102.0,
        call_wall=101.0,
        put_wall=None,
        zero_gamma=None,
        net_gex_total=100.0,
        position_size=10.0,
        entry_price=99.0,
        pnl_pct=2.0,
        executions=executions,
    )
    # ASSERT
    assert new_size == pytest.approx(10.0)
    assert harness.full_close_calls == []
    assert harness.reduce_calls == []


@pytest.mark.asyncio
async def test_short_proximity_uses_put_wall() -> None:
    # ARRANGE — SHORT, put wall 1% below spot.
    harness = _Harness()
    executions: list[Any] = []
    # ACT
    new_size = await harness._apply_gex_wall_exit(
        symbol="AAPL-USDT",
        side="SHORT",
        cycle_mode="slow",
        current_spot=100.0,
        call_wall=None,
        put_wall=99.0,
        zero_gamma=None,
        net_gex_total=200_000.0,
        position_size=10.0,
        entry_price=101.0,
        pnl_pct=1.0,
        executions=executions,
    )
    # ASSERT
    assert len(harness.reduce_calls) == 1
    assert harness.reduce_calls[0]["reason"] == "gex_wall_proximity_close"
    assert new_size == pytest.approx(8.0)
