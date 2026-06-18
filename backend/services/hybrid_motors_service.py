"""Run hybrid price+options motors and attach blocks to venue technical payload."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from backend.config.bingx_hybrid_motors_calibration import (
    DIVERGENCE_MIN_SCORE,
    DIVERGENCE_SINCE_BARS,
    HYBRID_MAX_BARS,
    HYBRID_MIN_BARS,
    VSA_HYBRID_MIN_SCORE,
)
from backend.config.logger_setup import get_logger
from backend.quant_engine.engines.hybrid.delta_profile_hybrid import run_delta_profile_hybrid
from backend.quant_engine.engines.hybrid.divergences_hybrid import (
    DivergenceSignalAdapter,
    HybridDivergenceEngine,
)
from backend.quant_engine.engines.hybrid.elliott_wave_hybrid import HybridElliottWaveEngine
from backend.quant_engine.engines.hybrid.exhaustion_hybrid import HybridTrendExhaustionEngine
from backend.quant_engine.engines.hybrid.shadow_macd_hybrid import HybridShadowMACDEngine
from backend.quant_engine.engines.hybrid.vsa_hybrid import HybridVSAEngine
from backend.quant_engine.engines.hybrid.wavetrend_hybrid import HybridWaveTrendEngine
from backend.services.hybrid_options_adapter import (
    build_charm_snapshot,
    build_gex_bar,
    build_gex_snapshot,
    build_options_chain,
    build_tick_input,
    build_vanna_snapshot,
    chain_rows_from_snapshot,
)

logger = get_logger(__name__)

_BULL_TOKENS = frozenset(
    {
        "BULL",
        "BUY",
        "LONG",
        "BULLISH",
        "ACCUMULATION",
        "W3_BULL",
        "GEX_LEAD_BULL",
        "WT_CROSS_BULL",
        "DOUBLE_CROSS_BULL",
        "SYNC_CROSS_BULL",
        "HYBRID_ZERO_CROSS_BULL",
        "LEAD_PRICE_BULL",
        "LEAD_NDDE_BULL",
        "PRICE_CROSS_BULL",
        "HYBRID_ACCELERATING_BULL",
        "FULL_BULL",
        "BUY_SETUP",
        "BUY_COUNTDOWN",
        "BULL_EXHAUSTION",
        "BULL_CHARM",
        "ACCUMULATION_ALIGNED",
        "W3_BULL_GAMMA_CONFIRMED",
        "W3_BULL_GEX_VALID",
    }
)
_BEAR_TOKENS = frozenset(
    {
        "BEAR",
        "SELL",
        "SHORT",
        "BEARISH",
        "DISTRIBUTION",
        "W3_BEAR",
        "GEX_LEAD_BEAR",
        "WT_CROSS_BEAR",
        "DOUBLE_CROSS_BEAR",
        "SYNC_CROSS_BEAR",
        "HYBRID_ZERO_CROSS_BEAR",
        "LEAD_PRICE_BEAR",
        "LEAD_NDDE_BEAR",
        "PRICE_CROSS_BEAR",
        "HYBRID_ACCELERATING_BEAR",
        "FULL_BEAR",
        "SELL_SETUP",
        "SELL_COUNTDOWN",
        "BEAR_EXHAUSTION",
        "BEAR_CHARM",
        "DISTRIBUTION_ALIGNED",
        "W3_BEAR_GAMMA_CONFIRMED",
        "W3_BEAR_GEX_VALID",
        "WC_BEAR_CONFIRMED",
    }
)


def _candle_ts_ms(row: dict[str, Any]) -> int:
    for key in ("open_time_ms", "t", "timestamp_ms", "time"):
        val = row.get(key)
        if val is not None:
            return int(val)
    return int(datetime.now(UTC).timestamp() * 1000)


def _normalize_candles(
    candles: list[Any], *, max_bars: int = HYBRID_MAX_BARS
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in candles[-max_bars:]:
        if isinstance(raw, dict):
            row = raw
        else:
            row = {
                "open_time_ms": getattr(raw, "open_time_ms", None),
                "open": getattr(raw, "open", None),
                "high": getattr(raw, "high", None),
                "low": getattr(raw, "low", None),
                "close": getattr(raw, "close", None),
                "volume": getattr(raw, "volume", None),
            }
        o = float(row.get("open") or row.get("o") or 0.0)
        h = float(row.get("high") or row.get("h") or 0.0)
        low = float(row.get("low") or row.get("l") or 0.0)
        c = float(row.get("close") or row.get("c") or 0.0)
        vol = float(row.get("volume") or row.get("v") or 0.0)
        if c <= 0:
            continue
        out.append(
            {
                "open_time_ms": _candle_ts_ms(row),
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": vol,
            }
        )
    return out


def _compute_flow_series(candles: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Proxy delta-RSI / NDDE series from OHLCV when historical options flow is absent."""
    if not candles:
        return []
    closes = np.array([c["close"] for c in candles], dtype=float)
    volumes = np.array([c["volume"] for c in candles], dtype=float)
    opens = np.array([c["open"] for c in candles], dtype=float)
    signed = volumes * np.sign(closes - opens)
    signed[np.isnan(signed)] = 0.0

    period = 14
    deltas = np.diff(signed, prepend=signed[0])
    gains = np.maximum(deltas, 0.0)
    losses = np.maximum(-deltas, 0.0)
    avg_gain = pd.Series(gains).ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    avg_loss = pd.Series(losses).ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    rs = np.divide(avg_gain, np.maximum(avg_loss, 1e-9))
    delta_rsi = 100.0 - (100.0 / (1.0 + rs))

    cum_ndde = pd.Series(signed).cumsum().to_numpy()
    ndde_smooth = pd.Series(cum_ndde).ewm(span=5, adjust=False).mean().to_numpy()
    macd_line = (
        pd.Series(ndde_smooth).ewm(span=12, adjust=False).mean()
        - pd.Series(ndde_smooth).ewm(span=26, adjust=False).mean()
    )
    macd_ndde = macd_line.to_numpy()
    macd_signal = pd.Series(macd_ndde).ewm(span=9, adjust=False).mean().to_numpy()

    flows: list[dict[str, float]] = []
    for i in range(len(candles)):
        flows.append(
            {
                "delta_rsi": float(delta_rsi[i]),
                "rsi_flow": float(delta_rsi[i]),
                "hist_flow": float(macd_ndde[i] - macd_signal[i]),
                "ndde": float(cum_ndde[i]),
                "ndde_smooth": float(ndde_smooth[i]),
                "macd_ndde": float(macd_ndde[i]),
            }
        )
    return flows


def _make_candle_bar(module: Any, row: dict[str, Any], ticker: str) -> Any:
    ts = pd.Timestamp(row["open_time_ms"], unit="ms", tz=UTC)
    return module.CandleBar(
        timestamp=ts,
        ticker=ticker,
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
    )


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.floating | np.integer):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    return value


def _block_from_result(result: dict[str, Any]) -> dict[str, Any]:
    safe = _json_safe(result)
    signal = str(safe.get("signal") or "NEUTRAL").upper()
    strength = int(safe.get("strength") or safe.get("priority") or 0)
    return {
        "ok": True,
        "signal": signal,
        "strength": strength,
        "direction": safe.get("direction") or safe.get("direction_bias"),
        "direction_bias": safe.get("direction_bias") or safe.get("direction"),
        "score": safe.get("score"),
        "regime": safe.get("regime"),
        "interpretation": safe.get("interpretation"),
        "raw": safe,
    }


def run_hybrid_motors(
    *,
    ticker: str,
    candles: list[Any],
    options_metrics: dict[str, Any] | None = None,
    raw_options_snapshot: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Execute all hybrid motors; returns engine-keyed blocks for venue payload."""
    normalized = _normalize_candles(candles)
    if len(normalized) < HYBRID_MIN_BARS:
        return {
            k: {"ok": False, "reason": "insufficient_bars", "signal": "NEUTRAL"}
            for k in (
                "hybrid_wavetrend",
                "hybrid_divergences",
                "hybrid_vsa",
                "hybrid_elliott",
                "hybrid_exhaustion",
                "hybrid_shadow_macd",
                "hybrid_delta_profile",
            )
        }

    metrics = dict(options_metrics or {})
    chain_rows = chain_rows_from_snapshot(raw_options_snapshot)
    flows = _compute_flow_series(normalized)

    import backend.quant_engine.engines.hybrid.elliott_wave_hybrid as ew_mod
    import backend.quant_engine.engines.hybrid.exhaustion_hybrid as exh_mod
    import backend.quant_engine.engines.hybrid.shadow_macd_hybrid as sm_mod
    import backend.quant_engine.engines.hybrid.vsa_hybrid as vsa_mod
    import backend.quant_engine.engines.hybrid.wavetrend_hybrid as wt_mod

    wt_engine = HybridWaveTrendEngine(ticker=ticker)
    div_engine = HybridDivergenceEngine(ticker=ticker, min_score=DIVERGENCE_MIN_SCORE)
    vsa_engine = HybridVSAEngine(ticker=ticker, min_score=VSA_HYBRID_MIN_SCORE)
    ew_engine = HybridElliottWaveEngine(ticker=ticker)
    exh_engine = HybridTrendExhaustionEngine(ticker=ticker)
    sm_engine = HybridShadowMACDEngine(ticker=ticker)

    last_wt: dict[str, Any] = {}
    last_vsa: dict[str, Any] = {}
    last_ew: dict[str, Any] = {}
    last_exh: dict[str, Any] = {}
    last_sm: dict[str, Any] = {}

    for idx, row in enumerate(normalized):
        ts = pd.Timestamp(row["open_time_ms"], unit="ms", tz=UTC)
        gex_snap = build_gex_snapshot(ticker=ticker, metrics=metrics, timestamp=ts)
        vanna_snap = build_vanna_snapshot(ticker=ticker, metrics=metrics, timestamp=ts)
        charm_snap = build_charm_snapshot(ticker=ticker, metrics=metrics, timestamp=ts)
        chain = build_options_chain(
            ticker=ticker,
            metrics=metrics,
            chain_rows=chain_rows,
            timestamp=ts,
        )
        gex_bar = build_gex_bar(metrics=metrics, timestamp=ts)

        wt_candle = _make_candle_bar(wt_mod, row, ticker)
        vsa_candle = _make_candle_bar(vsa_mod, row, ticker)
        exh_candle = _make_candle_bar(exh_mod, row, ticker)
        sm_candle = _make_candle_bar(sm_mod, row, ticker)
        ew_candle = _make_candle_bar(ew_mod, row, ticker)

        last_wt = wt_engine.update(wt_candle, gex_snap)
        last_vsa = vsa_engine.update(vsa_candle, vanna_snap)
        last_ew = ew_engine.update(ew_candle, gex_bar)
        last_exh = exh_engine.update(exh_candle, charm_snap)
        last_sm = sm_engine.update(sm_candle, chain)

        tick = build_tick_input(
            ticker=ticker,
            candle=row,
            timestamp=ts,
            flow=flows[idx],
            metrics=metrics,
        )
        div_engine.update(tick)

    div_adapter = DivergenceSignalAdapter(div_engine)
    div_signal = div_adapter.get_combiner_input(
        since_minutes=DIVERGENCE_SINCE_BARS,
        min_score=DIVERGENCE_MIN_SCORE,
    )
    div_block: dict[str, Any] = {
        "ok": True,
        "signal": div_signal.get("direction_bias", "NEUTRAL"),
        "direction_bias": div_signal.get("direction_bias", "NEUTRAL"),
        "direction": div_signal.get("direction_bias", "NEUTRAL"),
        "score": div_signal.get("score", 0.0),
        "strength": 3 if float(div_signal.get("score") or 0) >= 60 else 1,
        "n_active": div_signal.get("n_active", 0),
        "best_pair": div_signal.get("best_pair"),
    }
    if div_signal.get("n_active", 0) == 0:
        div_block["signal"] = "NEUTRAL"
        div_block["strength"] = 0

    delta_block = run_delta_profile_hybrid(
        symbol=ticker,
        candles=normalized,
        chain_rows=chain_rows,
        spot=float(metrics.get("spot") or normalized[-1]["close"]),
    )

    return {
        "hybrid_wavetrend": _block_from_result(last_wt),
        "hybrid_divergences": div_block,
        "hybrid_vsa": _block_from_result(last_vsa),
        "hybrid_elliott": _block_from_result(last_ew),
        "hybrid_exhaustion": _block_from_result(last_exh),
        "hybrid_shadow_macd": _block_from_result(last_sm),
        "hybrid_delta_profile": delta_block,
    }


def merge_hybrid_blocks_into_payload(
    payload: dict[str, Any],
    hybrid_blocks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merge hybrid motor blocks into an existing technical terminal payload."""
    merged = dict(payload)
    merged.update(hybrid_blocks)
    merged["hybrid_motors"] = {
        "ok": any(b.get("ok") for b in hybrid_blocks.values()),
        "engines": list(hybrid_blocks.keys()),
        "active": sum(1 for b in hybrid_blocks.values() if b.get("ok")),
    }
    return merged


def hybrid_bias_from_block(block: dict[str, Any] | None) -> str:
    """Map hybrid block to BULLISH / BEARISH / NEUTRAL for consensus voting."""
    if not isinstance(block, dict) or not block.get("ok"):
        return "NEUTRAL"
    for field in ("direction_bias", "direction", "signal"):
        token = str(block.get(field) or "").upper()
        if any(b in token for b in _BULL_TOKENS):
            return "BULLISH"
        if any(b in token for b in _BEAR_TOKENS):
            return "BEARISH"
    score = block.get("score")
    if isinstance(score, int | float):
        if score > 0.55:
            return "BULLISH"
        if score < -0.55:
            return "BEARISH"
    return "NEUTRAL"
