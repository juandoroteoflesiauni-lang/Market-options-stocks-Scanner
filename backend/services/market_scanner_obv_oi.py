from __future__ import annotations
from typing import Any
"""Market Scanner adapter for OBV-OI (volume × options OI delta fusion)."""


import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import ScannerCustomization, ScannerIndicatorDefinition
from backend.quant_engine.math.technical.obv_oi import (
    ObvOiFrame,
    last_obv_oi_frame,
    obv_oi_bias_from_frame,
    obv_oi_score_from_frame,
    run_obv_oi_pipeline,
)

logger = get_logger(__name__)

_ATM_BAND_PCT = 0.02
_MIN_PRICE_BARS = 30


@dataclass(frozen=True)
class ObvOiScannerResult:
    ok: bool
    score: float
    bias: str
    signal: int
    confidence: float
    engine_status: str
    metrics: dict[str, float | int | None]
    reasons: list[str]


def resolve_obv_oi_sma_period(timeframe: str) -> int:
    """Intraday SMA window: 9 on 5m/15m; wider on 1m-style noise."""
    tf = timeframe.strip().lower()
    if tf in {"1m", "1min"}:
        return 14
    return 9


def indicator_weight_for_timeframe(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    timeframe: str,
) -> float:
    """Effective catalog / matrix weight for gated execution."""
    catalog = next((item for item in indicators if item.key == "obv_oi"), None)
    if catalog is None:
        return 0.0
    if timeframe not in catalog.supports_timeframes:
        return 0.0
    custom = customization.weight_matrix.get("obv_oi", {}).get(timeframe)
    if custom is not None:
        return float(custom)
    return float(catalog.weight_by_timeframe.get(timeframe, 0.0))


def analyze_obv_oi_for_scanner(
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    options_snapshot: object | None,
    *,
    iv_ratio_long_min: float = 1.0,
    iv_ratio_short_max: float = 1.0,
) -> ObvOiScannerResult:
    """Compute OBV-OI for one symbol/timeframe; degrades to neutral when data is partial."""
    price_df = _bars_to_price_frame(bars)
    if price_df is None or len(price_df) < _MIN_PRICE_BARS:
        return _neutral_result("Insufficient OHLCV bars for OBV-OI.")

    spot = float(price_df["close"].iloc[-1])
    options_df = _build_options_frame(
        options_snapshot, spot=spot, bar_timestamps=price_df["timestamp"]
    )
    if options_df is None or options_df.empty:
        logger.warning(
            "market_scanner.obv_oi_options_unavailable symbol=%s timeframe=%s",
            symbol,
            timeframe,
        )
        return _neutral_result("Options OI snapshot unavailable; OBV-OI skipped.")

    try:
        pipeline = run_obv_oi_pipeline(
            price_df,
            options_df,
            sma_period=resolve_obv_oi_sma_period(timeframe),
            iv_ratio_long_min=iv_ratio_long_min,
            iv_ratio_short_max=iv_ratio_short_max,
        )
    except Exception as exc:
        logger.error(
            "market_scanner.obv_oi_failed symbol=%s timeframe=%s error=%s",
            symbol,
            timeframe,
            str(exc)[:180],
        )
        return _neutral_result("OBV-OI computation failed.")

    frame = last_obv_oi_frame(pipeline)
    options_amplified = bool(
        frame is not None
        and frame.delta_oi_net is not None
        and math.isfinite(frame.delta_oi_net)
        and abs(frame.delta_oi_net) > 0
    )
    status = "real" if options_amplified else "partial"
    score = obv_oi_score_from_frame(frame, options_amplified=options_amplified)
    bias = obv_oi_bias_from_frame(frame)
    signal = frame.cross_signal if frame is not None else 0
    confidence = 0.55 if status == "real" else 0.25
    if frame is not None and frame.cross_signal != 0:
        confidence = min(0.9, confidence + 0.2)

    reasons: list[str] = []
    if frame is not None:
        if frame.cross_signal > 0:
            reasons.append("OBV-OI crossed above SMA with bullish IV filter.")
        elif frame.cross_signal < 0:
            reasons.append("OBV-OI crossed below SMA with bearish IV filter.")
        elif bias == "bullish":
            reasons.append("OBV-OI above SMA; options OI confirms volume bias.")
        elif bias == "bearish":
            reasons.append("OBV-OI below SMA; options OI confirms volume bias.")
        if not options_amplified:
            reasons.append("Flat OI delta — OBV-OI collapsed to pure OBV (no noise).")

    metrics = _metrics_from_frame(frame, signal=signal)
    return ObvOiScannerResult(
        ok=True,
        score=round(score, 2),
        bias=bias,
        signal=signal,
        confidence=round(confidence, 3),
        engine_status=status,
        metrics=metrics,
        reasons=reasons[:4],
    )


def attach_obv_oi_deep_metrics(
    deep_metrics: dict[str, dict[str, Any]] | None,
    timeframe: str,
    result: ObvOiScannerResult,
) -> dict[str, dict[str, Any]]:
    """Merge OBV-OI metrics into scanner deep_metrics for the drawer."""
    out: dict[str, dict[str, Any]] = dict(deep_metrics or {})
    bucket = dict(out.get(timeframe) or {})
    bucket.update(result.metrics)
    bucket["obv_oi_engine_status"] = result.engine_status
    bucket["obv_oi_score"] = result.score
    out[timeframe] = bucket
    return out


def apply_obv_oi_score_adjustment(
    base_score: float,
    result: ObvOiScannerResult | None,
    *,
    weight: float,
) -> tuple[float, list[str]]:
    """Blend OBV-OI desk score into Options/GEX synthesis when weight > 0."""
    if result is None or not result.ok or weight <= 0:
        return base_score, []
    delta = (result.score - 50.0) * min(weight, 5.0) / 5.0 * 0.35
    adjusted = float(np.clip(base_score + delta, 0.0, 100.0))
    return adjusted, list(result.reasons)


def _neutral_result(warning: str) -> ObvOiScannerResult:
    return ObvOiScannerResult(
        ok=False,
        score=50.0,
        bias="neutral",
        signal=0,
        confidence=0.0,
        engine_status="fallback",
        metrics={
            "obv_oi": None,
            "obv_oi_sma": None,
            "obv_oi_signal": 0,
            "iv_ratio": None,
            "oi_net": None,
            "delta_oi_net": None,
            "obv": None,
        },
        reasons=[warning],
    )


def _metrics_from_frame(frame: ObvOiFrame | None, *, signal: int) -> dict[str, float | int | None]:
    if frame is None:
        return {
            "obv": None,
            "oi_net": None,
            "delta_oi_net": None,
            "obv_oi": None,
            "obv_oi_sma": None,
            "iv_ratio": None,
            "obv_oi_signal": signal,
        }
    return {
        "obv": frame.obv,
        "oi_net": frame.oi_net,
        "delta_oi_net": frame.delta_oi_net,
        "obv_oi": frame.obv_oi,
        "obv_oi_sma": frame.obv_oi_sma,
        "iv_ratio": frame.iv_ratio,
        "obv_oi_signal": signal,
    }


def _bars_to_price_frame(bars: list[dict[str, Any]]) -> pd.DataFrame | None:
    rows: list[dict[str, float | pd.Timestamp]] = []
    for raw in bars:
        ts = _bar_timestamp(raw)
        if ts is None:
            continue
        try:
            close = float(raw.get("close", raw.get("c")))
            open_price = float(raw.get("open", raw.get("o", close)))
            high = float(raw.get("high", raw.get("h", close)))
            low = float(raw.get("low", raw.get("l", close)))
            volume = float(raw.get("volume", raw.get("v", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (open_price, high, low, close, volume)):
            continue
        if min(open_price, high, low, close) <= 0:
            continue
        rows.append(
            {
                "timestamp": ts,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


def _bar_timestamp(raw: dict[str, Any]) -> pd.Timestamp | None:
    for key in ("t", "time", "timestamp", "ts"):
        val = raw.get(key)
        if val is None:
            continue
        try:
            if isinstance(val, int | float):
                ms = float(val)
                if ms > 1e12:
                    return pd.to_datetime(int(ms), unit="ms", utc=True)
                return pd.to_datetime(int(ms), unit="s", utc=True)
            return pd.to_datetime(val, utc=True)
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _build_options_frame(
    snapshot: object | None,
    *,
    spot: float,
    bar_timestamps: pd.Series,
) -> pd.DataFrame | None:
    payload = _snapshot_dict(snapshot)
    if not payload:
        return None

    rows: list[dict[str, float | pd.Timestamp]] = []

    explicit = payload.get("obv_oi_options_snapshots")
    if isinstance(explicit, list):
        for item in explicit:
            parsed = _parse_options_row(item, spot=spot)
            if parsed is not None:
                rows.append(parsed)

    if not rows:
        history = _history_option_rows(payload)
        rows.extend(history)

    if not rows:
        chain_spot = _finite_float(payload.get("spot"))
        spot_for_chain = chain_spot if chain_spot is not None and chain_spot > 0 else spot
        current = _aggregate_chain_atm(payload, spot=spot_for_chain)
        if current is not None and not bar_timestamps.empty:
            rows.append({**current, "timestamp": pd.Timestamp(bar_timestamps.iloc[-1])})

    if not rows:
        return None

    frame = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return frame.drop_duplicates(subset=["timestamp"], keep="last")


def _history_option_rows(payload: dict[str, Any]) -> list[dict[str, float | pd.Timestamp]]:
    history = payload.get("chain_analytics_history")
    if not isinstance(history, dict):
        return []
    points = history.get("points")
    if not isinstance(points, list):
        return []
    out: list[dict[str, float | pd.Timestamp]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        ts_raw = point.get("as_of") or point.get("timestamp")
        if ts_raw is None:
            continue
        try:
            ts = pd.to_datetime(ts_raw, utc=True)
        except (TypeError, ValueError):
            continue
        metrics = point.get("standard_metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        call_oi = _finite_float(metrics.get("call_oi_total") or metrics.get("call_oi"))
        put_oi = _finite_float(metrics.get("put_oi_total") or metrics.get("put_oi"))
        iv_c = _finite_float(metrics.get("iv_calls_avg"))
        iv_p = _finite_float(metrics.get("iv_puts_avg"))
        if call_oi is None and put_oi is None:
            continue
        out.append(
            {
                "timestamp": ts,
                "oi_calls": call_oi or 0.0,
                "oi_puts": put_oi or 0.0,
                "iv_calls_avg": iv_c if iv_c is not None else 0.35,
                "iv_puts_avg": iv_p if iv_p is not None else 0.30,
            }
        )
    return out


def _parse_options_row(item: object, *, spot: float) -> dict[str, float | pd.Timestamp] | None:
    if not isinstance(item, dict):
        return None
    ts_raw = item.get("timestamp") or item.get("as_of") or item.get("t")
    if ts_raw is None:
        return None
    try:
        if isinstance(ts_raw, int | float):
            ms = float(ts_raw)
            ts = pd.to_datetime(int(ms), unit="ms" if ms > 1e12 else "s", utc=True)
        else:
            ts = pd.to_datetime(ts_raw, utc=True)
    except (TypeError, ValueError, OverflowError):
        return None
    oi_calls = _finite_float(item.get("oi_calls"))
    oi_puts = _finite_float(item.get("oi_puts"))
    if oi_calls is None and oi_puts is None:
        aggregated = _aggregate_chain_atm(item, spot=spot)
        if aggregated is None:
            return None
        oi_calls = aggregated["oi_calls"]
        oi_puts = aggregated["oi_puts"]
        iv_calls = aggregated["iv_calls_avg"]
        iv_puts = aggregated["iv_puts_avg"]
    else:
        iv_calls = _finite_float(item.get("iv_calls_avg")) or 0.35
        iv_puts = _finite_float(item.get("iv_puts_avg")) or 0.30
    return {
        "timestamp": ts,
        "oi_calls": oi_calls or 0.0,
        "oi_puts": oi_puts or 0.0,
        "iv_calls_avg": iv_calls,
        "iv_puts_avg": iv_puts,
    }


def _aggregate_chain_atm(payload: dict[str, Any], *, spot: float) -> dict[str, float] | None:
    chain = payload.get("chain")
    if not isinstance(chain, list) or spot <= 0:
        return None
    oi_calls = 0.0
    oi_puts = 0.0
    iv_calls: list[float] = []
    iv_puts: list[float] = []
    for leg in chain:
        if not isinstance(leg, dict):
            continue
        strike = _finite_float(leg.get("strike"))
        if strike is None or abs(strike - spot) / spot > _ATM_BAND_PCT:
            continue
        oi = _finite_float(leg.get("open_interest")) or 0.0
        iv = _finite_float(leg.get("implied_volatility"))
        opt_type = str(leg.get("option_type") or leg.get("type") or "").lower()
        if opt_type == "call":
            oi_calls += oi
            if iv is not None:
                iv_calls.append(iv)
        elif opt_type == "put":
            oi_puts += oi
            if iv is not None:
                iv_puts.append(iv)
    if oi_calls <= 0 and oi_puts <= 0:
        return None
    return {
        "oi_calls": oi_calls,
        "oi_puts": oi_puts,
        "iv_calls_avg": float(np.mean(iv_calls)) if iv_calls else 0.35,
        "iv_puts_avg": float(np.mean(iv_puts)) if iv_puts else 0.30,
    }


def _snapshot_dict(snapshot: object | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    if hasattr(snapshot, "model_dump"):
        dumped = snapshot.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return snapshot if isinstance(snapshot, dict) else {}


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, int | float | str):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None
