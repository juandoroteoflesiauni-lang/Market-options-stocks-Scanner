"""Market Scanner adapter for CMF-IV (CMF / IV regime × Vega sign)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import ScannerCustomization, ScannerIndicatorDefinition
from backend.layer_2_quant_engine.math_core.cmf_iv import (
    CmfIvFrame,
    cmf_iv_bias_from_frame,
    cmf_iv_score_from_frame,
    last_cmf_iv_frame,
    run_cmf_iv_pipeline,
)
from backend.services.market_scanner_obv_oi import (
    _bars_to_price_frame,
    _finite_float,
    _snapshot_dict,
)

logger = get_logger(__name__)

_ATM_BAND_PCT = 0.02
_MIN_PRICE_BARS = 30
_CONTRACT_MULT = 100.0
_DEFAULT_VEGA_THRESHOLD = 100.0


@dataclass(frozen=True)
class CmfIvScannerResult:
    ok: bool
    score: float
    bias: str
    signal: int
    confidence: float
    engine_status: str
    metrics: dict[str, float | int | None]
    reasons: list[str]
    iv_crush_active: bool = False


def resolve_cmf_period(timeframe: str) -> int:
    tf = timeframe.strip().lower()
    if tf in {"1m", "1min", "5m"}:
        return 20
    return 20


def indicator_weight_for_timeframe(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    timeframe: str,
) -> float:
    catalog = next((item for item in indicators if item.key == "cmf_iv"), None)
    if catalog is None:
        return 0.0
    if timeframe not in catalog.supports_timeframes:
        return 0.0
    custom = customization.weight_matrix.get("cmf_iv", {}).get(timeframe)
    if custom is not None:
        return float(custom)
    return float(catalog.weight_by_timeframe.get(timeframe, 0.0))


def analyze_cmf_iv_for_scanner(
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    options_snapshot: object | None,
) -> CmfIvScannerResult:
    price_df = _bars_to_price_frame(bars)
    if price_df is None or len(price_df) < _MIN_PRICE_BARS:
        return _neutral_result("Insufficient OHLCV bars for CMF-IV.")

    spot = float(price_df["close"].iloc[-1])
    vol_df = _build_volatility_frame(
        options_snapshot,
        spot=spot,
        bar_timestamps=price_df["timestamp"],
    )
    if vol_df is None or vol_df.empty:
        logger.warning(
            "market_scanner.cmf_iv_vol_unavailable symbol=%s timeframe=%s",
            symbol,
            timeframe,
        )
        return _neutral_result("Volatility/Vega snapshot unavailable; CMF-IV skipped.")

    try:
        pipeline = run_cmf_iv_pipeline(
            price_df,
            vol_df,
            cmf_period=resolve_cmf_period(timeframe),
        )
    except Exception as exc:
        logger.error(
            "market_scanner.cmf_iv_failed symbol=%s timeframe=%s error=%s",
            symbol,
            timeframe,
            str(exc)[:180],
        )
        return _neutral_result("CMF-IV computation failed.")

    frame = last_cmf_iv_frame(pipeline)
    iv_regime_ok = bool(
        frame is not None
        and frame.iv_pct is not None
        and math.isfinite(frame.iv_pct)
        and not frame.iv_crush_active
    )
    status = (
        "real" if iv_regime_ok and frame is not None and frame.vega_net is not None else "partial"
    )
    score = cmf_iv_score_from_frame(frame)
    bias = cmf_iv_bias_from_frame(frame)
    signal = frame.signal if frame is not None else 0
    confidence = 0.5 if status == "real" else 0.28
    if frame is not None and frame.cmf_iv is not None:
        confidence = min(0.9, confidence + min(0.35, abs(frame.cmf_iv) * 0.12))
    if frame is not None and frame.iv_crush_active:
        confidence = max(0.15, confidence - 0.25)

    reasons: list[str] = []
    if frame is not None:
        if frame.iv_crush_active:
            reasons.append("IV crush filter active (iv_pct ≥ 0.80) — desk blocks directional bias.")
        elif frame.cmf_iv is not None and frame.cmf_iv > 0.10:
            reasons.append("CMF-IV bullish: low IV regime amplifies accumulation CMF.")
        elif frame.cmf_iv is not None and frame.cmf_iv < -0.10:
            reasons.append("CMF-IV bearish: distribution CMF confirmed in tradable vol regime.")
        if frame.iv_pct is not None and frame.iv_pct < 0.35:
            reasons.append("Compressed IV percentile — trend quality elevated.")
        if frame.vega_sign is not None and frame.vega_sign > 0:
            reasons.append("ATM Vega net favors calls (MM vol-up bias).")
        elif frame.vega_sign is not None and frame.vega_sign < 0:
            reasons.append("ATM Vega net favors puts (MM vol-down / hedge bias).")

    metrics = _metrics_from_frame(frame, signal=signal)
    return CmfIvScannerResult(
        ok=True,
        score=round(score, 2),
        bias=bias,
        signal=signal,
        confidence=round(confidence, 3),
        engine_status=status,
        metrics=metrics,
        reasons=reasons[:4],
        iv_crush_active=bool(frame.iv_crush_active) if frame is not None else False,
    )


def attach_cmf_iv_deep_metrics(
    deep_metrics: dict[str, dict[str, Any]] | None,
    timeframe: str,
    result: CmfIvScannerResult,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = dict(deep_metrics or {})
    bucket = dict(out.get(timeframe) or {})
    bucket.update(result.metrics)
    bucket["cmf_iv_engine_status"] = result.engine_status
    bucket["cmf_iv_score"] = result.score
    bucket["iv_crush_active"] = int(result.iv_crush_active)
    out[timeframe] = bucket
    return out


def apply_cmf_iv_score_adjustment(
    base_score: float,
    result: CmfIvScannerResult | None,
    *,
    weight: float,
) -> tuple[float, list[str]]:
    if result is None or not result.ok or weight <= 0:
        return base_score, []
    if result.iv_crush_active:
        adjusted = float(np.clip(base_score - 4.0, 0.0, 100.0))
        return adjusted, ["IV crush filter — CMF-IV score penalized."]
    delta = (result.score - 50.0) * min(weight, 5.0) / 5.0 * 0.28
    adjusted = float(np.clip(base_score + delta, 0.0, 100.0))
    return adjusted, list(result.reasons)


def _neutral_result(warning: str) -> CmfIvScannerResult:
    return CmfIvScannerResult(
        ok=False,
        score=50.0,
        bias="neutral",
        signal=0,
        confidence=0.0,
        engine_status="fallback",
        metrics={
            "cmf": None,
            "iv_pct": None,
            "iv_pct_norm": None,
            "vega_net": None,
            "vega_sign": None,
            "cmf_iv": None,
            "cmf_iv_signal": 0,
            "iv_crush_filter": 0,
        },
        reasons=[warning],
    )


def _metrics_from_frame(frame: CmfIvFrame | None, *, signal: int) -> dict[str, float | int | None]:
    if frame is None:
        return {
            "cmf": None,
            "iv_pct": None,
            "iv_pct_norm": None,
            "vega_net": None,
            "vega_sign": None,
            "cmf_iv": None,
            "cmf_iv_signal": signal,
            "iv_crush_filter": 0,
        }
    return {
        "cmf": frame.cmf,
        "iv_pct": frame.iv_pct,
        "iv_pct_norm": frame.iv_pct_norm,
        "vega_net": frame.vega_net,
        "vega_sign": int(frame.vega_sign) if frame.vega_sign is not None else None,
        "cmf_iv": frame.cmf_iv,
        "cmf_iv_signal": signal,
        "iv_crush_filter": int(frame.iv_crush_active),
    }


def _build_volatility_frame(
    snapshot: object | None,
    *,
    spot: float,
    bar_timestamps: pd.Series,
) -> pd.DataFrame | None:
    payload = _snapshot_dict(snapshot)
    if not payload:
        return None

    rows: list[dict[str, object]] = []

    explicit = payload.get("cmf_iv_vol_snapshots")
    if isinstance(explicit, list):
        for item in explicit:
            parsed = _parse_vol_row(item)
            if parsed is not None:
                rows.append(parsed)

    if not rows:
        rows.extend(_history_vol_rows(payload))

    if not rows:
        current = _vol_row_from_snapshot(payload, spot=spot)
        if current is not None and not bar_timestamps.empty:
            rows.append({**current, "timestamp": pd.Timestamp(bar_timestamps.iloc[-1])})

    if not rows:
        chain_row = _aggregate_chain_vol(payload, spot=spot)
        if chain_row is not None and not bar_timestamps.empty:
            rows.append({**chain_row, "timestamp": pd.Timestamp(bar_timestamps.iloc[-1])})

    if not rows:
        return None

    frame = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return frame.drop_duplicates(subset=["timestamp"], keep="last")


def _parse_vol_row(item: object) -> dict[str, object] | None:
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
    iv_atm = _finite_float(item.get("iv_atm"))
    if iv_atm is None:
        return None
    history = item.get("iv_30d_history")
    if not isinstance(history, list):
        history = _synthetic_iv_history(iv_atm)
    vega_calls = _finite_float(item.get("vega_calls")) or 0.0
    vega_puts = _finite_float(item.get("vega_puts")) or 0.0
    threshold = _finite_float(item.get("vega_min_threshold")) or _DEFAULT_VEGA_THRESHOLD
    return {
        "timestamp": ts,
        "iv_atm": iv_atm,
        "iv_30d_history": history,
        "vega_calls": vega_calls,
        "vega_puts": vega_puts,
        "vega_min_threshold": threshold,
    }


def _history_vol_rows(payload: dict[str, Any]) -> list[dict[str, object]]:
    history = payload.get("chain_analytics_history")
    if not isinstance(history, dict):
        return []
    points = history.get("points")
    if not isinstance(points, list):
        return []
    iv_hist_global = _iv_history_from_surface(payload)
    out: list[dict[str, object]] = []
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
        iv_atm = _finite_float(metrics.get("atm_iv") or metrics.get("iv_atm"))
        if iv_atm is None:
            continue
        hist = iv_hist_global or _synthetic_iv_history(iv_atm)
        vega_c = _finite_float(metrics.get("vega_calls")) or 0.0
        vega_p = _finite_float(metrics.get("vega_puts")) or 0.0
        out.append(
            {
                "timestamp": ts,
                "iv_atm": iv_atm,
                "iv_30d_history": hist,
                "vega_calls": vega_c,
                "vega_puts": vega_p,
                "vega_min_threshold": _DEFAULT_VEGA_THRESHOLD,
            }
        )
    return out


def _vol_row_from_snapshot(payload: dict[str, Any], *, spot: float) -> dict[str, object] | None:
    iv_surface = payload.get("iv_surface")
    if isinstance(iv_surface, dict):
        iv_atm = _finite_float(iv_surface.get("atm_iv"))
        iv_pct = _finite_float(
            iv_surface.get("iv_percentile_cross_term")
            or iv_surface.get("iv_rank_cross_expiry")
            or iv_surface.get("iv_rank_hv_rolling"),
        )
        if iv_atm is not None:
            hist = _iv_history_from_surface(payload) or _synthetic_iv_history(iv_atm)
            if iv_pct is not None and math.isfinite(iv_pct):
                hist = _synthetic_iv_history(iv_atm, center_pct=iv_pct)
            vega = _aggregate_chain_vega(payload, spot=spot)
            return {
                "iv_atm": iv_atm,
                "iv_30d_history": hist,
                "vega_calls": vega["vega_calls"] if vega else 0.0,
                "vega_puts": vega["vega_puts"] if vega else 0.0,
                "vega_min_threshold": _DEFAULT_VEGA_THRESHOLD,
            }

    features = payload.get("options_gex_features")
    if isinstance(features, dict):
        iv_atm = _finite_float(features.get("atm_iv") or features.get("iv_atm"))
        if iv_atm is not None:
            vega_c = _finite_float(features.get("vega_calls")) or 0.0
            vega_p = _finite_float(features.get("vega_puts")) or 0.0
            return {
                "iv_atm": iv_atm,
                "iv_30d_history": _synthetic_iv_history(iv_atm),
                "vega_calls": vega_c,
                "vega_puts": vega_p,
                "vega_min_threshold": _DEFAULT_VEGA_THRESHOLD,
            }

    return _aggregate_chain_vol(payload, spot=spot)


def _aggregate_chain_vol(payload: dict[str, Any], *, spot: float) -> dict[str, object] | None:
    chain = payload.get("chain")
    if not isinstance(chain, list) or spot <= 0:
        return None

    vega_calls = 0.0
    vega_puts = 0.0
    iv_samples: list[float] = []

    for leg in chain:
        if not isinstance(leg, dict):
            continue
        strike = _finite_float(leg.get("strike"))
        if strike is None or abs(strike - spot) / spot > _ATM_BAND_PCT:
            continue
        opt_type = str(leg.get("option_type") or leg.get("type") or "").lower()
        oi = _finite_float(leg.get("open_interest")) or 0.0
        if opt_type == "call":
            vega = _finite_float(leg.get("call_vega") or leg.get("vega")) or 0.0
            iv = _finite_float(leg.get("call_iv") or leg.get("implied_volatility"))
            vega_calls += abs(vega) * oi * _CONTRACT_MULT
            if iv is not None:
                iv_samples.append(iv)
        elif opt_type == "put":
            vega = _finite_float(leg.get("put_vega") or leg.get("vega")) or 0.0
            iv = _finite_float(leg.get("put_iv") or leg.get("implied_volatility"))
            vega_puts += abs(vega) * oi * _CONTRACT_MULT
            if iv is not None:
                iv_samples.append(iv)

    if not iv_samples and vega_calls <= 0 and vega_puts <= 0:
        return None

    iv_atm = float(np.mean(iv_samples)) if iv_samples else 0.28
    return {
        "iv_atm": iv_atm,
        "iv_30d_history": _synthetic_iv_history(iv_atm),
        "vega_calls": vega_calls,
        "vega_puts": vega_puts,
        "vega_min_threshold": _DEFAULT_VEGA_THRESHOLD,
    }


def _aggregate_chain_vega(payload: dict[str, Any], *, spot: float) -> dict[str, float] | None:
    row = _aggregate_chain_vol(payload, spot=spot)
    if row is None:
        return None
    return {
        "vega_calls": float(row["vega_calls"]),
        "vega_puts": float(row["vega_puts"]),
    }


def _iv_history_from_surface(payload: dict[str, Any]) -> list[float] | None:
    iv_surface = payload.get("iv_surface")
    if not isinstance(iv_surface, dict):
        return None
    points = iv_surface.get("term_structure") or iv_surface.get("points")
    if not isinstance(points, list):
        skews = iv_surface.get("expiry_skews")
        if isinstance(skews, list):
            ivs = [
                float(_finite_float(s.get("atm_iv")))
                for s in skews
                if isinstance(s, dict) and _finite_float(s.get("atm_iv")) is not None
            ]
            return ivs if len(ivs) >= 2 else None
        return None
    ivs: list[float] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        val = _finite_float(point.get("atm_iv") or point.get("iv"))
        if val is not None:
            ivs.append(val)
    return ivs if len(ivs) >= 2 else None


def _synthetic_iv_history(iv_atm: float, *, center_pct: float | None = None) -> list[float]:
    """Fallback 30d band when vendor history is missing."""
    base = max(iv_atm, 0.05)
    if center_pct is not None and math.isfinite(center_pct):
        lo = base * (0.7 + 0.2 * (1.0 - center_pct))
        hi = base * (1.1 + 0.4 * center_pct)
    else:
        lo = base * 0.75
        hi = base * 1.25
    return [float(x) for x in np.linspace(lo, hi, 30)]
