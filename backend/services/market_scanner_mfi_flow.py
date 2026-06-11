"""Market Scanner adapter for MFI-Flow (MFI × normalized options premium flow)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import ScannerCustomization, ScannerIndicatorDefinition
from backend.layer_2_quant_engine.math_core.mfi_flow import (
    MfiFlowFrame,
    last_mfi_flow_frame,
    mfi_flow_bias_from_frame,
    mfi_flow_score_from_frame,
    obv_mfi_double_conviction_active,
    run_mfi_flow_pipeline,
)
from backend.layer_3_specialists.opciones_gex.options_flow_signal import OptionsFlowSignalEngine
from backend.services.market_scanner_obv_oi import (
    ObvOiScannerResult,
    _bars_to_price_frame,
    _finite_float,
    _snapshot_dict,
)

logger = get_logger(__name__)

_ATM_BAND_PCT = 0.02
_MIN_PRICE_BARS = 30
_CONTRACT_MULT = 100.0


@dataclass(frozen=True)
class MfiFlowScannerResult:
    ok: bool
    score: float
    bias: str
    signal: int
    confidence: float
    engine_status: str
    metrics: dict[str, float | int | None]
    reasons: list[str]
    double_conviction: bool = False


def resolve_mfi_flow_norm_window(timeframe: str) -> int:
    tf = timeframe.strip().lower()
    if tf in {"5m", "15m"}:
        return 20
    return 20


def resolve_mfi_flow_period(timeframe: str) -> int:
    tf = timeframe.strip().lower()
    if tf in {"1m", "1min", "5m"}:
        return 14
    return 14


def indicator_weight_for_timeframe(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    timeframe: str,
) -> float:
    catalog = next((item for item in indicators if item.key == "mfi_flow"), None)
    if catalog is None:
        return 0.0
    if timeframe not in catalog.supports_timeframes:
        return 0.0
    custom = customization.weight_matrix.get("mfi_flow", {}).get(timeframe)
    if custom is not None:
        return float(custom)
    return float(catalog.weight_by_timeframe.get(timeframe, 0.0))


def analyze_mfi_flow_for_scanner(
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    options_snapshot: object | None,
) -> MfiFlowScannerResult:
    price_df = _bars_to_price_frame(bars)
    if price_df is None or len(price_df) < _MIN_PRICE_BARS:
        return _neutral_result("Insufficient OHLCV bars for MFI-Flow.")

    spot = float(price_df["close"].iloc[-1])
    options_df = _build_flow_options_frame(
        options_snapshot,
        spot=spot,
        bar_timestamps=price_df["timestamp"],
    )
    if options_df is None or options_df.empty:
        logger.warning(
            "market_scanner.mfi_flow_options_unavailable symbol=%s timeframe=%s",
            symbol,
            timeframe,
        )
        return _neutral_result("Options flow snapshot unavailable; MFI-Flow skipped.")

    try:
        pipeline = run_mfi_flow_pipeline(
            price_df,
            options_df,
            mfi_period=resolve_mfi_flow_period(timeframe),
            norm_window=resolve_mfi_flow_norm_window(timeframe),
        )
    except Exception as exc:
        logger.error(
            "market_scanner.mfi_flow_failed symbol=%s timeframe=%s error=%s",
            symbol,
            timeframe,
            str(exc)[:180],
        )
        return _neutral_result("MFI-Flow computation failed.")

    frame = last_mfi_flow_frame(pipeline)
    flow_amplified = bool(
        frame is not None
        and frame.flow_ratio_norm is not None
        and math.isfinite(frame.flow_ratio_norm)
        and abs(frame.flow_ratio_norm - 1.0) > 0.05
    )
    status = "real" if flow_amplified else "partial"
    score = mfi_flow_score_from_frame(frame, flow_amplified=flow_amplified)
    bias = mfi_flow_bias_from_frame(frame)
    signal = (
        frame.entry_signal
        if frame is not None and frame.entry_signal != 0
        else (frame.signal if frame is not None else 0)
    )
    confidence = 0.55 if status == "real" else 0.25
    if frame is not None and frame.mfi_flow is not None and frame.mfi_flow >= 70.0:
        confidence = min(0.92, confidence + 0.22)
    elif frame is not None and frame.mfi_flow is not None and frame.mfi_flow <= 30.0:
        confidence = min(0.92, confidence + 0.18)

    reasons: list[str] = []
    if frame is not None and frame.mfi_flow is not None:
        if frame.mfi_flow > 60.0:
            reasons.append("MFI-Flow > 60 with call-flow dominance (ratio-normalized).")
        elif frame.mfi_flow < 40.0:
            reasons.append("MFI-Flow < 40 with put-flow dominance (ratio-normalized).")
        if frame.flow_ratio_norm is not None and frame.flow_ratio_norm > 1.2:
            reasons.append("Flow ratio norm elevated vs SMA(20) — institutional call premium.")
        if not flow_amplified:
            reasons.append("Neutral flow ratio — MFI-Flow collapsed toward classic MFI.")

    metrics = _metrics_from_frame(frame, signal=signal)
    return MfiFlowScannerResult(
        ok=True,
        score=round(score, 2),
        bias=bias,
        signal=signal,
        confidence=round(confidence, 3),
        engine_status=status,
        metrics=metrics,
        reasons=reasons[:4],
    )


def attach_mfi_flow_deep_metrics(
    deep_metrics: dict[str, dict[str, Any]] | None,
    timeframe: str,
    result: MfiFlowScannerResult,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = dict(deep_metrics or {})
    bucket = dict(out.get(timeframe) or {})
    bucket.update(result.metrics)
    bucket["mfi_flow_engine_status"] = result.engine_status
    bucket["mfi_flow_score"] = result.score
    bucket["mfi_flow_double_conviction"] = result.double_conviction
    out[timeframe] = bucket
    return out


def apply_mfi_flow_score_adjustment(
    base_score: float,
    result: MfiFlowScannerResult | None,
    *,
    weight: float,
) -> tuple[float, list[str]]:
    if result is None or not result.ok or weight <= 0:
        return base_score, []
    delta = (result.score - 50.0) * min(weight, 5.0) / 5.0 * 0.32
    adjusted = float(np.clip(base_score + delta, 0.0, 100.0))
    return adjusted, list(result.reasons)


def apply_obv_mfi_conviction_adjustment(
    score: float,
    confidence: float,
    *,
    obv_oi_result: ObvOiScannerResult | None,
    mfi_flow_result: MfiFlowScannerResult | None,
    conviction_weight_scale: float = 1.0,
) -> tuple[float, float, list[str]]:
    """Boost score/confidence when OBV-OI and MFI-Flow align (double conviction)."""
    if (
        obv_oi_result is None
        or mfi_flow_result is None
        or not obv_oi_result.ok
        or not mfi_flow_result.ok
    ):
        return score, confidence, []

    mfi_val = mfi_flow_result.metrics.get("mfi_flow")
    if not isinstance(mfi_val, int | float):
        return score, confidence, []

    active = obv_mfi_double_conviction_active(
        obv_oi_signal=int(obv_oi_result.metrics.get("obv_oi_signal") or obv_oi_result.signal),
        obv_oi_bias=obv_oi_result.bias,
        mfi_flow=float(mfi_val),
    )
    if not active:
        return score, confidence, []

    reasons: list[str] = []
    boost = 6.0 * max(0.0, min(2.0, conviction_weight_scale))
    adjusted_score = score
    if float(mfi_val) >= 70.0:
        adjusted_score = float(np.clip(score + boost, 0.0, 100.0))
        reasons.append("Double conviction: OBV-OI bullish + MFI-Flow > 70 (OI + active premium).")
    else:
        adjusted_score = float(np.clip(score - boost, 0.0, 100.0))
        reasons.append("Double conviction: OBV-OI bearish + MFI-Flow < 30 (OI + active premium).")
    boosted_conf = float(np.clip(confidence + 0.1, 0.0, 1.0))
    return adjusted_score, boosted_conf, reasons


def mark_double_conviction(
    result: MfiFlowScannerResult,
    *,
    obv_oi_result: ObvOiScannerResult | None,
) -> MfiFlowScannerResult:
    if obv_oi_result is None or not obv_oi_result.ok or not result.ok:
        return result
    mfi_val = result.metrics.get("mfi_flow")
    if not isinstance(mfi_val, int | float):
        return result
    active = obv_mfi_double_conviction_active(
        obv_oi_signal=int(obv_oi_result.metrics.get("obv_oi_signal") or obv_oi_result.signal),
        obv_oi_bias=obv_oi_result.bias,
        mfi_flow=float(mfi_val),
    )
    if not active:
        return result
    reasons = list(result.reasons)
    reasons.insert(0, "OBV-OI + MFI-Flow double conviction aligned.")
    return MfiFlowScannerResult(
        ok=result.ok,
        score=result.score,
        bias=result.bias,
        signal=result.signal,
        confidence=min(0.95, result.confidence + 0.05),
        engine_status=result.engine_status,
        metrics=dict(result.metrics),
        reasons=reasons[:5],
        double_conviction=True,
    )


def _neutral_result(warning: str) -> MfiFlowScannerResult:
    return MfiFlowScannerResult(
        ok=False,
        score=50.0,
        bias="neutral",
        signal=0,
        confidence=0.0,
        engine_status="fallback",
        metrics={
            "mfi": None,
            "flow_ratio": None,
            "flow_ratio_norm": None,
            "delta_net": None,
            "mfi_flow": None,
            "mfi_flow_signal": 0,
        },
        reasons=[warning],
    )


def _metrics_from_frame(
    frame: MfiFlowFrame | None, *, signal: int
) -> dict[str, float | int | None]:
    if frame is None:
        return {
            "mfi": None,
            "flow_ratio": None,
            "flow_ratio_norm": None,
            "delta_net": None,
            "mfi_flow": None,
            "mfi_flow_signal": signal,
        }
    return {
        "mfi": frame.mfi,
        "flow_ratio": frame.flow_ratio,
        "flow_ratio_norm": frame.flow_ratio_norm,
        "delta_net": frame.delta_net,
        "mfi_flow": frame.mfi_flow,
        "mfi_flow_signal": signal,
    }


def _build_flow_options_frame(
    snapshot: object | None,
    *,
    spot: float,
    bar_timestamps: pd.Series,
) -> pd.DataFrame | None:
    payload = _snapshot_dict(snapshot)
    if not payload:
        return None

    rows: list[dict[str, float | pd.Timestamp]] = []

    explicit = payload.get("mfi_flow_options_snapshots")
    if isinstance(explicit, list):
        for item in explicit:
            parsed = _parse_flow_row(item)
            if parsed is not None:
                rows.append(parsed)

    if not rows:
        history = _history_flow_rows(payload)
        rows.extend(history)

    if not rows:
        flow_row = _flow_row_from_snapshot(payload, spot=spot)
        if flow_row is not None and not bar_timestamps.empty:
            rows.append({**flow_row, "timestamp": pd.Timestamp(bar_timestamps.iloc[-1])})

    if not rows:
        chain_row = _aggregate_chain_flow(payload, spot=spot)
        if chain_row is not None and not bar_timestamps.empty:
            rows.append({**chain_row, "timestamp": pd.Timestamp(bar_timestamps.iloc[-1])})

    if not rows:
        return None

    frame = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return frame.drop_duplicates(subset=["timestamp"], keep="last")


def _parse_flow_row(item: object) -> dict[str, float | pd.Timestamp] | None:
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
    call_flow = _finite_float(item.get("call_flow_usd"))
    put_flow = _finite_float(item.get("put_flow_usd"))
    if call_flow is None and put_flow is None:
        return None
    delta = _finite_float(item.get("delta_net")) or 0.0
    return {
        "timestamp": ts,
        "call_flow_usd": call_flow or 0.0,
        "put_flow_usd": put_flow or 1.0,
        "delta_net": delta,
    }


def _history_flow_rows(payload: dict[str, Any]) -> list[dict[str, float | pd.Timestamp]]:
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
        call_flow = _finite_float(metrics.get("call_flow_usd") or metrics.get("call_premium_usd"))
        put_flow = _finite_float(metrics.get("put_flow_usd") or metrics.get("put_premium_usd"))
        if call_flow is None and put_flow is None:
            continue
        delta = _finite_float(metrics.get("net_dex") or metrics.get("delta_net")) or 0.0
        out.append(
            {
                "timestamp": ts,
                "call_flow_usd": call_flow or 0.0,
                "put_flow_usd": put_flow or 1.0,
                "delta_net": delta,
            }
        )
    return out


def _flow_row_from_snapshot(payload: dict[str, Any], *, spot: float) -> dict[str, float] | None:
    flow_signal = payload.get("flow_signal")
    if isinstance(flow_signal, dict):
        total_premium = _finite_float(flow_signal.get("total_premium"))
        ratio = _finite_float(flow_signal.get("call_put_volume_ratio"))
        directional = _finite_float(flow_signal.get("directional_score")) or 0.0
        if total_premium is not None and total_premium > 0 and ratio is not None and ratio > 0:
            put_flow = total_premium / (1.0 + ratio)
            call_flow = total_premium - put_flow
            return {
                "call_flow_usd": call_flow,
                "put_flow_usd": max(put_flow, 1.0),
                "delta_net": directional * 1_000_000.0,
            }

    features = payload.get("options_gex_features")
    if isinstance(features, dict):
        call_flow = _finite_float(features.get("call_flow_usd"))
        put_flow = _finite_float(features.get("put_flow_usd"))
        if call_flow is not None or put_flow is not None:
            delta = _finite_float(features.get("net_delta_exposure")) or _finite_float(
                payload.get("total_dex")
            )
            return {
                "call_flow_usd": call_flow or 0.0,
                "put_flow_usd": put_flow or 1.0,
                "delta_net": delta or 0.0,
            }

    chain = payload.get("chain")
    if isinstance(chain, list) and chain:
        engine = OptionsFlowSignalEngine()
        signal = engine.analyze(chain)
        if signal.total_premium > 0 and signal.call_put_volume_ratio is not None:
            ratio = signal.call_put_volume_ratio
            put_flow = signal.total_premium / (1.0 + ratio)
            call_flow = signal.total_premium - put_flow
            return {
                "call_flow_usd": call_flow,
                "put_flow_usd": max(put_flow, 1.0),
                "delta_net": signal.directional_score * 1_000_000.0,
            }

    return _aggregate_chain_flow(payload, spot=spot)


def _aggregate_chain_flow(payload: dict[str, Any], *, spot: float) -> dict[str, float] | None:
    chain = payload.get("chain")
    if not isinstance(chain, list) or spot <= 0:
        return None
    call_flow = 0.0
    put_flow = 0.0
    delta_net = _finite_float(payload.get("total_dex")) or 0.0
    for leg in chain:
        if not isinstance(leg, dict):
            continue
        strike = _finite_float(leg.get("strike"))
        if strike is None or abs(strike - spot) / spot > _ATM_BAND_PCT:
            continue
        opt_type = str(leg.get("option_type") or leg.get("type") or "").lower()
        if opt_type == "call":
            vol = _finite_float(leg.get("call_volume") or leg.get("volume")) or 0.0
            px = (
                _finite_float(leg.get("call_mid"))
                or _finite_float(leg.get("call_mark"))
                or _finite_float(leg.get("call_last"))
                or 0.0
            )
            call_flow += vol * px * _CONTRACT_MULT
            cd = _finite_float(leg.get("call_delta") or leg.get("net_dex"))
            if cd is not None:
                delta_net += cd
        elif opt_type == "put":
            vol = _finite_float(leg.get("put_volume") or leg.get("volume")) or 0.0
            px = (
                _finite_float(leg.get("put_mid"))
                or _finite_float(leg.get("put_mark"))
                or _finite_float(leg.get("put_last"))
                or 0.0
            )
            put_flow += vol * px * _CONTRACT_MULT
            pd = _finite_float(leg.get("put_delta") or leg.get("net_dex"))
            if pd is not None:
                delta_net += pd
    if call_flow <= 0 and put_flow <= 0:
        return None
    return {
        "call_flow_usd": call_flow,
        "put_flow_usd": max(put_flow, 1.0),
        "delta_net": delta_net,
    }
