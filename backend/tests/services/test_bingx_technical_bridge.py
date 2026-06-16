from __future__ import annotations
from typing import Any
"""Tests for ``bingx_technical_bridge`` — full SMC/VSA/FVG/VP pipeline binding.

Coverage:
- ``klines_to_candles`` pure conversion (BingXKline / dict / mixed objects)
- ``inject_l2_into_payload`` for Pydantic models, dicts, and missing snapshots
- ``compute_technical_quality_score`` — empty, partial, and full payloads
- Summary extraction (SMC / VSA / FVG / volume profile / trend fallback)
- ``build_venue_technical`` integration: insufficient bars, no fetcher,
  fetcher exception, ok=False payload, ok=True with L2 injection
- ``build_underlying_technical`` integration: full mode, lite mode, crypto
  guard, full preferred over lite when both provided, equity snapshot ok=False
- JSON safety end-to-end via ``to_dict``
"""


import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.services.bingx_technical_bridge import (
    MIN_BARS_FOR_SMC,
    REASON_EQUITY_SNAPSHOT_NOT_OK,
    REASON_INSUFFICIENT_BARS,
    REASON_NO_EQUITY_FOR_CRYPTO,
    REASON_NO_TECHNICAL_FETCHER,
    REASON_NO_VENUE_BARS,
    REASON_TECHNICAL_FETCH_FAILED,
    SOURCE_EQUITY_SNAPSHOT,
    SOURCE_UNDERLYING_FULL,
    SOURCE_VENUE,
    build_underlying_technical,
    build_venue_technical,
    compute_technical_quality_score,
    inject_l2_into_payload,
    klines_to_candles,
)

# ── Fixture builders ─────────────────────────────────────────────────────────


def _kline_obj(i: int) -> SimpleNamespace:
    base = 100.0 + i
    return SimpleNamespace(
        open_time_ms=1_700_000_000_000 + i * 300_000,
        close_time_ms=1_700_000_000_000 + i * 300_000 + 299_999,
        open=base,
        high=base + 1.0,
        low=base - 1.0,
        close=base + 0.5,
        volume=1000.0 + i * 10,
    )


def _kline_dict(i: int) -> dict[str, Any]:
    base = 100.0 + i
    return {
        "open_time_ms": 1_700_000_000_000 + i * 300_000,
        "open": base,
        "high": base + 1.0,
        "low": base - 1.0,
        "close": base + 0.5,
        "volume": 1000.0 + i * 10,
    }


def _full_payload(*, bars: int = 120) -> dict[str, Any]:
    """A realistic ``ok=True`` payload covering every engine the summary reads."""
    return {
        "ok": True,
        "symbol": "BTC-USDT",
        "timeframe": "5m",
        "as_of": "2026-05-20T00:00:00",
        "candles": [],
        "overlays": {},
        "structure_markers": [],
        "smc": {
            "sesgo": "BULLISH",
            "composite_score": 0.72,
            "structure_events": [],
        },
        "fractal": {},
        "vsa": {
            "enabled": True,
            "ok": True,
            "error": None,
            "signal": "STRONG_BUY",
            "composite_score": 0.65,
        },
        "fvg": {
            "enabled": True,
            "ok": True,
            "error": None,
            "active_count": 5,
            "history_count": 12,
            "bullish_active_count": 3,
            "bearish_active_count": 2,
        },
        "volume_profile": {
            "enabled": True,
            "ok": True,
            "error": None,
            "poc": 100.5,
            "vah": 102.0,
            "val": 99.0,
            "volume_bias": "bullish",
        },
        "order_flow_delta": {
            "enabled": True,
            "ok": True,
            "error": None,
        },
        "market_structure": {
            "ok": True,
            "bias": "BULLISH",
        },
        "lob_dynamics": {
            "enabled": True,
            "ok": False,
            "error": "L2 order-book feed not configured",
        },
        "engine_status": {
            "smc": {"enabled": True, "ok": True, "error": None},
            "lob_dynamics": {"enabled": True, "ok": False, "error": "..."},
        },
        "meta": {
            "bars": bars,
            "composite_score": 0.72,
            "sesgo_smc": "BULLISH",
        },
    }


# ── klines_to_candles ────────────────────────────────────────────────────────


def test_klines_to_candles_empty_input() -> None:
    assert klines_to_candles([]) == []
    assert klines_to_candles(None) == []


def test_klines_to_candles_accepts_dataclass_like_objects() -> None:
    klines = [_kline_obj(i) for i in range(3)]
    candles = klines_to_candles(klines)
    assert len(candles) == 3
    assert candles[0]["time"] == 1_700_000_000_000
    assert candles[0]["open"] == 100.0
    assert candles[0]["volume"] == 1000.0
    assert candles[2]["close"] == 102.5


def test_klines_to_candles_accepts_dicts() -> None:
    candles = klines_to_candles([_kline_dict(i) for i in range(4)])
    assert len(candles) == 4
    assert candles[3]["high"] == 104.0


def test_klines_to_candles_drops_invalid_rows() -> None:
    klines = [
        _kline_obj(0),
        SimpleNamespace(open_time_ms=0, open=1, high=1, low=1, close=1, volume=1),  # bad ts
        SimpleNamespace(
            open_time_ms=1_700_000_001_000, open=0, high=1, low=1, close=1, volume=1
        ),  # bad open
        _kline_obj(2),
    ]
    candles = klines_to_candles(klines)
    assert len(candles) == 2  # the two valid ones


def test_klines_to_candles_falls_back_to_time_field() -> None:
    """Some upstream candle producers use ``time`` instead of ``open_time_ms``."""
    candles = klines_to_candles(
        [{"time": 1_700_000_000_000, "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 10}]
    )
    assert len(candles) == 1
    assert candles[0]["time"] == 1_700_000_000_000


# ── inject_l2_into_payload ───────────────────────────────────────────────────


def test_inject_l2_dict_replaces_lob_block_and_returns_quality() -> None:
    payload = _full_payload()
    l2 = {
        "ok": True,
        "source": "bingx_l2_snapshot_rest",
        "data_quality_score": 0.83,
        "error": None,
    }
    quality = inject_l2_into_payload(payload, l2)
    assert quality == 0.83
    assert payload["lob_dynamics"] == l2
    assert payload["engine_status"]["lob_dynamics"]["ok"] is True


def test_inject_l2_pydantic_like_model_uses_model_dump() -> None:
    payload = _full_payload()

    class _LobModel:
        def model_dump(self, mode: str = "python") -> dict[str, Any]:
            return {
                "ok": False,
                "source": "bingx_l2_unavailable",
                "error": "snapshot_empty",
                "data_quality_score": None,
            }

    quality = inject_l2_into_payload(payload, _LobModel())
    assert quality is None
    assert payload["lob_dynamics"]["source"] == "bingx_l2_unavailable"
    assert payload["engine_status"]["lob_dynamics"]["ok"] is False
    assert payload["engine_status"]["lob_dynamics"]["error"] == "snapshot_empty"


def test_inject_l2_none_leaves_payload_untouched() -> None:
    payload = _full_payload()
    before = dict(payload["lob_dynamics"])
    quality = inject_l2_into_payload(payload, None)
    assert quality is None
    assert payload["lob_dynamics"] == before


def test_inject_l2_unrecognised_type_is_no_op() -> None:
    payload = _full_payload()
    before = dict(payload["lob_dynamics"])
    quality = inject_l2_into_payload(payload, 12345)  # not dict, not Pydantic
    assert quality is None
    assert payload["lob_dynamics"] == before


# ── compute_technical_quality_score ──────────────────────────────────────────


def test_quality_zero_when_no_engines_ok() -> None:
    assert compute_technical_quality_score({"smc": {}, "vsa": {"ok": False}}) == 0.0


def test_quality_full_when_all_engines_succeed() -> None:
    # 0.3 (smc) + 0.2 (vsa) + 0.2 (fvg ≥1 zone) + 0.2 (vp) + 0.1 (of) = 1.0
    assert compute_technical_quality_score(_full_payload()) == 1.0


def test_quality_drops_when_vsa_signal_missing() -> None:
    payload = _full_payload()
    payload["vsa"]["signal"] = None
    # full (1.0) minus vsa contribution (0.2) = 0.8
    assert compute_technical_quality_score(payload) == 0.8


def test_quality_partial_credit_when_fvg_empty_but_ok() -> None:
    payload = _full_payload()
    payload["fvg"]["active_count"] = 0
    payload["fvg"]["history_count"] = 0
    # FVG drops from 0.2 to 0.1 → total 0.9
    assert compute_technical_quality_score(payload) == 0.9


# ── build_venue_technical: degradation paths ─────────────────────────────────


async def test_build_venue_technical_no_klines_returns_unavailable() -> None:
    result = await build_venue_technical("BTC-USDT", [], technical_fn=AsyncMock())
    assert result.status == "unavailable"
    assert result.reason == REASON_NO_VENUE_BARS
    assert result.summary is None
    assert result.technical_quality_score is None


async def test_build_venue_technical_below_min_bars_returns_insufficient() -> None:
    # MIN_BARS_FOR_SMC = 35 — pass fewer so the gate trips before the fetcher runs.
    klines = [_kline_obj(i) for i in range(MIN_BARS_FOR_SMC - 5)]
    fetcher = AsyncMock()
    result = await build_venue_technical("BTC-USDT", klines, technical_fn=fetcher)
    assert result.status == "unavailable"
    assert result.reason == REASON_INSUFFICIENT_BARS
    fetcher.assert_not_awaited()


async def test_build_venue_technical_no_fetcher_returns_unavailable() -> None:
    klines = [_kline_obj(i) for i in range(40)]
    result = await build_venue_technical("BTC-USDT", klines, technical_fn=None)
    assert result.status == "unavailable"
    assert result.reason == REASON_NO_TECHNICAL_FETCHER


async def test_build_venue_technical_fetcher_exception_degrades() -> None:
    klines = [_kline_obj(i) for i in range(40)]
    fetcher = AsyncMock(side_effect=RuntimeError("smc engine crashed"))
    result = await build_venue_technical("BTC-USDT", klines, technical_fn=fetcher)
    assert result.status == "unavailable"
    assert result.reason == REASON_TECHNICAL_FETCH_FAILED


async def test_build_venue_technical_payload_not_ok_carries_error() -> None:
    klines = [_kline_obj(i) for i in range(40)]
    fetcher = AsyncMock(
        return_value={"ok": False, "error": "Insufficient bars (33); need at least 35..."}
    )
    result = await build_venue_technical("BTC-USDT", klines, technical_fn=fetcher)
    assert result.status == "unavailable"
    assert "Insufficient bars" in (result.reason or "")
    assert result.payload is not None


# ── build_venue_technical: success path + L2 injection ───────────────────────


async def test_build_venue_technical_success_extracts_summary_and_quality() -> None:
    klines = [_kline_obj(i) for i in range(40)]
    payload = _full_payload(bars=40)
    fetcher = AsyncMock(return_value=payload)

    result = await build_venue_technical(
        "BTC-USDT",
        klines,
        timeframe="5m",
        technical_fn=fetcher,
    )

    fetcher.assert_awaited_once()
    args, _ = fetcher.call_args
    assert args[0] == "BTC-USDT"
    candles = args[1]
    assert len(candles) == 40
    assert candles[0]["time"] == 1_700_000_000_000
    assert args[2] == "5m"

    assert result.status == "available"
    assert result.source == SOURCE_VENUE
    assert result.timeframe == "5m"
    assert result.summary is not None
    assert result.summary.trend_direction == "bullish"  # market_structure.bias
    assert result.summary.smc_bias == "BULLISH"
    assert result.summary.vsa_signal == "STRONG_BUY"
    assert result.summary.fvg_state == "bullish_dominant"
    assert result.summary.volume_profile_bias == "bullish"
    assert result.summary.composite_score == 0.72
    assert result.summary.bars_used == 40
    assert result.technical_quality_score == 1.0
    assert result.lob_quality_score is None  # no L2 provided


async def test_build_venue_technical_injects_l2_dict_and_surfaces_quality() -> None:
    klines = [_kline_obj(i) for i in range(40)]
    payload = _full_payload(bars=40)
    fetcher = AsyncMock(return_value=payload)
    l2 = {
        "ok": True,
        "source": "bingx_l2_snapshot_rest",
        "data_quality_score": 0.91,
    }
    result = await build_venue_technical(
        "BTC-USDT",
        klines,
        technical_fn=fetcher,
        l2_snapshot=l2,
    )
    assert result.status == "available"
    assert result.lob_quality_score == 0.91
    assert result.payload is not None
    assert result.payload["lob_dynamics"]["source"] == "bingx_l2_snapshot_rest"
    assert result.payload["engine_status"]["lob_dynamics"]["ok"] is True


# ── build_underlying_technical: routing ──────────────────────────────────────


async def test_build_underlying_crypto_skips_fetcher() -> None:
    full_fn = AsyncMock()
    lite_fn = AsyncMock()
    result = await build_underlying_technical(
        "BTC",
        market_type="crypto_standard",
        technical_fn=full_fn,
        equity_snapshot_fn=lite_fn,
    )
    full_fn.assert_not_awaited()
    lite_fn.assert_not_awaited()
    assert result.status == "unavailable"
    assert result.reason == REASON_NO_EQUITY_FOR_CRYPTO


async def test_build_underlying_no_fetcher_returns_unavailable() -> None:
    result = await build_underlying_technical(
        "GOOGL",
        market_type="stock_perp",
        technical_fn=None,
        equity_snapshot_fn=None,
    )
    assert result.status == "unavailable"
    assert result.reason == REASON_NO_TECHNICAL_FETCHER


async def test_build_underlying_full_mode_succeeds() -> None:
    payload = _full_payload(bars=250)
    payload["symbol"] = "GOOGL"
    full_fn = AsyncMock(return_value=payload)

    result = await build_underlying_technical(
        "GOOGL",
        market_type="stock_perp",
        technical_fn=full_fn,
        timeframe="1d",
        days=320,
    )
    full_fn.assert_awaited_once_with("GOOGL", 320, "1d")
    assert result.status == "available"
    assert result.source == SOURCE_UNDERLYING_FULL
    assert result.summary is not None
    assert result.summary.smc_bias == "BULLISH"
    assert result.summary.bars_used == 250
    assert result.technical_quality_score == 1.0


async def test_build_underlying_prefers_full_over_lite_when_both_given() -> None:
    full_fn = AsyncMock(return_value=_full_payload(bars=250))
    lite_fn = AsyncMock(return_value={"ok": True, "trend_direction": "bullish", "bars_used": 250})

    result = await build_underlying_technical(
        "GOOGL",
        market_type="stock_perp",
        technical_fn=full_fn,
        equity_snapshot_fn=lite_fn,
    )
    full_fn.assert_awaited_once()
    lite_fn.assert_not_awaited()
    assert result.source == SOURCE_UNDERLYING_FULL


async def test_build_underlying_lite_mode_succeeds_when_full_absent() -> None:
    lite_fn = AsyncMock(
        return_value={
            "ok": True,
            "trend_direction": "bearish",
            "bars_used": 180,
            "rsi_14": 35.0,
            "ema_fast": 175.0,
            "ema_slow": 178.0,
        }
    )
    result = await build_underlying_technical(
        "GOOGL",
        market_type="stock_perp",
        equity_snapshot_fn=lite_fn,
    )
    lite_fn.assert_awaited_once_with("GOOGL")
    assert result.status == "available"
    assert result.source == SOURCE_EQUITY_SNAPSHOT
    assert result.summary is not None
    assert result.summary.trend_direction == "bearish"
    assert result.summary.smc_bias is None
    assert result.summary.bars_used == 180
    # bars_used=180 → quality ≈ 0.9
    assert result.technical_quality_score == pytest.approx(0.9, rel=1e-3)


async def test_build_underlying_lite_mode_ok_false_surfaces_reason() -> None:
    lite_fn = AsyncMock(return_value={"ok": False, "reason": "fmp_unauthorized"})
    result = await build_underlying_technical(
        "GOOGL",
        market_type="stock_perp",
        equity_snapshot_fn=lite_fn,
    )
    assert result.status == "unavailable"
    assert result.reason == "fmp_unauthorized"


async def test_build_underlying_lite_mode_ok_false_without_reason_uses_default() -> None:
    lite_fn = AsyncMock(return_value={"ok": False})
    result = await build_underlying_technical(
        "GOOGL",
        market_type="stock_perp",
        equity_snapshot_fn=lite_fn,
    )
    assert result.reason == REASON_EQUITY_SNAPSHOT_NOT_OK


async def test_build_underlying_full_mode_exception_degrades() -> None:
    full_fn = AsyncMock(side_effect=RuntimeError("price repo offline"))
    result = await build_underlying_technical(
        "GOOGL",
        market_type="stock_perp",
        technical_fn=full_fn,
    )
    assert result.status == "unavailable"
    assert result.source == SOURCE_UNDERLYING_FULL
    assert result.reason == REASON_TECHNICAL_FETCH_FAILED


# ── JSON safety ──────────────────────────────────────────────────────────────


async def test_venue_result_to_dict_is_json_safe() -> None:
    klines = [_kline_obj(i) for i in range(40)]
    fetcher = AsyncMock(return_value=_full_payload(bars=40))
    result = await build_venue_technical("BTC-USDT", klines, technical_fn=fetcher)
    payload = result.to_dict()
    serialised = json.dumps(payload)
    parsed = json.loads(serialised)
    assert parsed["status"] == "available"
    assert parsed["source"] == SOURCE_VENUE
    assert parsed["summary"]["smc_bias"] == "BULLISH"
    assert parsed["summary"]["fvg_state"] == "bullish_dominant"


async def test_underlying_lite_result_to_dict_is_json_safe() -> None:
    lite_fn = AsyncMock(return_value={"ok": True, "trend_direction": "neutral", "bars_used": 150})
    result = await build_underlying_technical(
        "GOOGL",
        market_type="stock_perp",
        equity_snapshot_fn=lite_fn,
    )
    payload = result.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["source"] == SOURCE_EQUITY_SNAPSHOT
    assert payload["summary"]["trend_direction"] == "neutral"


async def test_unavailable_result_to_dict_is_json_safe() -> None:
    result = await build_venue_technical("BTC-USDT", [], technical_fn=AsyncMock())
    payload = result.to_dict()
    json.dumps(payload)
    assert payload["status"] == "unavailable"
    assert payload["summary"] is None
    assert payload["technical_quality_score"] is None
