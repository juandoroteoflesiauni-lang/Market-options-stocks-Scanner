"""Tests for ``bingx_predictive_bridge`` — predictive cascade for BingX.

Coverage:
- Pure routing (``resolve_predictive_target``) for every market type
- Heuristic normalisation (equity)
- Direct local model execution (equity heuristic, crypto predictive)
- Index proxy substitution (SPX→SPY, NDX→QQQ, RUT→IWM)
- Crypto branch — wired and unwired
- Direction-label normalisation (BULLISH/UP/LONG → LONG; BEARISH/DOWN/SHORT → SHORT)
- JSON safety end-to-end
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.services.bingx_predictive_bridge import (
    REASON_ALL_SOURCES_FAILED,
    REASON_CRYPTO_NOT_WIRED,
    REASON_NO_PROXY_FOR_INDEX,
    SOURCE_CRYPTO,
    SOURCE_EQUITY_HEURISTIC,
    BingXPredictiveBridgeResult,
    BingXPredictiveSignal,
    _normalise_direction_label,
    build_predictive_bridge,
    resolve_predictive_target,
)

# ── Fixture builders ─────────────────────────────────────────────────────────


def _equity_summary_response(
    *,
    ok: bool = True,
    bull: float = 0.6,
    bear: float = 0.3,
    confidence: float = 0.65,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "ticker": "GOOGL",
        "bull_probability": bull,
        "bear_probability": bear,
        "neutral_probability": 1.0 - bull - bear,
        "confidence": confidence,
        "source": "equity_heuristic",
    }


# ── Direction label normalisation ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("LONG", "LONG"),
        ("BULLISH", "LONG"),
        ("UP", "LONG"),
        ("STRONG_BUY", "LONG"),
        ("SHORT", "SHORT"),
        ("BEARISH", "SHORT"),
        ("DOWN", "SHORT"),
        ("NEUTRAL", "NEUTRAL"),
        ("anything-else", "NEUTRAL"),
        (None, "NEUTRAL"),
    ],
)
def test_normalise_direction_label_maps_to_canonical(raw: object, expected: str) -> None:
    assert _normalise_direction_label(raw) == expected


# ── resolve_predictive_target ────────────────────────────────────────────────


def test_resolve_stock_perp_returns_underlying() -> None:
    options_symbol, proxy, reason = resolve_predictive_target("GOOGL-USDT", "stock_perp")
    assert options_symbol == "GOOGL"
    assert proxy is None
    assert reason is None


@pytest.mark.parametrize(
    ("index", "proxy"),
    [("SPX", "SPY"), ("NDX", "QQQ"), ("RUT", "IWM"), ("US100", "QQQ"), ("US500", "SPY")],
)
def test_resolve_stock_index_perp_maps_to_proxy(index: str, proxy: str) -> None:
    options_symbol, resolved_proxy, reason = resolve_predictive_target(
        f"{index}-USDT", "stock_index_perp"
    )
    assert options_symbol == proxy
    assert resolved_proxy == proxy
    assert reason is None


def test_resolve_unknown_index_returns_no_proxy_reason() -> None:
    options_symbol, proxy, reason = resolve_predictive_target("EXOTIC-USDT", "stock_index_perp")
    assert options_symbol is None
    assert proxy is None
    assert reason == REASON_NO_PROXY_FOR_INDEX


def test_resolve_crypto_returns_crypto_reason() -> None:
    options_symbol, proxy, reason = resolve_predictive_target("BTC-USDT", "crypto_standard")
    assert options_symbol is None
    assert proxy is None
    assert reason == REASON_CRYPTO_NOT_WIRED


# ── Local equity heuristic execution ──────────────────────────────────────────


async def test_equity_heuristic_is_used_when_provided() -> None:
    equity_fn = AsyncMock(return_value=_equity_summary_response(bull=0.7, bear=0.2))

    result = await build_predictive_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        equity_summary_fn=equity_fn,
    )

    equity_fn.assert_awaited_once_with("GOOGL")
    assert result.status == "available"
    assert result.signal is not None
    assert result.signal.source == SOURCE_EQUITY_HEURISTIC
    assert result.signal.directional_bias == "LONG"
    assert result.signal.probability_long == 0.7
    assert result.signal.probability_short == 0.2
    assert result.signal.confidence == 0.65
    assert result.signal.quality_score == 0.65


async def test_all_sources_failed_returns_unavailable_when_no_fetchers() -> None:
    result = await build_predictive_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
    )
    assert result.status == "unavailable"
    assert result.reason is not None
    assert result.reason.startswith(REASON_ALL_SOURCES_FAILED)


# ── Index proxy substitution end-to-end ──────────────────────────────────────


async def test_stock_index_perp_routes_equity_heuristic_through_proxy() -> None:
    equity_fn = AsyncMock(return_value=_equity_summary_response())
    result = await build_predictive_bridge(
        "SPX-USDT",
        market_type="stock_index_perp",
        equity_summary_fn=equity_fn,
    )
    equity_fn.assert_awaited_once_with("SPY")  # SPX → SPY proxy
    assert result.status == "available"
    assert result.proxy_symbol == "SPY"
    assert result.options_symbol == "SPY"
    assert result.underlying_symbol == "SPX"


# ── Crypto branch ────────────────────────────────────────────────────────────


async def test_crypto_no_fetcher_returns_crypto_not_wired() -> None:
    result = await build_predictive_bridge(
        "BTC-USDT",
        market_type="crypto_standard",
    )
    assert result.status == "unavailable"
    assert result.reason == REASON_CRYPTO_NOT_WIRED


async def test_crypto_with_fetcher_returns_normalised_signal() -> None:
    crypto_fn = AsyncMock(
        return_value={
            "ok": True,
            "directional_bias": "SHORT",
            "probability_long": 0.3,
            "probability_short": 0.55,
            "confidence": 0.6,
            "horizon": "intraday",
            "quality_score": 0.5,
            "reason_codes": ["btc_momentum_engine"],
        }
    )
    result = await build_predictive_bridge(
        "BTC-USDT",
        market_type="crypto_standard",
        crypto_predictive_fn=crypto_fn,
    )
    crypto_fn.assert_awaited_once_with("BTC")
    assert result.status == "available"
    assert result.signal is not None
    assert result.signal.source == SOURCE_CRYPTO
    assert result.signal.directional_bias == "SHORT"
    assert result.signal.probability_short == 0.55


async def test_crypto_fetcher_not_ok_falls_through_to_unavailable() -> None:
    crypto_fn = AsyncMock(return_value={"ok": False, "reason": "no_btc_predictor"})
    result = await build_predictive_bridge(
        "BTC-USDT",
        market_type="crypto_standard",
        crypto_predictive_fn=crypto_fn,
    )
    assert result.status == "unavailable"
    assert result.reason == REASON_CRYPTO_NOT_WIRED


# ── Per-source edge cases ────────────────────────────────────────────────────


async def test_equity_summary_chooses_short_when_bear_dominates() -> None:
    equity_fn = AsyncMock(return_value=_equity_summary_response(bull=0.2, bear=0.7, confidence=0.6))
    result = await build_predictive_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        equity_summary_fn=equity_fn,
    )
    assert result.signal is not None
    assert result.signal.directional_bias == "SHORT"
    assert result.signal.probability_short == 0.7


async def test_equity_summary_neutral_when_probs_close() -> None:
    equity_fn = AsyncMock(
        return_value=_equity_summary_response(bull=0.48, bear=0.49, confidence=0.5)
    )
    result = await build_predictive_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        equity_summary_fn=equity_fn,
    )
    assert result.signal is not None
    assert result.signal.directional_bias == "NEUTRAL"


# ── JSON safety ──────────────────────────────────────────────────────────────


async def test_result_to_dict_is_json_safe_full_signal() -> None:
    equity_fn = AsyncMock(return_value=_equity_summary_response())
    result = await build_predictive_bridge(
        "GOOGL-USDT",
        market_type="stock_perp",
        equity_summary_fn=equity_fn,
    )
    payload = result.to_dict()
    serialised = json.dumps(payload)
    parsed = json.loads(serialised)
    assert parsed["status"] == "available"
    assert parsed["signal"]["source"] == SOURCE_EQUITY_HEURISTIC
    assert parsed["signal"]["directional_bias"] == "LONG"
    assert isinstance(parsed["signal"]["reason_codes"], list)


async def test_result_to_dict_is_json_safe_unavailable() -> None:
    result = await build_predictive_bridge(
        "BTC-USDT",
        market_type="crypto_standard",
    )
    payload = result.to_dict()
    json.dumps(payload)
    assert payload["signal"] is None
    assert payload["reason"] == REASON_CRYPTO_NOT_WIRED


# ── Result construction smoke ────────────────────────────────────────────────


def test_signal_default_reason_codes_is_independent_per_instance() -> None:
    """Guards against the classic mutable-default trap."""
    a = BingXPredictiveSignal(
        directional_bias="LONG",
        probability_long=0.6,
        probability_short=0.3,
        confidence=0.65,
        horizon="swing",
        source=SOURCE_EQUITY_HEURISTIC,
        quality_score=0.7,
    )
    b = BingXPredictiveSignal(
        directional_bias="SHORT",
        probability_long=0.3,
        probability_short=0.6,
        confidence=0.55,
        horizon="swing",
        source=SOURCE_EQUITY_HEURISTIC,
        quality_score=0.4,
    )
    assert a.reason_codes == []
    assert b.reason_codes == []
    assert a.reason_codes is not b.reason_codes


def test_result_default_payload_can_be_none() -> None:
    r = BingXPredictiveBridgeResult(
        status="unavailable",
        symbol="BTC-USDT",
        underlying_symbol="BTC",
        market_type="crypto_standard",
        proxy_symbol=None,
        options_symbol="BTC",
        signal=None,
        payload=None,
        reason="x",
    )
    assert r.payload is None
