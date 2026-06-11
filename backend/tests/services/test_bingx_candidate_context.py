from __future__ import annotations

from typing import Any

import pytest

from backend.layer_1_data.datos.bingx_client import BingXKline
from backend.services.bingx_candidate_context import (
    REASON_FETCH_FAILED,
    REASON_NO_CLIENT,
    REASON_NO_OPTIONS_FOR_CRYPTO,
    REASON_NOT_FITTED,
    build_candidate_context,
)


@pytest.fixture(autouse=True)
def mock_crypto_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.services.bingx_symbol_linker import classify_underlying

    def _classify(symbol: str) -> str:
        if symbol == "BTC-USDT":
            return "crypto_standard"
        return classify_underlying(symbol)

    monkeypatch.setattr("backend.services.bingx_candidate_context.classify_underlying", _classify)


# ── Fake clients ───────────────────────────────────────────────────────────────


class FakeBingXClient:
    def __init__(self, *, fail_klines: bool = False, fail_oi: bool = False) -> None:
        self._fail_klines = fail_klines
        self._fail_oi = fail_oi

    async def fetch_klines_perp(
        self,
        symbol: str,
        interval: str = "5m",
        *,
        limit: int,
    ) -> list[BingXKline]:
        if self._fail_klines:
            raise RuntimeError("klines unavailable")
        return [
            BingXKline(
                open_time_ms=1_000_000,
                open=100.0,
                high=105.0,
                low=95.0,
                close=102.0,
                volume=50_000.0,
                close_time_ms=1_060_000,
            )
        ]

    async def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
        if self._fail_oi:
            raise RuntimeError("oi unavailable")
        return {"openInterest": "1500000"}

    async def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
        return {"lastFundingRate": "0.0001"}


class FakeFMPClient:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def get_quote(self, symbol: str) -> dict[str, Any] | None:
        if self._fail:
            raise RuntimeError("fmp unavailable")
        return {"symbol": symbol, "price": 190.0}


class FakeMassiveClient:
    def __init__(self, *, fail: bool = False, empty: bool = False) -> None:
        self._fail = fail
        self._empty = empty

    async def get_options_chain(self, ticker: str) -> list[dict[str, Any]] | None:
        if self._fail:
            raise RuntimeError("massive unavailable")
        if self._empty:
            return None
        return [{"contract": f"{ticker}C200"}]


class FakeAlpacaClient:
    async def get_historical_bars(
        self, symbol: str, timeframe: str, *, max_bars: int, limit: int | None
    ) -> list[dict[str, Any]]:
        return [{"t": 1_000_000, "o": 189.0, "h": 192.0, "l": 188.0, "c": 191.0, "v": 1000.0}]


class FakeMetaLearner:
    def __init__(self, *, fitted: bool = True) -> None:
        self.is_fitted = fitted
        self.model_type = "lgb"


# ── Tests: identity fields ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_googl_context_has_correct_identity() -> None:
    ctx = await build_candidate_context("GOOGL-USDT")
    assert ctx.venue_symbol == "GOOGL-USDT"
    assert ctx.underlying_symbol == "GOOGL"
    assert ctx.market_type == "stock_perp"


@pytest.mark.asyncio
async def test_btc_context_is_crypto() -> None:
    ctx = await build_candidate_context("BTC-USDT")
    assert ctx.venue_symbol == "BTC-USDT"
    assert ctx.underlying_symbol == "BTC"
    assert ctx.market_type == "crypto_standard"


@pytest.mark.asyncio
async def test_on_suffix_resolves_correctly() -> None:
    ctx = await build_candidate_context("MSFTON/USDT")
    assert ctx.underlying_symbol == "MSFT"
    assert ctx.market_type == "stock_perp"


# ── Tests: venue OHLCV block ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_venue_ohlcv_available_with_client() -> None:
    ctx = await build_candidate_context("BTC-USDT", bingx_client=FakeBingXClient())
    assert ctx.venue_ohlcv_source.status == "available"
    assert ctx.venue_ohlcv_source.source_name == "bingx_perp"
    assert len(ctx.venue_ohlcv_source.klines) == 1
    assert ctx.venue_ohlcv_source.last_price == 102.0
    assert ctx.venue_ohlcv_source.open_interest == 1_500_000.0
    assert ctx.venue_ohlcv_source.funding_rate == 0.0001


@pytest.mark.asyncio
async def test_venue_ohlcv_unavailable_without_client() -> None:
    ctx = await build_candidate_context("BTC-USDT")
    assert ctx.venue_ohlcv_source.status == "unavailable"
    assert ctx.venue_ohlcv_source.reason == REASON_NO_CLIENT


@pytest.mark.asyncio
async def test_venue_ohlcv_unavailable_on_kline_failure() -> None:
    ctx = await build_candidate_context("BTC-USDT", bingx_client=FakeBingXClient(fail_klines=True))
    assert ctx.venue_ohlcv_source.status == "unavailable"
    assert ctx.venue_ohlcv_source.reason == REASON_FETCH_FAILED


@pytest.mark.asyncio
async def test_venue_ohlcv_available_even_when_oi_fails() -> None:
    """Klines succeed but OI fails — block is still 'available' with oi=None."""
    ctx = await build_candidate_context("BTC-USDT", bingx_client=FakeBingXClient(fail_oi=True))
    assert ctx.venue_ohlcv_source.status == "available"
    assert ctx.venue_ohlcv_source.open_interest is None
    assert ctx.venue_ohlcv_source.last_price is not None


# ── Tests: underlying OHLCV block ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stock_underlying_available_with_fmp_and_alpaca() -> None:
    ctx = await build_candidate_context(
        "GOOGL-USDT",
        bingx_client=FakeBingXClient(),
        fmp_client=FakeFMPClient(),
        alpaca_client=FakeAlpacaClient(),
    )
    block = ctx.underlying_ohlcv_source
    assert block.status == "available"
    assert block.source_name == "alpaca"
    assert block.fmp_quote == {"symbol": "GOOGL", "price": 190.0}
    assert len(block.bars) == 1


@pytest.mark.asyncio
async def test_stock_underlying_unavailable_without_any_client() -> None:
    ctx = await build_candidate_context("GOOGL-USDT")
    block = ctx.underlying_ohlcv_source
    assert block.status == "unavailable"
    assert block.reason == REASON_NO_CLIENT


@pytest.mark.asyncio
async def test_crypto_underlying_uses_bingx_as_source() -> None:
    ctx = await build_candidate_context("BTC-USDT", bingx_client=FakeBingXClient())
    block = ctx.underlying_ohlcv_source
    assert block.status == "available"
    assert block.source_name == "bingx_perp"
    assert len(block.bars) == 1


@pytest.mark.asyncio
async def test_crypto_underlying_unavailable_without_bingx_client() -> None:
    ctx = await build_candidate_context("BTC-USDT")
    block = ctx.underlying_ohlcv_source
    assert block.status == "unavailable"
    assert block.reason == REASON_NO_CLIENT


# ── Tests: options block ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_options_unavailable_for_crypto() -> None:
    ctx = await build_candidate_context("BTC-USDT", massive_client=FakeMassiveClient())
    assert ctx.options_source.status == "unavailable"
    assert ctx.options_source.reason == REASON_NO_OPTIONS_FOR_CRYPTO


@pytest.mark.asyncio
async def test_options_available_for_stock_perp() -> None:
    ctx = await build_candidate_context("GOOGL-USDT", massive_client=FakeMassiveClient())
    assert ctx.options_source.status == "available"
    assert ctx.options_source.source_name == "massive_polygon"
    assert len(ctx.options_source.chain) == 1


@pytest.mark.asyncio
async def test_options_unavailable_without_massive_client() -> None:
    ctx = await build_candidate_context("GOOGL-USDT")
    assert ctx.options_source.status == "unavailable"
    assert ctx.options_source.reason == REASON_NO_CLIENT


@pytest.mark.asyncio
async def test_options_unavailable_when_massive_fails() -> None:
    ctx = await build_candidate_context("GOOGL-USDT", massive_client=FakeMassiveClient(fail=True))
    assert ctx.options_source.status == "unavailable"
    assert ctx.options_source.reason == REASON_FETCH_FAILED


# ── Tests: predictive block ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_predictive_available_when_model_fitted() -> None:
    ctx = await build_candidate_context("BTC-USDT", meta_learner=FakeMetaLearner(fitted=True))
    assert ctx.predictive_source.status == "available"
    assert ctx.predictive_source.is_fitted is True
    assert ctx.predictive_source.model_version == "lgb"


@pytest.mark.asyncio
async def test_predictive_unavailable_without_model() -> None:
    ctx = await build_candidate_context("BTC-USDT")
    assert ctx.predictive_source.status == "unavailable"
    assert ctx.predictive_source.reason == REASON_NO_CLIENT


@pytest.mark.asyncio
async def test_predictive_unavailable_when_model_not_fitted() -> None:
    ctx = await build_candidate_context("BTC-USDT", meta_learner=FakeMetaLearner(fitted=False))
    assert ctx.predictive_source.status == "unavailable"
    assert ctx.predictive_source.reason == REASON_NOT_FITTED


# ── Tests: L2 block ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l2_available_when_hub_provided() -> None:
    ctx = await build_candidate_context("BTC-USDT", ws_hub=object())
    assert ctx.l2_source.status == "available"
    assert ctx.l2_source.source_name == "bingx_ws_hub"
    assert ctx.l2_source.micro_bars == ()


@pytest.mark.asyncio
async def test_l2_unavailable_without_hub() -> None:
    ctx = await build_candidate_context("BTC-USDT")
    assert ctx.l2_source.status == "unavailable"
    assert ctx.l2_source.reason == REASON_NO_CLIENT


# ── Tests: isolation — one source failure does not break others ────────────────


@pytest.mark.asyncio
async def test_source_failure_isolation() -> None:
    """Failing venue klines does not prevent options or predictive from succeeding."""
    ctx = await build_candidate_context(
        "GOOGL-USDT",
        bingx_client=FakeBingXClient(fail_klines=True),
        massive_client=FakeMassiveClient(),
        meta_learner=FakeMetaLearner(fitted=True),
    )
    assert ctx.venue_ohlcv_source.status == "unavailable"
    assert ctx.options_source.status == "available"
    assert ctx.predictive_source.status == "available"


# ── Tests: to_dict ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_to_dict_is_serialisable() -> None:
    ctx = await build_candidate_context(
        "GOOGL-USDT",
        bingx_client=FakeBingXClient(),
        fmp_client=FakeFMPClient(),
        massive_client=FakeMassiveClient(),
        meta_learner=FakeMetaLearner(),
        ws_hub=object(),
    )
    d = ctx.to_dict()
    assert d["venue_symbol"] == "GOOGL-USDT"
    assert d["underlying_symbol"] == "GOOGL"
    assert d["market_type"] == "stock_perp"
    assert isinstance(d["venue_ohlcv_source"], dict)
    assert isinstance(d["options_source"], dict)
    assert d["options_source"]["status"] == "available"
