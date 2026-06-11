"""Tests for ``bingx_options_bridge`` — BingX ↔ institutional options metrics.

Coverage:
- Pure routing (``resolve_options_symbol``) for every market type
- Integration (``build_options_bridge``) with injected fetcher
- Index proxy substitution for SPX/NDX/US100/US500/IWM/RUT/DJI
- Crypto and excluded handling
- Quality score behaviour
- JSON safety via ``to_dict``
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.services.bingx_options_bridge import (
    INDEX_OPTIONS_PROXIES,
    REASON_MARKET_TYPE_EXCLUDED,
    REASON_NO_FETCHER,
    REASON_NO_OPTIONS_FOR_CRYPTO,
    REASON_NO_PROXY_FOR_INDEX,
    REASON_SNAPSHOT_FETCH_FAILED,
    REASON_SNAPSHOT_NOT_OK,
    BingXOptionsBridgeResult,
    BingXOptionsMetrics,
    _compute_quality_score,
    build_options_bridge,
    resolve_options_symbol,
)

# ── Fixture builders ─────────────────────────────────────────────────────────


def _gex(
    *,
    call_wall: float | None = 185.0,
    put_wall: float | None = 175.0,
    zero_gamma: float | None = 180.0,
    max_pain: float | None = 180.5,
    net_gex_total: float = 1_200_000.0,
    call_gex_total: float = 800_000.0,
    put_gex_total: float = -400_000.0,
    dealer_bias: str = "BULLISH",
    squeeze_probability: float = 0.15,
) -> SimpleNamespace:
    return SimpleNamespace(
        call_wall=call_wall,
        put_wall=put_wall,
        call_wall_moderate=186.0,
        put_wall_moderate=174.0,
        zero_gamma_level=zero_gamma,
        max_pain=max_pain,
        net_gex_total=net_gex_total,
        call_gex_total=call_gex_total,
        put_gex_total=put_gex_total,
        dealer_bias=dealer_bias,
        squeeze_probability=squeeze_probability,
    )


def _iv(
    *,
    atm_iv: float | None = 0.28,
    iv_rank_hv_rolling: float | None = 0.45,
    iv_rank_cross_expiry: float | None = 0.55,
    iv_percentile_cross_term: float | None = 0.60,
    vrp: float | None = 0.04,
) -> SimpleNamespace:
    return SimpleNamespace(
        atm_iv=atm_iv,
        iv_rank_hv_rolling=iv_rank_hv_rolling,
        iv_rank_cross_expiry=iv_rank_cross_expiry,
        iv_percentile_cross_term=iv_percentile_cross_term,
        vrp=vrp,
    )


def _confluence(
    *,
    score: float | None = 0.72,
    signal: str | None = "BULLISH",
    confidence: float | None = 0.65,
    pcr_oi: float | None = 0.82,
    pcr_volume: float | None = 0.91,
    total_vanna: float | None = 250_000.0,
    total_vex: float | None = 180_000.0,
    total_cex: float | None = 120_000.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        score=score,
        signal=signal,
        confidence=confidence,
        pcr_oi=pcr_oi,
        pcr_volume=pcr_volume,
        total_vanna_exposure=total_vanna,
        total_vex=total_vex,
        total_cex=total_cex,
        vanna_exposure_regime="BULLISH",
        vex_regime="NEUTRAL",
        cex_regime="BEARISH",
    )


def _chain_rows(n: int = 12) -> list[dict[str, Any]]:
    return [
        {
            "strike": 175.0 + i,
            "call_oi": 500 + i * 10,
            "put_oi": 400 + i * 8,
            "call_volume": 100 + i,
            "put_volume": 80 + i,
            "net_dex": 50_000.0 + i * 1_000.0,
        }
        for i in range(n)
    ]


def _snapshot_ok(
    ticker: str = "GOOGL",
    *,
    spot: float = 180.0,
    chain: list[dict[str, Any]] | None = None,
    gex: SimpleNamespace | None = None,
    iv: SimpleNamespace | None = None,
    confluence: SimpleNamespace | None = None,
    total_dex: float = 500_000.0,
    dex_flip_level: float | None = 179.5,
    chain_quality: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        ticker=ticker,
        spot=spot,
        ok=True,
        error=None,
        chain=chain if chain is not None else _chain_rows(),
        gex_levels=gex if gex is not None else _gex(),
        iv_surface=iv if iv is not None else _iv(),
        confluence=confluence if confluence is not None else _confluence(),
        total_dex=total_dex,
        dex_flip_level=dex_flip_level,
        chain_quality=chain_quality or {"provider": "massive", "strikes": 12},
    )


def _snapshot_failed(error: str = "Option chain unavailable") -> SimpleNamespace:
    return SimpleNamespace(
        ticker="GOOGL",
        spot=0.0,
        ok=False,
        error=error,
        chain=[],
        gex_levels=_gex(call_wall=None, put_wall=None, zero_gamma=None, max_pain=None),
        iv_surface=_iv(atm_iv=None, iv_rank_hv_rolling=None),
        confluence=_confluence(score=None, signal=None, confidence=None),
        total_dex=0.0,
        dex_flip_level=None,
        chain_quality={"provider": "none"},
    )


# ── resolve_options_symbol ───────────────────────────────────────────────────


def test_resolve_stock_perp_returns_underlying() -> None:
    options_symbol, proxy, reason = resolve_options_symbol("GOOGL-USDT", "stock_perp")
    assert options_symbol == "GOOGL"
    assert proxy is None
    assert reason is None


def test_resolve_msfton_quirk_strips_on_suffix() -> None:
    """BingX appends ``ON`` to some tickers in raw streams — the bridge must
    strip it before issuing the options query."""
    options_symbol, proxy, reason = resolve_options_symbol("MSFTON-USDT", "stock_perp")
    assert options_symbol == "MSFT"
    assert proxy is None
    assert reason is None


@pytest.mark.parametrize(
    ("index_root", "expected_proxy"),
    [
        ("SPX", "SPY"),
        ("US500", "SPY"),
        ("NDX", "QQQ"),
        ("US100", "QQQ"),
        ("RUT", "IWM"),
        ("IWM", "IWM"),  # passthrough — ETF already optionable
        ("DJI", "DIA"),
        ("DJX", "DIA"),
    ],
)
def test_resolve_index_perp_maps_to_known_proxy(index_root: str, expected_proxy: str) -> None:
    options_symbol, proxy, reason = resolve_options_symbol(f"{index_root}-USDT", "stock_index_perp")
    assert options_symbol == expected_proxy
    assert proxy == expected_proxy
    assert reason is None


def test_resolve_index_perp_unknown_returns_reason() -> None:
    options_symbol, proxy, reason = resolve_options_symbol("EXOTIC-USDT", "stock_index_perp")
    assert options_symbol is None
    assert proxy is None
    assert reason == REASON_NO_PROXY_FOR_INDEX


def test_resolve_crypto_returns_unavailable_reason() -> None:
    options_symbol, proxy, reason = resolve_options_symbol("BTC-USDT", "crypto_standard")
    assert options_symbol is None
    assert proxy is None
    assert reason == REASON_NO_OPTIONS_FOR_CRYPTO


def test_index_proxy_map_contains_all_documented_pairs() -> None:
    # Stability check: the runbook + task spec promise these pairs are wired.
    # If anyone narrows the map, this test forces an explicit acknowledgement.
    required = {"SPX", "US500", "NDX", "US100", "IWM"}
    assert required.issubset(INDEX_OPTIONS_PROXIES.keys())


# ── build_options_bridge: success path ───────────────────────────────────────


async def test_build_bridge_stock_perp_extracts_full_metric_set() -> None:
    fetcher = AsyncMock(return_value=_snapshot_ok("GOOGL"))
    result = await build_options_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        options_snapshot_fn=fetcher,
    )
    fetcher.assert_awaited_once_with("GOOGL", None, 0.04)

    assert result.status == "available"
    assert result.source == "underlying_options"
    assert result.market_type == "stock_perp"
    assert result.underlying_symbol == "GOOGL"
    assert result.proxy_symbol is None
    assert result.options_symbol == "GOOGL"
    assert result.reason is None

    metrics = result.metrics
    assert metrics is not None
    assert metrics.spot == 180.0
    assert metrics.call_wall == 185.0
    assert metrics.put_wall == 175.0
    assert metrics.zero_gamma == 180.0
    assert metrics.max_pain == 180.5
    assert metrics.net_gex_total == 1_200_000.0
    assert metrics.dealer_bias == "BULLISH"
    assert metrics.atm_iv == 0.28
    assert metrics.iv_rank_hv_rolling == 0.45
    assert metrics.iv_percentile_cross_term == 0.60
    assert metrics.vrp == 0.04
    assert metrics.pcr_oi == 0.82
    assert metrics.pcr_volume == 0.91
    assert metrics.total_dex == 500_000.0
    assert metrics.dex_flip_level == 179.5
    assert metrics.total_vanna == 250_000.0
    assert metrics.total_vex == 180_000.0
    assert metrics.total_cex == 120_000.0
    assert metrics.confluence_signal == "BULLISH"
    assert metrics.confluence_score == 0.72
    assert metrics.chain_contracts == 12
    assert metrics.wall_direction == "above"  # zero_gamma=180 ≥ spot=180
    assert metrics.wall_distance_pct is not None

    assert result.quality_score == 1.0  # all four quadrants populated
    assert result.chain_quality == {"provider": "massive", "strikes": 12}


async def test_build_bridge_index_perp_uses_proxy_and_tags_source() -> None:
    fetcher = AsyncMock(return_value=_snapshot_ok("SPY"))
    result = await build_options_bridge(
        "SPX-USDT",
        market_type="stock_index_perp",
        options_snapshot_fn=fetcher,
    )
    fetcher.assert_awaited_once_with("SPY", None, 0.04)

    assert result.status == "available"
    assert result.source == "index_proxy_options"
    assert result.market_type == "stock_index_perp"
    assert result.underlying_symbol == "SPX"
    assert result.proxy_symbol == "SPY"
    assert result.options_symbol == "SPY"


async def test_build_bridge_index_perp_unknown_index_is_unavailable() -> None:
    fetcher = AsyncMock()
    result = await build_options_bridge(
        "EXOTIC-USDT",
        market_type="stock_index_perp",
        options_snapshot_fn=fetcher,
    )
    fetcher.assert_not_awaited()
    assert result.status == "unavailable"
    assert result.reason == REASON_NO_PROXY_FOR_INDEX
    assert result.source == "none"
    assert result.proxy_symbol is None


# ── build_options_bridge: crypto + excluded ──────────────────────────────────


async def test_build_bridge_crypto_skips_fetch() -> None:
    fetcher = AsyncMock()
    result = await build_options_bridge(
        "BTC-USDT",
        market_type="crypto_standard",
        options_snapshot_fn=fetcher,
    )
    fetcher.assert_not_awaited()
    assert result.status == "unavailable"
    assert result.reason == REASON_NO_OPTIONS_FOR_CRYPTO
    assert result.source == "none"
    assert result.market_type == "crypto_standard"


# ── build_options_bridge: error handling ─────────────────────────────────────


async def test_build_bridge_fetcher_raises_returns_failed_status() -> None:
    fetcher = AsyncMock(side_effect=RuntimeError("provider 502"))
    result = await build_options_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        options_snapshot_fn=fetcher,
    )
    assert result.status == "unavailable"
    assert result.reason == REASON_SNAPSHOT_FETCH_FAILED
    assert result.source == "underlying_options"


async def test_build_bridge_snapshot_ok_false_carries_error_reason() -> None:
    fetcher = AsyncMock(return_value=_snapshot_failed("FINNHUB unauthorized"))
    result = await build_options_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        options_snapshot_fn=fetcher,
    )
    assert result.status == "unavailable"
    assert "FINNHUB" in (result.reason or "")
    assert result.chain_quality == {"provider": "none"}


async def test_build_bridge_snapshot_ok_false_without_error_uses_stable_code() -> None:
    snapshot = _snapshot_failed("")
    fetcher = AsyncMock(return_value=snapshot)
    result = await build_options_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        options_snapshot_fn=fetcher,
    )
    assert result.status == "unavailable"
    assert result.reason == REASON_SNAPSHOT_NOT_OK


async def test_build_bridge_no_fetcher_returns_no_fetcher_reason() -> None:
    result = await build_options_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        options_snapshot_fn=None,
    )
    assert result.status == "unavailable"
    assert result.reason == REASON_NO_FETCHER


async def test_build_bridge_resolves_market_type_when_not_passed() -> None:
    """If the caller omits ``market_type``, the bridge classifies the symbol itself."""
    fetcher = AsyncMock()
    result = await build_options_bridge(
        "BTC-USDT",
        options_snapshot_fn=fetcher,
    )
    fetcher.assert_not_awaited()
    assert result.market_type == "excluded"
    assert result.reason == REASON_MARKET_TYPE_EXCLUDED


# ── Quality scoring ──────────────────────────────────────────────────────────


def _bare_metrics(**overrides: Any) -> BingXOptionsMetrics:
    base: dict[str, Any] = {
        "spot": 100.0,
        "call_wall": None,
        "put_wall": None,
        "call_wall_moderate": None,
        "put_wall_moderate": None,
        "zero_gamma": None,
        "max_pain": None,
        "net_gex_total": 0.0,
        "call_gex_total": 0.0,
        "put_gex_total": 0.0,
        "dealer_bias": "NEUTRAL",
        "squeeze_probability": 0.0,
        "total_dex": 0.0,
        "dex_flip_level": None,
        "total_vanna": None,
        "total_vex": None,
        "total_cex": None,
        "vanna_exposure_regime": "NEUTRAL",
        "vex_regime": "NEUTRAL",
        "cex_regime": "NEUTRAL",
        "atm_iv": None,
        "iv_rank_hv_rolling": None,
        "iv_rank_cross_expiry": None,
        "iv_percentile_cross_term": None,
        "vrp": None,
        "pcr_oi": None,
        "pcr_volume": None,
        "wall_distance_pct": None,
        "wall_direction": None,
        "confluence_score": None,
        "confluence_signal": None,
        "confluence_confidence": None,
        "chain_contracts": 0,
    }
    base.update(overrides)
    return BingXOptionsMetrics(**base)


def test_quality_zero_when_all_quadrants_empty() -> None:
    assert _compute_quality_score(_bare_metrics()) == 0.0


def test_quality_partial_when_chain_only() -> None:
    # chain >= 10 → 0.4
    assert _compute_quality_score(_bare_metrics(chain_contracts=15)) == 0.4


def test_quality_full_when_all_four_quadrants_populated() -> None:
    metrics = _bare_metrics(
        chain_contracts=20,
        call_wall=185.0,
        put_wall=175.0,
        atm_iv=0.28,
        net_gex_total=1_000_000.0,
    )
    assert _compute_quality_score(metrics) == 1.0


def test_quality_partial_walls_get_half_credit() -> None:
    metrics = _bare_metrics(chain_contracts=15, call_wall=185.0)
    # 0.4 (chain) + 0.1 (single wall) = 0.5
    assert _compute_quality_score(metrics) == 0.5


# ── JSON safety ──────────────────────────────────────────────────────────────


async def test_to_dict_is_json_safe() -> None:
    fetcher = AsyncMock(return_value=_snapshot_ok("GOOGL"))
    result = await build_options_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        options_snapshot_fn=fetcher,
    )
    payload = result.to_dict()
    serialised = json.dumps(payload)
    parsed = json.loads(serialised)
    assert parsed["status"] == "available"
    assert parsed["underlying_symbol"] == "GOOGL"
    assert parsed["metrics"]["call_wall"] == 185.0
    assert parsed["proxy_symbol"] is None
    assert isinstance(parsed["chain_quality"], dict)


async def test_to_dict_is_json_safe_for_unavailable_crypto() -> None:
    result = await build_options_bridge(
        "BTC-USDT",
        market_type="crypto_standard",
        options_snapshot_fn=AsyncMock(),
    )
    payload = result.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["metrics"] is None
    assert payload["reason"] == REASON_NO_OPTIONS_FOR_CRYPTO


# ── Result construction smoke test ───────────────────────────────────────────


def test_bridge_result_default_chain_quality_is_empty_dict() -> None:
    """``field(default_factory=dict)`` guarantees each instance gets its own
    dict — guards against the classic mutable-default trap."""
    a = BingXOptionsBridgeResult(
        status="unavailable",
        source="none",
        market_type="crypto_standard",
        underlying_symbol="BTC",
        proxy_symbol=None,
        options_symbol="BTC",
        metrics=None,
    )
    b = BingXOptionsBridgeResult(
        status="unavailable",
        source="none",
        market_type="crypto_standard",
        underlying_symbol="ETH",
        proxy_symbol=None,
        options_symbol="ETH",
        metrics=None,
    )
    assert a.chain_quality == {}
    assert b.chain_quality == {}
    assert a.chain_quality is not b.chain_quality
