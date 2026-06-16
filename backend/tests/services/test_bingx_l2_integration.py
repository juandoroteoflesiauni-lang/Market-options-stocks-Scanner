from __future__ import annotations
from typing import Any
"""Tests for the BingX L2 → Layer 3 LOB bridge."""



import pytest

from backend.layer_1_data.datos.bingx_l2_adapter import (
    L2_SOURCE_PERP_REST,
    L2_SOURCE_UNAVAILABLE,
    BingXL2Metrics,
    build_l2_snapshot_from_bingx_depth,
)
from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBSnapshot
from backend.services.bingx_l2_integration import (
    DATA_QUALITY_DEFAULT_DEPTH_TARGET,
    _compute_data_quality_score,
    adapter_result_to_lob_snapshot,
    analyze_bingx_l2,
)


class _StubOrderBookClient:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch_order_book_perp(
        self,
        symbol: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        self.calls.append((symbol, {"limit": limit}))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._payload or {}


def test_adapter_result_to_lob_snapshot_returns_valid_snapshot_when_ok() -> None:
    adapter_result = build_l2_snapshot_from_bingx_depth(
        "GOOGL-USDT",
        {"bids": [["100", "1.5"]], "asks": [["101", "0.5"]]},
        market_type="stock_perp",
        timestamp_ms=1_700_000_000_000,
    )

    snapshot = adapter_result_to_lob_snapshot(adapter_result)

    assert isinstance(snapshot, LOBSnapshot)
    assert snapshot.timestamp == 1_700_000_000_000
    assert [(lvl.price, lvl.quantity) for lvl in snapshot.bids] == [(100.0, 1.5)]
    assert [(lvl.price, lvl.quantity) for lvl in snapshot.asks] == [(101.0, 0.5)]


def test_adapter_result_to_lob_snapshot_returns_none_when_not_ok() -> None:
    adapter_result = build_l2_snapshot_from_bingx_depth(
        "GOOGL-USDT",
        {"bids": [], "asks": []},
        market_type="stock_perp",
    )

    assert adapter_result_to_lob_snapshot(adapter_result) is None


@pytest.mark.asyncio
async def test_analyze_bingx_l2_valid_book_yields_ok_analysis() -> None:
    """Valid mock order book → LOBDynamicsAnalysis with ok=True."""
    client = _StubOrderBookClient(
        payload={
            "bids": [["100", "1.0"], ["99.5", "2.0"]],
            "asks": [["100.5", "0.5"], ["101.0", "1.5"]],
        }
    )

    analysis = await analyze_bingx_l2(
        client,
        "GOOGL-USDT",
        market_type="stock_perp",
    )

    assert analysis.ok is True
    assert analysis.error is None
    assert analysis.source == L2_SOURCE_PERP_REST
    assert analysis.result is not None
    # Imbalance ρ = (bid_sum − ask_sum) / total = (3 − 2) / 5 = 0.2
    assert analysis.result.imbalance_rho == pytest.approx(0.2)
    assert client.calls == [("GOOGL-USDT", {"limit": 20})]


@pytest.mark.asyncio
async def test_analyze_bingx_l2_empty_book_yields_unavailable_analysis() -> None:
    """Empty book → LOBDynamicsAnalysis with ok=False and a descriptive reason."""
    client = _StubOrderBookClient(payload={"bids": [], "asks": []})

    analysis = await analyze_bingx_l2(
        client,
        "GOOGL-USDT",
        market_type="stock_perp",
    )

    assert analysis.ok is False
    assert analysis.result is None
    assert analysis.source == L2_SOURCE_PERP_REST
    assert analysis.error is not None
    assert "empty_book" in analysis.error


@pytest.mark.asyncio
async def test_analyze_bingx_l2_unsupported_type_is_blocked_pre_network() -> None:
    client = _StubOrderBookClient(payload={"bids": [["100", "1"]], "asks": [["101", "1"]]})

    analysis = await analyze_bingx_l2(
        client,
        "EURUSD",
        market_type="excluded",
    )

    assert analysis.ok is False
    assert analysis.source == L2_SOURCE_UNAVAILABLE
    assert analysis.error is not None
    assert "l2_unavailable" in analysis.error
    assert client.calls == []


@pytest.mark.asyncio
async def test_analyze_bingx_l2_network_error_surfaces_as_unavailable() -> None:
    client = _StubOrderBookClient(raise_exc=RuntimeError("upstream timeout"))

    analysis = await analyze_bingx_l2(
        client,
        "GOOGL-USDT",
        market_type="stock_perp",
    )

    assert analysis.ok is False
    assert analysis.source == L2_SOURCE_UNAVAILABLE
    assert analysis.error is not None
    assert "fetch_error" in analysis.error


@pytest.mark.asyncio
async def test_analyze_bingx_l2_uses_fetch_order_book_perp_for_googl() -> None:
    """Regression guard: GOOGL-USDT must route through fetch_order_book_perp."""
    client = _StubOrderBookClient(payload={"bids": [["100", "1"]], "asks": [["101", "1"]]})

    await analyze_bingx_l2(
        client,
        "GOOGL-USDT",
        market_type="stock_perp",
        limit=10,
    )

    assert client.calls == [("GOOGL-USDT", {"limit": 10})]


# ── _compute_data_quality_score ─────────────────────────────────────────────


def test_data_quality_score_perfect_book_scores_one() -> None:
    """Spread ≤ 0.05% of mid AND depth ≥ target → 1.0."""
    metrics = BingXL2Metrics(
        spread=0.0,
        bid_depth=DATA_QUALITY_DEFAULT_DEPTH_TARGET / 2.0,
        ask_depth=DATA_QUALITY_DEFAULT_DEPTH_TARGET / 2.0,
        imbalance=0.5,
    )
    score = _compute_data_quality_score(metrics, mid_price=100.0)
    assert score == pytest.approx(1.0)


def test_data_quality_score_zero_when_wide_spread_and_empty_book() -> None:
    """Spread ≥ 1% of mid AND zero depth → 0.0."""
    metrics = BingXL2Metrics(spread=2.0, bid_depth=0.0, ask_depth=0.0, imbalance=0.0)
    score = _compute_data_quality_score(metrics, mid_price=100.0)
    assert score == pytest.approx(0.0)


def test_data_quality_score_intermediate_values_are_strictly_between_zero_and_one() -> None:
    """Spread = 0.5% of mid, depth = half target → spread comp 0.5, depth comp 0.5 → 0.5."""
    metrics = BingXL2Metrics(
        spread=0.5,
        bid_depth=250.0,
        ask_depth=250.0,
        imbalance=0.5,
    )
    score = _compute_data_quality_score(metrics, mid_price=100.0)
    # Linear: spread_pct=0.5; spread_comp = 1 - (0.5-0.05)/(1.0-0.05) = 1 - 0.4737 = 0.5263
    # depth_comp = 500/1000 = 0.5; mean = (0.5263 + 0.5)/2 = 0.5132
    assert 0.0 < score < 1.0
    assert score == pytest.approx(0.5132, abs=1e-3)


def test_data_quality_score_falls_below_threshold_for_wide_spread_thin_book() -> None:
    """Wide spread + thin book → score below the funding-gate trigger (0.4)."""
    metrics = BingXL2Metrics(
        spread=0.8,
        bid_depth=50.0,
        ask_depth=50.0,
        imbalance=0.5,
    )
    score = _compute_data_quality_score(metrics, mid_price=100.0)
    # spread_pct = 0.8; spread_comp ≈ 1 - (0.75/0.95) ≈ 0.2105; depth_comp = 0.1
    assert score < 0.4


def test_data_quality_score_clamps_to_unit_interval_for_overshoot_depth() -> None:
    """Depth far above target must not push the component past 1.0."""
    metrics = BingXL2Metrics(
        spread=0.0,
        bid_depth=10_000.0,
        ask_depth=10_000.0,
        imbalance=0.5,
    )
    score = _compute_data_quality_score(metrics, mid_price=100.0)
    assert score == pytest.approx(1.0)


def test_data_quality_score_without_mid_uses_absolute_spread_as_percent() -> None:
    """When mid_price is None, the absolute spread is treated as a percent."""
    metrics = BingXL2Metrics(spread=0.5, bid_depth=500.0, ask_depth=500.0, imbalance=0.5)
    score_with_mid = _compute_data_quality_score(metrics, mid_price=100.0)
    score_without_mid = _compute_data_quality_score(metrics, mid_price=None)
    # Both fall in (0, 1) but without mid the spread component is the same since
    # ``0.5`` is already in the [best=0.05, worst=1.0] window.
    assert score_with_mid == score_without_mid


@pytest.mark.asyncio
async def test_analyze_bingx_l2_attaches_data_quality_score_on_ok_result() -> None:
    """When the bridge produces an ok analysis it must carry the data_quality_score."""
    client = _StubOrderBookClient(
        payload={
            "bids": [["100", "500"], ["99.5", "500"]],
            "asks": [["100.1", "500"], ["100.5", "500"]],
        }
    )

    analysis = await analyze_bingx_l2(
        client,
        "AAPL-USDT",
        market_type="stock_perp",
    )

    assert analysis.ok is True
    assert analysis.data_quality_score is not None
    assert 0.0 <= analysis.data_quality_score <= 1.0


@pytest.mark.asyncio
async def test_analyze_bingx_l2_unavailable_result_has_no_data_quality_score() -> None:
    """When the bridge fails the score must be ``None`` — never silently faked."""
    client = _StubOrderBookClient(payload={"bids": [], "asks": []})

    analysis = await analyze_bingx_l2(
        client,
        "AAPL-USDT",
        market_type="stock_perp",
    )

    assert analysis.ok is False
    assert analysis.data_quality_score is None
