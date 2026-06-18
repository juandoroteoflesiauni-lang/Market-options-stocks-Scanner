"""FMP motor candles for BingX 16-engine venue stack. # [PD-6][TH]"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.bingx_technical_bridge import (
    MIN_BARS_FOR_SMC,
    SOURCE_VENUE_FMP,
    fetch_fmp_motor_candles,
    klines_to_candles,
    motor_fmp_max_bars,
)


def test_klines_to_candles_accepts_fmp_intraday_shape() -> None:
    bars = [
        {"t": 1_700_000_000_000, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "volume": 5000},
        {"t": 1_700_000_300_000, "o": 100.5, "h": 102.0, "l": 100.0, "c": 101.5, "volume": 6000},
    ]
    candles = klines_to_candles(bars)
    assert len(candles) == 2
    assert candles[0]["open"] == 100.0
    assert candles[1]["close"] == 101.5


def test_motor_fmp_max_bars_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINGX_MOTOR_FMP_MAX_BARS", "50")
    assert motor_fmp_max_bars() == 100
    monkeypatch.setenv("BINGX_MOTOR_FMP_MAX_BARS", "2000")
    assert motor_fmp_max_bars() == 1000
    monkeypatch.setenv("BINGX_MOTOR_FMP_MAX_BARS", "750")
    assert motor_fmp_max_bars() == 750


@pytest.mark.asyncio
async def test_fetch_fmp_motor_candles_maps_bars() -> None:
    fake_bars = [
        {
            "t": 1_700_000_000_000 + i * 300_000,
            "o": 10 + i,
            "h": 11 + i,
            "l": 9 + i,
            "c": 10.5 + i,
            "volume": 100,
        }
        for i in range(120)
    ]

    def _fake_fetch(sym: str, interval: str, *, max_bars: int | None = None, **_: object) -> dict:
        assert sym == "AMD"
        assert interval == "5m"
        return {"bars": fake_bars, "source": "FMP Enterprise", "count": len(fake_bars)}

    with patch(
        "backend.layer_1_data.datos.intraday_bars_fetcher.fetch_intraday_bars",
        side_effect=_fake_fetch,
    ):
        candles, source, count = await fetch_fmp_motor_candles("AMD", "5m")

    assert count == 120
    assert source == "FMP Enterprise"
    assert len(candles) == 120
    assert candles[-1]["close"] == pytest.approx(10.5 + 119)


@pytest.mark.asyncio
async def test_attach_venue_technical_uses_fmp_for_stock_perp() -> None:
    from backend.services.bingx_candidate_analysis import (
        BingXL2Block,
        BingXTechnicalBlock,
        _attach_venue_technical,
    )
    from backend.services.bingx_candidate_context import (
        BingXCandidateContext,
        L2SourceBlock,
        OptionsSourceBlock,
        PredictiveSourceBlock,
        UnderlyingOHLCVBlock,
        VenueOHLCVBlock,
    )

    motor_candles = [
        {
            "time": 1_700_000_000_000 + i * 300_000,
            "open": 100 + i,
            "high": 101 + i,
            "low": 99 + i,
            "close": 100.5 + i,
            "volume": 1,
        }
        for i in range(MIN_BARS_FOR_SMC)
    ]
    fetcher = AsyncMock(
        return_value={
            "ok": True,
            "symbol": "AMD",
            "smc": {"ok": True, "bias": "BULLISH"},
            "vsa": {"ok": True, "signal": "STRONG_BUY"},
            "meta": {"bars": MIN_BARS_FOR_SMC},
        }
    )
    ctx = BingXCandidateContext(
        venue_symbol="AMDUS-USDT",
        underlying_symbol="AMD",
        market_type="stock_perp",
        venue_ohlcv_source=VenueOHLCVBlock(
            status="available",
            source_name="bingx_perp",
            klines=(
                SimpleNamespace(
                    open_time_ms=1,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=1,
                ),
            ),
        ),
        underlying_ohlcv_source=UnderlyingOHLCVBlock(
            status="available", source_name="fmp_historical"
        ),
        options_source=OptionsSourceBlock(status="unavailable", source_name="none"),
        predictive_source=PredictiveSourceBlock(status="unavailable", source_name="none"),
        l2_source=L2SourceBlock(status="unavailable", source_name="unavailable"),
    )
    block = BingXTechnicalBlock(status="available", source="fmp", quality_score=0.5)
    l2 = BingXL2Block(status="unavailable", source="bingx", quality_score=0.0)

    with patch(
        "backend.services.bingx_candidate_analysis.fetch_fmp_motor_candles",
        new=AsyncMock(return_value=(motor_candles, "FMP Enterprise", len(motor_candles))),
    ):
        out = await _attach_venue_technical(
            block,
            venue_symbol="AMDUS-USDT",
            underlying_symbol="AMD",
            market_type="stock_perp",
            ctx=ctx,
            l2_block=l2,
            timeframe="5m",
            technical_fn=fetcher,
        )

    assert out.venue_technical is not None
    assert out.venue_technical.get("status") == "available"
    assert out.venue_technical.get("source") == SOURCE_VENUE_FMP
    fetcher.assert_awaited_once()
    args = fetcher.await_args
    assert args is not None
    assert args.args[0] == "AMD"
