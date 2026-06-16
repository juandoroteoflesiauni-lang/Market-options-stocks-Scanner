"""Fase 9 — enriquecimiento R1 completo (L2 + 5m + híbridos + predictivo). # [TH][IM]"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.domain.alpaca_options_models import (
    OptionsConfluence,
    Route1OptionsSnapshotContext,
)
from backend.models.market_snapshot import DataLineage, MarketSnapshot, OHLCVBar
from backend.models.options_strategy import (
    OptionsStrategyInput,
    R1EnrichmentContext,
    merge_all_layer_features,
)
from backend.services.options_strategy._bars import ohlcv_frame_from_input
from backend.services.options_strategy._scoring import (
    confluence_to_bias,
    l2_ofi_bias_from_microstructure,
)
from backend.services.options_strategy.fusion_router import fuse_features
from backend.services.options_strategy.options_layer import OptionsLayer
from backend.services.options_strategy.predictive_layer import PredictiveLayer
from backend.services.options_strategy.r1_enrichment_builder import build_r1_enrichment
from backend.services.options_strategy.technical_layer import TechnicalLayer


def _lineage() -> DataLineage:
    return DataLineage(source="test", ingestion_latency_ms=1, raw_field_count=3)


def _ohlcv_bars(n: int = 40) -> tuple[OHLCVBar, ...]:
    bars: list[OHLCVBar] = []
    for i in range(n):
        close = Decimal(str(180.0 + i * 0.35))
        bars.append(
            OHLCVBar(
                time=f"2026-06-13T14:{i:02d}:00Z",
                open=close - Decimal("0.10"),
                high=close + Decimal("0.30"),
                low=close - Decimal("0.30"),
                close=close,
                volume=Decimal("100000"),
            )
        )
    return tuple(bars)


def _intraday_5m(n: int = 35) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for i in range(n):
        close = 180.0 + i * 0.2
        rows.append(
            {
                "open": close - 0.1,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "volume": 50_000 + i * 100,
                "t": 1_700_000_000_000 + i * 300_000,
            }
        )
    return tuple(rows)


def _options_context(symbol: str = "AAPL") -> Route1OptionsSnapshotContext:
    return Route1OptionsSnapshotContext(
        symbol=symbol,
        as_of=datetime.now(tz=UTC).isoformat(),
        available=True,
        features={"shadow_delta_signal": 0.5},
        snapshot={"spot": 180.0, "chain": [], "iv_surface": {"atm_iv": 0.28}},
    )


def _bullish_confluence() -> OptionsConfluence:
    return OptionsConfluence(
        score=0.72,
        by_family={"momentum": 0.8, "volume": 0.7, "structure": 0.65},
        by_engine={"delta_rsi": 0.8},
        dominant_direction="BULL",
        moderate=False,
        reason_codes=("options_confluence_bull",),
    )


def _make_input(
    *,
    with_enrichment: bool = True,
    l2_ok: bool = True,
) -> OptionsStrategyInput:
    ohlcv = _ohlcv_bars(40)
    snap = MarketSnapshot(
        ticker="AAPL",
        exchange="NASDAQ",
        price=ohlcv[-1].close,
        volume=1_000_000,
        exchange_timestamp=datetime.now(tz=UTC),
        data_lineage=_lineage(),
        ohlcv=ohlcv,
    )
    enrichment = None
    if with_enrichment:
        l2_micro = {
            "ok": l2_ok,
            "l2_imbalance": 0.45,
            "vpin": 0.2,
            "order_book": {"bids": [[180.0, 500.0]], "asks": [[180.1, 200.0]]},
        }
        enrichment = R1EnrichmentContext(
            hybrid_confluence=_bullish_confluence(),
            hybrid_signal_count=8,
            l2_microstructure=l2_micro,
            l2_ok=l2_ok,
            intraday_bars_5m=_intraday_5m(35),
            predictive_meta={"directional_bias": 0.6, "confidence": 0.75},
            sources={"intraday_5m": "test", "l2": "test"},
        )
    return OptionsStrategyInput(
        symbol="AAPL",
        as_of=datetime.now(tz=UTC),
        market_snapshot=snap,
        options_context=_options_context(),
        r1_enrichment=enrichment,
    )


def test_r1_enrichment_context_frozen() -> None:
    ctx = R1EnrichmentContext(hybrid_signal_count=3)
    copy = ctx.model_copy(update={"hybrid_signal_count": 5})
    assert copy.hybrid_signal_count == 5
    assert ctx.hybrid_signal_count == 3


def test_ohlcv_frame_prefers_intraday_5m() -> None:
    inp = _make_input()
    frame = ohlcv_frame_from_input(inp, min_bars=30)
    assert frame is not None
    assert len(frame) == 35
    assert float(frame["close"].iloc[-1]) == pytest.approx(186.8, rel=1e-3)


def test_l2_ofi_bias_from_imbalance() -> None:
    bias = l2_ofi_bias_from_microstructure({"l2_imbalance": 0.5})
    assert bias == pytest.approx(0.5)


def test_confluence_to_bias_bullish() -> None:
    assert confluence_to_bias(_bullish_confluence()) == pytest.approx(0.72)


def test_technical_layer_uses_l2_enrichment() -> None:
    inp = _make_input(l2_ok=True)
    out = TechnicalLayer.run(inp)
    assert out.insufficient_data is False
    assert out.l2_microstructure_score > 0.0
    assert "l2_microstructure" in out.engine_scores


def test_options_layer_blends_hybrid_confluence() -> None:
    inp = _make_input()
    out = OptionsLayer.run(inp)
    assert out.hybrid_confluence_score == pytest.approx(0.72)
    assert "hybrid_confluence" in out.engine_scores
    assert out.options_direction_bias > 0.0


def test_predictive_layer_blends_bridge_meta() -> None:
    inp = _make_input()
    out = PredictiveLayer.run(inp)
    assert "predictive_bridge" in out.engine_scores
    assert out.predictive_direction_bias > 0.0


def test_merge_and_fuse_include_r1_scores() -> None:
    inp = _make_input()
    tech = TechnicalLayer.run(inp)
    pred = PredictiveLayer.run(inp)
    opt = OptionsLayer.run(inp)
    merged = merge_all_layer_features(tech, pred, opt)
    assert merged.hybrid_confluence_score == pytest.approx(0.72)
    assert merged.l2_microstructure_score > 0.0
    fused = fuse_features(merged)
    assert fused.global_confidence > 0.0


@patch(
    "backend.services.options_strategy.r1_enrichment_builder.fetch_intraday_bars",
    return_value={
        "bars": list(_intraday_5m(35)),
        "source": "test_intraday",
        "count": 35,
        "error": None,
    },
)
@patch(
    "backend.services.options_strategy.r1_enrichment_builder.fetch_route1_predictive_meta",
    new_callable=AsyncMock,
    return_value={"directional_bias": 0.4, "confidence": 0.6},
)
@patch(
    "backend.services.options_strategy.r1_enrichment_builder._fetch_l2_microstructure",
    new_callable=AsyncMock,
    return_value=({"ok": True, "l2_imbalance": 0.3}, True, "test_l2"),
)
def test_build_r1_enrichment_orchestrates_sources(
    _mock_l2: AsyncMock,
    _mock_pred: AsyncMock,
    _mock_bars: object,
) -> None:
    ctx = _options_context()
    enrichment = build_r1_enrichment("AAPL", options_ctx=ctx)
    assert enrichment.hybrid_signal_count >= 0
    assert len(enrichment.intraday_bars_5m) == 35
    assert enrichment.l2_ok is True
    assert enrichment.predictive_meta.get("directional_bias") == 0.4
    assert enrichment.sources.get("intraday_5m") == "test_intraday"
