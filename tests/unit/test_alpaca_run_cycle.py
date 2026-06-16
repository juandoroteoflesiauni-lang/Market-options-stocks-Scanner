"""AAA tests for the native Alpaca run_cycle pipeline. # [TH][IM]"""

from __future__ import annotations

import pytest

from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.services.alpaca_r1_options_context import Route1OptionsBundle
from backend.services.alpaca_bot_service import AlpacaBotService
from backend.services.alpaca_market_hours import AlpacaMarketHoursGuard
from backend.services.alpaca_universe_funnel import SymbolBars

_BARS = 60


class _FakeClock:
    def __init__(self, is_open: bool) -> None:
        self._is_open = is_open

    async def get_clock(self) -> dict[str, object]:
        return {"is_open": self._is_open}


def _rising_bars(symbol: str, slope: float, last_volume_spike: bool) -> SymbolBars:
    rate = slope / 100.0
    closes = tuple(100.0 * ((1.0 + rate) ** i) for i in range(_BARS))
    highs = tuple(c + 1.0 for c in closes)
    lows = tuple(c - 1.0 for c in closes)
    base = [1_000_000.0 + (i % 5) * 50_000.0 for i in range(_BARS)]
    if last_volume_spike:
        base[-1] = 8_000_000.0
    return SymbolBars(
        symbol=symbol, highs=highs, lows=lows, closes=closes, volumes=tuple(base)
    )


def _bars_map() -> dict[str, SymbolBars]:
    return {
        "AAPL": _rising_bars("AAPL", slope=3.0, last_volume_spike=True),
        "SPY": _rising_bars("SPY", slope=0.5, last_volume_spike=False),
    }


@pytest.mark.asyncio
async def test_run_cycle_authorizes_long_intent_in_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ARRANGE
    client = AlpacaClient(api_key="k", secret_key="s", dry_run=True)
    service = AlpacaBotService(client=client, universe=("AAPL", "SPY"))

    async def _fake_gather(
        symbols: tuple[str, ...],
    ) -> tuple[dict[str, SymbolBars], dict[str, list]]:
        bars = _bars_map()
        klines = {sym: [] for sym in bars}
        return bars, klines

    async def _no_options_bundle(_s: str) -> Route1OptionsBundle:
        return Route1OptionsBundle(report=None, context=None)

    async def _no_pred(_s: str) -> dict:
        return {}

    monkeypatch.setattr(service, "_gather_bars_and_klines", _fake_gather)
    monkeypatch.setattr(
        "backend.services.alpaca_dual_route_service.fetch_route1_options_bundle",
        _no_options_bundle,
    )
    monkeypatch.setattr(
        "backend.services.alpaca_route1_context_service.fetch_route1_predictive_meta",
        _no_pred,
    )
    # ACT
    result = await service.run_cycle()
    # ASSERT
    assert "AAPL" in result.route1_symbols
    assert any(d.authorized for d in result.risk_decisions)


@pytest.mark.asyncio
async def test_run_cycle_skips_when_market_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE
    client = AlpacaClient(api_key="k", secret_key="s", dry_run=True)
    service = AlpacaBotService(
        client=client,
        universe=("AAPL", "SPY"),
        market_hours_guard=AlpacaMarketHoursGuard(_FakeClock(is_open=False)),
    )

    async def _fail_gather(
        symbols: tuple[str, ...],
    ) -> tuple[dict[str, SymbolBars], dict[str, list]]:
        raise AssertionError("must not fetch bars when market is closed")

    monkeypatch.setattr(service, "_gather_bars_and_klines", _fail_gather)
    # ACT
    result = await service.run_cycle()
    # ASSERT
    assert result.prefiltered == ()
    assert result.blocked_reasons == {"_market": ["market_closed"]}
