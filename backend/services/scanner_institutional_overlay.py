"""Build institutional overlay payloads for market scanner rows (application service)."""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np

from backend.domain.market_scanner_models import GexPressureLevel, ScannerInstitutionalOverlay
from backend.layer_2_quant_engine.math_core.vpin_proxy import compute_ofi_proxy, compute_vpin_proxy


def _as_dict(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _float(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        if not math.isfinite(float(v)):
            return None
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def build_overlay_from_options_snapshot(
    snapshot: object | None,
    greek_flow: dict[str, Any] | None = None,
) -> ScannerInstitutionalOverlay | None:
    """Derive GEX pressure strip + key levels from options snapshot (same shape as options router)."""
    payload = _as_dict(snapshot)
    greek = _as_dict(greek_flow)
    if not payload and not greek:
        return None

    gex = _as_dict(payload.get("gex_levels"))
    chain_raw = payload.get("chain")
    chain = chain_raw if isinstance(chain_raw, list) else []

    strike_map: dict[float, dict[str, float]] = {}
    for raw_row in chain[:800]:
        row = raw_row.model_dump(mode="json") if hasattr(raw_row, "model_dump") else raw_row
        if not isinstance(row, dict):
            continue
        strike = _float(row.get("strike"))
        if strike is None:
            continue
        cg = _float(row.get("call_gex")) or 0.0
        pg = _float(row.get("put_gex")) or 0.0
        bucket = strike_map.setdefault(strike, {"call": 0.0, "put": 0.0})
        bucket["call"] += cg
        bucket["put"] += pg

    pressure: list[GexPressureLevel] = []
    for strike, parts in strike_map.items():
        cg = parts["call"]
        pg = parts["put"]
        pressure.append(
            GexPressureLevel(
                strike=strike,
                net_gex=cg + pg,
                call_gex=cg,
                put_gex=pg,
            )
        )
    pressure.sort(key=lambda x: abs(x.net_gex), reverse=True)
    pressure = pressure[:48]
    pressure.sort(key=lambda x: x.strike)
    greek_pressure = _greek_pressure_levels(greek)
    if greek_pressure:
        pressure = greek_pressure

    iv_surface = _as_dict(payload.get("iv_surface"))
    term = _as_dict(iv_surface.get("term_structure"))

    micro: dict[str, float | str | bool | None] = {}
    mc = _as_dict(payload.get("microstructure_confluence"))
    if mc:
        micro["microstructure_confluence_score"] = _float(mc.get("score"))
        micro["microstructure_signal"] = str(mc.get("signal") or "")

    spot = _float(payload.get("spot"))
    if spot is None:
        spot = _float(greek.get("spot"))
    greek_source_tier = str(greek.get("source_tier") or "").strip() or None
    greek_status: Literal["available", "degraded", "unavailable"] = "unavailable"
    if greek_source_tier == "degraded":
        greek_status = "degraded"
    elif greek_source_tier:
        greek_status = "available"

    return ScannerInstitutionalOverlay(
        snapshot_ok=bool(payload.get("ok", True)),
        spot=spot,
        gamma_flip=_float(greek.get("gamma_flip")) or _float(gex.get("zero_gamma_level")),
        net_gex_total=_float(gex.get("net_gex_total")),
        dealer_bias=str(gex.get("dealer_bias") or "") or None,
        call_wall=_float(greek.get("call_wall")) or _float(gex.get("call_wall")),
        put_wall=_float(greek.get("put_wall")) or _float(gex.get("put_wall")),
        zero_gamma_distance_pct=_float(greek.get("zero_gamma_distance_pct")),
        net_vanna_exposure=_float(greek.get("net_vanna_exposure")),
        net_charm_exposure=_float(greek.get("net_charm_exposure")),
        greek_flow_status=greek_status,
        greek_flow_source_tier=greek_source_tier,
        greek_flow_data_quality_score=_float(greek.get("data_quality_score")),
        greek_flow_missing_components=(
            [str(item) for item in greek.get("missing_components", []) if item]
            if isinstance(greek.get("missing_components"), list)
            else []
        ),
        pressure_by_strike=pressure,
        microstructure=micro,
        iv_term_structure=term,
    )


def _greek_pressure_levels(greek_flow: dict[str, Any]) -> list[GexPressureLevel]:
    raw_levels = greek_flow.get("pressure_by_strike")
    levels = raw_levels if isinstance(raw_levels, list) else []
    pressure: list[GexPressureLevel] = []
    for raw in levels[:64]:
        row = _as_dict(raw)
        strike = _float(row.get("strike"))
        if strike is None:
            continue
        net_gamma = _float(row.get("net_gamma_exposure")) or 0.0
        call_gamma = _float(row.get("call_gamma_exposure")) or 0.0
        put_gamma = _float(row.get("put_gamma_exposure")) or 0.0
        pressure.append(
            GexPressureLevel(
                strike=strike,
                net_gex=net_gamma,
                call_gex=call_gamma,
                put_gex=put_gamma,
                net_gamma_exposure=net_gamma,
                call_gamma_exposure=call_gamma,
                put_gamma_exposure=put_gamma,
                open_interest=_float(row.get("open_interest")),
            )
        )
    return pressure


def merge_microstructure_from_bingx_bundle(
    overlay: ScannerInstitutionalOverlay | None,
    bundle: dict[str, Any],
) -> ScannerInstitutionalOverlay | None:
    """Augment overlay with real BingX trade tape + L2 metrics."""
    if not bundle or not bundle.get("ok"):
        return overlay

    base = overlay or ScannerInstitutionalOverlay()
    merged = base.model_copy(deep=True)
    merged.microstructure = {
        **merged.microstructure,
        "vpin": bundle.get("vpin"),
        "vpin_real": bundle.get("vpin"),
        "volume_imbalance": bundle.get("volume_imbalance"),
        "order_flow_cvd": bundle.get("cvd"),
        "ofi_real": bundle.get("period_delta"),
        "l2_spread": bundle.get("l2_spread"),
        "l2_imbalance": bundle.get("l2_imbalance"),
        "volume_profile_poc": bundle.get("poc_price"),
        "volume_profile_vah": bundle.get("vah_price"),
        "volume_profile_val": bundle.get("val_price"),
        "vpin_method": str(bundle.get("method_vpin") or "bingx_trade_l2_v1"),
        "microstructure_source": "bingx_trade_l2_v1",
        "microstructure_proxy_note": (
            "Real BingX trade tape + L2 depth; replaces OHLCV-only VPIN/CVD proxy when available."
        ),
    }
    return merged


def merge_microstructure_from_bars(
    overlay: ScannerInstitutionalOverlay | None,
    bars: list[dict[str, Any]],
) -> ScannerInstitutionalOverlay | None:
    """Augment overlay VPIN/OFI proxies from OHLCV (Layer 2 math)."""
    if not bars or len(bars) < 30:
        return overlay

    clean: list[dict[str, Any]] = [b for b in bars if isinstance(b, dict)]
    if len(clean) < 30:
        return overlay

    close = np.asarray([float(b.get("close") or 0) for b in clean], dtype=np.float64)
    high = np.asarray([float(b.get("high") or 0) for b in clean], dtype=np.float64)
    low = np.asarray([float(b.get("low") or 0) for b in clean], dtype=np.float64)
    vol = np.asarray([float(b.get("volume") or 0) for b in clean], dtype=np.float64)

    vpin_bundle = compute_vpin_proxy(close, high, low, vol)
    ofi = compute_ofi_proxy(close, vol)

    base = overlay or ScannerInstitutionalOverlay()
    merged = base.model_copy(deep=True)
    merged.microstructure = {
        **merged.microstructure,
        "vpin_proxy": vpin_bundle.get("vpin_proxy"),
        "volume_imbalance": vpin_bundle.get("volume_imbalance"),
        "ofi_proxy": ofi,
        "vpin_method": str(vpin_bundle.get("method") or ""),
        "footprint_pressure_proxy": ofi,
        "dark_pool_flow_proxy": vpin_bundle.get("vpin_proxy"),
        "microstructure_proxy_note": (
            "OHLCV-only footprint / flow proxies (no ATS tape); OFI z-score and VPIN-style toxicity."
        ),
    }
    return merged


def attach_institutional_overlay(
    snapshot: object | None,
    bars_primary_tf: list[dict[str, Any]] | None,
    greek_flow: dict[str, Any] | None = None,
) -> ScannerInstitutionalOverlay | None:
    """Combine snapshot GEX strip with bar-derived microstructure proxies."""
    base = build_overlay_from_options_snapshot(snapshot, greek_flow)
    return merge_microstructure_from_bars(base, bars_primary_tf or [])


def correlation_matrix_from_sparklines(
    rows: list[Any], max_symbols: int = 6
) -> dict[str, Any] | None:
    """Pearson correlation of log-return sequences from row sparklines."""
    leaders = rows[:max_symbols]
    series_list: list[np.ndarray] = []
    symbols: list[str] = []
    for row in leaders:
        sym = str(getattr(row, "symbol", "") or "")
        sl = getattr(row, "sparkline", None) or []
        if not sym or not isinstance(sl, list) or len(sl) < 8:
            continue
        arr = np.asarray([float(x) for x in sl if x is not None], dtype=np.float64)
        if arr.size < 8:
            continue
        ret = np.diff(np.log(np.clip(arr, 1e-9, None)))
        if ret.size < 4:
            continue
        series_list.append(ret)
        symbols.append(sym)

    n = len(series_list)
    if n < 2:
        return None

    min_len = min(len(s) for s in series_list)
    if min_len < 3:
        return None

    trimmed = [s[-min_len:] for s in series_list]
    mat: list[list[float | None]] = []
    for i in range(n):
        row_vals: list[float | None] = []
        for j in range(n):
            if i == j:
                row_vals.append(1.0)
                continue
            a, b = trimmed[i], trimmed[j]
            if np.std(a) < 1e-12 or np.std(b) < 1e-12:
                row_vals.append(None)
                continue
            corr = float(np.corrcoef(a, b)[0, 1])
            if not math.isfinite(corr):
                row_vals.append(None)
            else:
                row_vals.append(round(corr, 3))
        mat.append(row_vals)

    return {"symbols": symbols, "matrix": mat}
