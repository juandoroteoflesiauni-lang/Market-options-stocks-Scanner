"""Tests for bingx_candidate_analysis — unified BingX candidate analysis contract.

Coverage:
- Pure helpers: _compute_venue_ta, _compute_readiness_score, _collect_data_sources,
  _collect_errors (no I/O, no async)
- Structural: to_dict JSON safety, engine_statuses completeness
- Integration: build_candidate_analysis with all engines mocked
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.bingx_candidate_analysis import (
    BingXCandidateAnalysis,
    BingXExchangeDerivativesBlock,
    BingXL2Block,
    BingXOptionsBlock,
    BingXPredictiveBlock,
    BingXTechnicalBlock,
    BingXUnderlyingBlock,
    BingXVenueBlock,
    _collect_data_sources,
    _collect_errors,
    _compute_readiness_score,
    _compute_venue_ta,
    build_candidate_analysis,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _kline_dicts(closes: list[float]) -> tuple[dict, ...]:
    return tuple(
        {
            "open_time_ms": i * 1000,
            "close_time_ms": i * 1000 + 299_000,
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 100.0,
        }
        for i, c in enumerate(closes)
    )


def _available_venue(source: str = "bingx_perp_klines") -> BingXVenueBlock:
    return BingXVenueBlock(venue_symbol="BTC-USDT", status="available", source=source)


def _unavailable_venue(reason: str | None = None) -> BingXVenueBlock:
    return BingXVenueBlock(
        venue_symbol="BTC-USDT", status="unavailable", source="none", reason=reason
    )


def _available_underlying() -> BingXUnderlyingBlock:
    return BingXUnderlyingBlock(
        underlying_symbol="BTC",
        market_type="crypto_standard",
        ohlcv_status="available",
        source="alpaca",
    )


def _unavailable_underlying(reason: str | None = None) -> BingXUnderlyingBlock:
    return BingXUnderlyingBlock(
        underlying_symbol="BTC",
        market_type="crypto_standard",
        ohlcv_status="unavailable",
        source="none",
        reason=reason,
    )


# ── _compute_venue_ta ─────────────────────────────────────────────────────────


def test_compute_venue_ta_empty_returns_none() -> None:
    assert _compute_venue_ta(()) is None


def test_compute_venue_ta_fewer_than_22_bars_returns_base_with_none_metrics() -> None:
    klines = _kline_dicts([100.0 + i for i in range(10)])
    result = _compute_venue_ta(klines)
    assert result is not None
    assert result["bars_count"] == 10
    assert result["rsi_14"] is None
    assert result["ema_9"] is None
    assert result["ema_21"] is None
    assert result["trend"] == "neutral"


def test_compute_venue_ta_sufficient_bars_ascending_produces_bullish() -> None:
    closes = [100.0 + i for i in range(30)]
    klines = _kline_dicts(closes)
    result = _compute_venue_ta(klines)
    assert result is not None
    assert result["bars_count"] == 30
    assert result["rsi_14"] is not None
    assert result["ema_9"] is not None
    assert result["ema_21"] is not None
    assert result["trend"] == "bullish"


def test_compute_venue_ta_descending_produces_bearish() -> None:
    closes = [200.0 - i for i in range(30)]
    klines = _kline_dicts(closes)
    result = _compute_venue_ta(klines)
    assert result is not None
    assert result["trend"] == "bearish"


# ── _compute_readiness_score ──────────────────────────────────────────────────


def test_readiness_zero_when_venue_unavailable() -> None:
    score = _compute_readiness_score(
        "crypto_standard",
        _unavailable_venue(),
        BingXTechnicalBlock(),
        BingXPredictiveBlock(),
        BingXL2Block(),
    )
    assert score == 0.0


def test_readiness_crypto_l2_unavailable_uses_degraded_contribution() -> None:
    # crypto: only venue + l2 contribute; l2 unavailable → 0.2 contribution
    score = _compute_readiness_score(
        "crypto_standard",
        _available_venue(),
        BingXTechnicalBlock(),  # unavailable — not penalized for crypto
        BingXPredictiveBlock(),  # unavailable — not penalized for crypto
        BingXL2Block(),  # unavailable → 0.2
    )
    # contributions: [1.0, 0.2] → avg = 0.6
    assert score == 0.6


def test_readiness_crypto_l2_available_raises_score() -> None:
    l2 = BingXL2Block(status="available", source="bingx_l2_snapshot_rest", quality_score=0.8)
    score = _compute_readiness_score(
        "crypto_standard",
        _available_venue(),
        BingXTechnicalBlock(),
        BingXPredictiveBlock(),
        l2,
    )
    # contributions: [1.0, 0.8] → avg = 0.9
    assert score == 0.9


def test_readiness_equity_all_engines_available() -> None:
    tech = BingXTechnicalBlock(status="available", source="fmp", quality_score=0.8)
    pred = BingXPredictiveBlock(status="available", source="equity_heuristic", quality_score=0.6)
    l2 = BingXL2Block(status="available", source="bingx_l2", quality_score=0.9)
    score = _compute_readiness_score(
        "stock_perp",
        _available_venue(),
        tech,
        pred,
        l2,
    )
    # contributions: [1.0, 0.8, 0.6, 0.9] → avg = 3.3 / 4 = 0.825
    assert score == 0.825


def test_readiness_equity_all_engines_unavailable() -> None:
    score = _compute_readiness_score(
        "stock_perp",
        _available_venue(),
        BingXTechnicalBlock(),  # unavailable → 0.0
        BingXPredictiveBlock(),  # unavailable → 0.0
        BingXL2Block(),  # unavailable → 0.2
    )
    # contributions: [1.0, 0.0, 0.0, 0.2] → avg = 1.2 / 4 = 0.3
    assert score == 0.3


# ── _collect_data_sources ─────────────────────────────────────────────────────


def test_collect_data_sources_all_available() -> None:
    venue = _available_venue("bingx_perp_klines")
    underlying = _available_underlying()
    options = BingXOptionsBlock(status="available", source="massive_polygon")
    tech = BingXTechnicalBlock(status="available", source="fmp")
    pred = BingXPredictiveBlock(status="available", source="equity_heuristic")
    l2 = BingXL2Block(status="available", source="bingx_l2_snapshot_rest")
    derivatives = BingXExchangeDerivativesBlock(
        status="available",
        source="exchange_derivatives_public",
        data_sources=("binance_public_derivatives", "deribit_public_derivatives"),
    )
    sources = _collect_data_sources(venue, underlying, options, tech, pred, l2, derivatives)
    assert set(sources) == {
        "bingx_perp_klines",
        "alpaca",
        "massive_polygon",
        "fmp",
        "equity_heuristic",
        "bingx_l2_snapshot_rest",
        "binance_public_derivatives",
        "deribit_public_derivatives",
    }


def test_collect_data_sources_only_available_blocks_included() -> None:
    venue = _available_venue("bingx_perp_klines")
    underlying = _unavailable_underlying()
    sources = _collect_data_sources(
        venue,
        underlying,
        BingXOptionsBlock(),
        BingXTechnicalBlock(),
        BingXPredictiveBlock(),
        BingXL2Block(),
        BingXExchangeDerivativesBlock(),
    )
    assert sources == ("bingx_perp_klines",)


# ── _collect_errors ───────────────────────────────────────────────────────────


def test_collect_errors_available_blocks_are_excluded() -> None:
    errors = _collect_errors(
        _available_venue(),
        _available_underlying(),
        BingXOptionsBlock(),
        BingXTechnicalBlock(),
        BingXPredictiveBlock(),
        BingXL2Block(),
        BingXExchangeDerivativesBlock(status="available", source="exchange_derivatives_public"),
    )
    assert "venue" not in errors
    assert "underlying" not in errors


def test_collect_errors_captures_unavailable_blocks_with_reason() -> None:
    errors = _collect_errors(
        _unavailable_venue("no_bingx_client"),
        _unavailable_underlying("no_fmp_client"),
        BingXOptionsBlock(status="unavailable", reason="no_options_client"),
        BingXTechnicalBlock(status="unavailable", reason="no_equity_ta_for_market_type"),
        BingXPredictiveBlock(status="unavailable", reason="no_predictive_for_market_type"),
        BingXL2Block(status="unavailable", reason="l2_fetch_failed"),
        BingXExchangeDerivativesBlock(
            status="unavailable",
            source="exchange_derivatives_public",
            reason="exchange_derivatives_fetch_failed",
        ),
    )
    assert errors["venue"] == "no_bingx_client"
    assert errors["underlying"] == "no_fmp_client"
    assert errors["options"] == "no_options_client"
    assert errors["technical"] == "no_equity_ta_for_market_type"
    assert errors["predictive"] == "no_predictive_for_market_type"
    assert errors["l2"] == "l2_fetch_failed"
    assert errors["exchange_derivatives"] == "exchange_derivatives_fetch_failed"


def test_collect_errors_ignores_unavailable_blocks_without_reason() -> None:
    # Default BingXTechnicalBlock has status="unavailable" and reason=None
    errors = _collect_errors(
        _available_venue(),
        _available_underlying(),
        BingXOptionsBlock(),
        BingXTechnicalBlock(),
        BingXPredictiveBlock(),
        BingXL2Block(),
        BingXExchangeDerivativesBlock(),
    )
    assert "technical" not in errors
    assert "predictive" not in errors
    assert "l2" not in errors


# ── to_dict and engine_statuses ───────────────────────────────────────────────


def _minimal_analysis(market_type: str = "crypto_standard") -> BingXCandidateAnalysis:
    venue_symbol = "BTC-USDT" if market_type == "crypto_standard" else "AAPL-USDT"
    underlying = "BTC" if market_type == "crypto_standard" else "AAPL"
    return BingXCandidateAnalysis(
        venue_symbol=venue_symbol,
        underlying_symbol=underlying,
        market_type=market_type,
        venue=_available_venue(),
        underlying=_available_underlying(),
        options=BingXOptionsBlock(),
        technical=BingXTechnicalBlock(),
        predictive=BingXPredictiveBlock(),
        l2=BingXL2Block(),
        exchange_derivatives=BingXExchangeDerivativesBlock(),
        readiness_score=0.6,
        captured_at="2026-05-20T00:00:00+00:00",
    )


def test_to_dict_is_json_safe() -> None:
    d = _minimal_analysis().to_dict()
    serialised = json.dumps(d)  # must not raise
    parsed = json.loads(serialised)
    assert parsed["venue_symbol"] == "BTC-USDT"
    assert parsed["readiness_score"] == 0.6


def test_to_dict_contains_all_top_level_keys() -> None:
    d = _minimal_analysis().to_dict()
    expected = {
        "venue_symbol",
        "underlying_symbol",
        "market_type",
        "venue",
        "underlying",
        "options",
        "technical",
        "predictive",
        "l2",
        "exchange_derivatives",
        "institutional_research",
        "data_sources",
        "errors",
        "readiness_score",
        "captured_at",
    }
    assert set(d.keys()) == expected


def test_engine_statuses_returns_all_six_engines() -> None:
    statuses = _minimal_analysis().engine_statuses()
    assert set(statuses.keys()) == {
        "venue",
        "underlying",
        "options",
        "technical",
        "predictive",
        "l2",
        "exchange_derivatives",
    }


def test_engine_statuses_venue_reflects_block_status() -> None:
    analysis = _minimal_analysis()
    statuses = analysis.engine_statuses()
    assert statuses["venue"].status == "available"
    assert statuses["venue"].source == "bingx_perp_klines"


def test_engine_statuses_technical_reflects_block_status() -> None:
    statuses = _minimal_analysis().engine_statuses()
    assert statuses["technical"].status == "unavailable"


# ── build_candidate_analysis integration ─────────────────────────────────────

MODULE = "backend.services.bingx_candidate_analysis"


def _make_derivatives_bridge_result(
    *,
    status: str = "unavailable",
    source: str = "none",
    reason: str | None = "exchange_derivatives_only_for_crypto",
    quality_score: float | None = None,
    data_sources: tuple[str, ...] = (),
    metrics: dict[str, object] | None = None,
) -> MagicMock:
    result = MagicMock()
    result.status = status
    result.source = source
    result.reason = reason
    result.quality_score = quality_score
    result.data_sources = data_sources
    result.metrics = metrics
    result.providers = ()
    result.to_dict.return_value = {
        "status": status,
        "source": source,
        "reason": reason,
        "quality_score": quality_score,
        "data_sources": list(data_sources),
        "metrics": metrics,
    }
    return result


@pytest.fixture(autouse=True)
def _stub_exchange_derivatives_bridge() -> object:
    with patch(
        f"{MODULE}.build_exchange_derivatives_bridge",
        new=AsyncMock(return_value=_make_derivatives_bridge_result()),
    ) as mocked:
        yield mocked


def _make_ctx_mock(
    venue_symbol: str,
    underlying_symbol: str,
    market_type: str,
    *,
    venue_available: bool = True,
    options_chain_len: int = 0,
) -> MagicMock:
    venue_src = MagicMock()
    venue_src.status = "available" if venue_available else "unavailable"
    venue_src.source_name = "bingx_perp_klines" if venue_available else "none"
    venue_src.reason = None if venue_available else "no_client"
    venue_src.klines = ()
    venue_src.funding_rate = 0.001 if venue_available else None
    venue_src.open_interest = 5_000_000.0 if venue_available else None

    underlying_src = MagicMock()
    underlying_src.status = "unavailable"
    underlying_src.source_name = "none"
    underlying_src.fmp_quote = None
    underlying_src.reason = None

    options_src = MagicMock()
    options_src.status = "unavailable"
    options_src.source_name = "none"
    options_src.reason = None
    options_src.chain = ()

    ctx = MagicMock()
    ctx.venue_symbol = venue_symbol
    ctx.underlying_symbol = underlying_symbol
    ctx.market_type = market_type
    ctx.venue_ohlcv_source = venue_src
    ctx.underlying_ohlcv_source = underlying_src
    ctx.options_source = options_src
    return ctx


def _make_lob_mock(*, ok: bool = True, quality: float = 0.75) -> MagicMock:
    lob = MagicMock()
    lob.ok = ok
    lob.source = "bingx_l2_snapshot_rest" if ok else "bingx_l2_unavailable"
    lob.error = None if ok else "l2_unavailable:snapshot_empty"
    lob.data_quality_score = quality if ok else None
    lob.model_dump.return_value = {
        "ok": ok,
        "source": lob.source,
        "data_quality_score": lob.data_quality_score,
    }
    return lob


async def test_build_candidate_analysis_crypto_no_equity_blocks() -> None:
    ctx = _make_ctx_mock("BTC-USDT", "BTC", "crypto_standard")

    with (
        patch(f"{MODULE}.underlying_from_bingx_symbol", return_value="BTC"),
        patch(f"{MODULE}.classify_underlying", return_value="crypto_standard"),
        patch(f"{MODULE}.build_candidate_context", new=AsyncMock(return_value=ctx)),
        patch(f"{MODULE}.analyze_bingx_l2", new=AsyncMock(return_value=_make_lob_mock())),
        patch(f"{MODULE}.EquityTASnapshotService") as MockTA,
        patch(
            f"{MODULE}.equity_probabilistic_summary",
            new=AsyncMock(return_value={"ok": False, "reason": "not_called"}),
        ),
    ):
        MockTA.return_value.snapshot = AsyncMock(
            return_value={"ok": False, "reason": "should_not_be_called"}
        )
        result = await build_candidate_analysis("BTC-USDT", bingx_client=MagicMock())

    assert result.market_type == "crypto_standard"
    assert result.technical.status == "unavailable"
    assert result.technical.reason == "no_equity_ta_for_market_type"
    assert result.predictive.status == "unavailable"
    # The predictive bridge surfaces the crypto-specific stable reason now —
    # see ``REASON_CRYPTO_NOT_WIRED`` in bingx_predictive_bridge.py.
    assert result.predictive.reason == "predictive_crypto_not_wired"
    assert result.l2.status == "available"
    assert result.readiness_score > 0.0


async def test_build_candidate_analysis_crypto_attaches_exchange_derivatives(
    _stub_exchange_derivatives_bridge: AsyncMock,
) -> None:
    ctx = _make_ctx_mock("BTC-USDT", "BTC", "crypto_standard")
    _stub_exchange_derivatives_bridge.return_value = _make_derivatives_bridge_result(
        status="available",
        source="exchange_derivatives_public",
        reason=None,
        quality_score=0.8,
        data_sources=("binance_public_derivatives", "okx_public_derivatives"),
        metrics={"provider_count": 2, "available_provider_count": 2},
    )

    with (
        patch(f"{MODULE}.underlying_from_bingx_symbol", return_value="BTC"),
        patch(f"{MODULE}.classify_underlying", return_value="crypto_standard"),
        patch(f"{MODULE}.build_candidate_context", new=AsyncMock(return_value=ctx)),
        patch(f"{MODULE}.analyze_bingx_l2", new=AsyncMock(return_value=_make_lob_mock())),
        patch(f"{MODULE}.EquityTASnapshotService"),
        patch(
            f"{MODULE}.equity_probabilistic_summary",
            new=AsyncMock(return_value={"ok": False}),
        ),
    ):
        result = await build_candidate_analysis("BTC-USDT", bingx_client=MagicMock())

    assert result.exchange_derivatives.status == "available"
    assert result.exchange_derivatives.quality_score == pytest.approx(0.8)
    assert result.exchange_derivatives.metrics == {
        "provider_count": 2,
        "available_provider_count": 2,
    }
    assert "binance_public_derivatives" in result.data_sources
    assert "okx_public_derivatives" in result.data_sources
    assert result.engine_statuses()["exchange_derivatives"].status == "available"


async def test_build_candidate_analysis_stock_perp_all_available() -> None:
    ctx = _make_ctx_mock("AAPL-USDT", "AAPL", "stock_perp", venue_available=True)

    ta_snapshot = {
        "ok": True,
        "source": "fmp",
        "bars_used": 200,
        "rsi_14": 58.0,
        "ema_fast": 175.0,
        "ema_slow": 170.0,
        "trend_direction": "bullish",
    }
    pred_summary = {
        "ok": True,
        "source": "equity_heuristic",
        "confidence": 0.7,
        "bull_probability": 0.6,
        "bear_probability": 0.3,
        "neutral_probability": 0.1,
    }

    with (
        patch(f"{MODULE}.underlying_from_bingx_symbol", return_value="AAPL"),
        patch(f"{MODULE}.classify_underlying", return_value="stock_perp"),
        patch(f"{MODULE}.build_candidate_context", new=AsyncMock(return_value=ctx)),
        patch(
            f"{MODULE}.analyze_bingx_l2", new=AsyncMock(return_value=_make_lob_mock(quality=0.9))
        ),
        patch(f"{MODULE}.EquityTASnapshotService") as MockTA,
        patch(f"{MODULE}.equity_probabilistic_summary", new=AsyncMock(return_value=pred_summary)),
    ):
        MockTA.return_value.snapshot = AsyncMock(return_value=ta_snapshot)
        result = await build_candidate_analysis("AAPL-USDT", bingx_client=MagicMock())

    assert result.market_type == "stock_perp"
    assert result.technical.status == "available"
    assert result.technical.quality_score == pytest.approx(1.0)
    assert result.predictive.status == "available"
    assert result.predictive.quality_score == pytest.approx(0.7)
    assert result.l2.status == "available"
    assert result.l2.quality_score == pytest.approx(0.9)
    assert result.readiness_score > 0.5
    assert "fmp" in result.data_sources
    assert "equity_heuristic" in result.data_sources


async def test_build_candidate_analysis_venue_unavailable_readiness_zero() -> None:
    ctx = _make_ctx_mock("BTC-USDT", "BTC", "crypto_standard", venue_available=False)

    with (
        patch(f"{MODULE}.underlying_from_bingx_symbol", return_value="BTC"),
        patch(f"{MODULE}.classify_underlying", return_value="crypto_standard"),
        patch(f"{MODULE}.build_candidate_context", new=AsyncMock(return_value=ctx)),
        patch(f"{MODULE}.analyze_bingx_l2", new=AsyncMock(return_value=_make_lob_mock(ok=False))),
        patch(f"{MODULE}.EquityTASnapshotService"),
        patch(
            f"{MODULE}.equity_probabilistic_summary",
            new=AsyncMock(return_value={"ok": False}),
        ),
    ):
        result = await build_candidate_analysis("BTC-USDT")

    assert result.venue.status == "unavailable"
    assert result.readiness_score == 0.0


async def test_build_candidate_analysis_l2_exception_degrades_gracefully() -> None:
    ctx = _make_ctx_mock("BTC-USDT", "BTC", "crypto_standard")

    with (
        patch(f"{MODULE}.underlying_from_bingx_symbol", return_value="BTC"),
        patch(f"{MODULE}.classify_underlying", return_value="crypto_standard"),
        patch(f"{MODULE}.build_candidate_context", new=AsyncMock(return_value=ctx)),
        patch(
            f"{MODULE}.analyze_bingx_l2",
            new=AsyncMock(side_effect=RuntimeError("connection_refused")),
        ),
        patch(f"{MODULE}.EquityTASnapshotService"),
        patch(
            f"{MODULE}.equity_probabilistic_summary",
            new=AsyncMock(return_value={"ok": False}),
        ),
    ):
        result = await build_candidate_analysis("BTC-USDT", bingx_client=MagicMock())

    assert result.l2.status == "unavailable"
    assert result.l2.reason == "l2_fetch_failed"
    # readiness still non-zero — venue is available, crypto not penalised for equity engines
    assert result.readiness_score > 0.0


async def test_build_candidate_analysis_technical_exception_degrades_gracefully() -> None:
    ctx = _make_ctx_mock("AAPL-USDT", "AAPL", "stock_perp")

    with (
        patch(f"{MODULE}.underlying_from_bingx_symbol", return_value="AAPL"),
        patch(f"{MODULE}.classify_underlying", return_value="stock_perp"),
        patch(f"{MODULE}.build_candidate_context", new=AsyncMock(return_value=ctx)),
        patch(f"{MODULE}.analyze_bingx_l2", new=AsyncMock(return_value=_make_lob_mock())),
        patch(f"{MODULE}.EquityTASnapshotService") as MockTA,
        patch(
            f"{MODULE}.equity_probabilistic_summary",
            new=AsyncMock(return_value={"ok": False, "reason": "unavailable"}),
        ),
    ):
        MockTA.return_value.snapshot = AsyncMock(side_effect=RuntimeError("fmp_timeout"))
        result = await build_candidate_analysis("AAPL-USDT")

    assert result.technical.status == "unavailable"
    assert result.technical.reason == "equity_ta_fetch_failed"


async def test_build_candidate_analysis_to_dict_json_safe_integration() -> None:
    ctx = _make_ctx_mock("BTC-USDT", "BTC", "crypto_standard")

    with (
        patch(f"{MODULE}.underlying_from_bingx_symbol", return_value="BTC"),
        patch(f"{MODULE}.classify_underlying", return_value="crypto_standard"),
        patch(f"{MODULE}.build_candidate_context", new=AsyncMock(return_value=ctx)),
        patch(f"{MODULE}.analyze_bingx_l2", new=AsyncMock(return_value=_make_lob_mock())),
        patch(f"{MODULE}.EquityTASnapshotService"),
        patch(
            f"{MODULE}.equity_probabilistic_summary",
            new=AsyncMock(return_value={"ok": False}),
        ),
    ):
        result = await build_candidate_analysis("BTC-USDT")

    d = result.to_dict()
    json.dumps(d)  # must not raise
    assert d["venue_symbol"] == "BTC-USDT"
    assert isinstance(d["data_sources"], list | tuple)
    assert isinstance(d["errors"], dict)
