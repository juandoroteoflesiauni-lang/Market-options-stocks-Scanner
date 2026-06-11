"""Ensamblado de payload para terminal técnico avanzado (gráfico + SMC + fractales).

Este módulo orquesta el análisis técnico combinando:
- Datos OHLCV desde un repositorio (Domain Layer)
- Indicadores técnicos (TechnicalMath)
- Análisis SMC (Smart Money Concepts)
- Análisis fractal con entropía de Shannon

Clean Architecture:
- Depende de PriceRepository (abstracción), no de FMPClient (implementación)
- Permite inyección de dependencias para testing

Performance (HFT):
- Usa ThreadPoolExecutor para operaciones CPU-bound
- No bloquea el event loop de asyncio
- Permite concurrencia real en múltiples requests
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.domain.fmp_models import FMPHistoricalPrice
from backend.domain.repositories.price_repository import PriceRepository
from backend.layer_3_specialists.tecnico.candle_geometry_engine import (
    analyze_candle_geometry_from_ohlcv,
)
from backend.layer_3_specialists.tecnico.fvg_engine import analyze_fvg_from_ohlcv
from backend.layer_3_specialists.tecnico.hmm_engine import analyze_hmm_regime_from_ohlcv
from backend.layer_3_specialists.tecnico.lob_dynamics_engine import unavailable_lob_dynamics_payload
from backend.layer_3_specialists.tecnico.market_structure_engine import (
    analyze_market_structure_from_ohlcv,
)
from backend.layer_3_specialists.tecnico.ofi_engine import OFIEngine
from backend.layer_3_specialists.tecnico.order_flow_delta_engine import (
    analyze_order_flow_delta_from_ohlcv,
)
from backend.layer_3_specialists.tecnico.single_prints import (
    SinglePrintConfig,
    scan_single_prints_from_tpo_profile,
)
from backend.layer_3_specialists.tecnico.smc import SMCEngine
from backend.layer_3_specialists.tecnico.smc_fractal_engine import SMCFractalEngine
from backend.layer_3_specialists.tecnico.tpo_skewness import TPOSkewnessConfig, TPOSkewnessEngine
from backend.layer_3_specialists.tecnico.volume import DeltaVolumeProfile
from backend.layer_3_specialists.tecnico.volume_node_engine import analyze_volume_nodes_from_ohlcv
from backend.layer_3_specialists.tecnico.volume_profile import VolumeProfileEngine
from backend.layer_3_specialists.tecnico.vpoc_migration import VPOCMigrationEngine
from backend.layer_3_specialists.tecnico.vsa import VSAEngine
from backend.layer_3_specialists.tecnico.vsa_footprint_engine import VSAFootprintEngine
from backend.layer_3_specialists.tecnico.vwap_engine import analyze_vwap_from_ohlcv
from backend.quant_engine.math.technical.technical import AVWAPMath, TechnicalMath
from backend.utils.async_executor import run_cpu_bound
from backend.utils.numpy_pool import allocate_technical_arrays, release_technical_arrays

# Importación diferida para evitar circular imports
logger = get_logger(__name__)

_TIMEFRAME_ALIASES: dict[str, str] = {
    "1s": "1s",
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1D": "1d",
    "1w": "1w",
    "1W": "1w",
    "1S": "1w",
}

_INTRADAY_TIMEFRAMES = {"1s", "1m", "5m", "15m", "30m", "1h", "4h"}

_TECHNICAL_MAX_BARS: dict[str, int] = {
    "1s": 8_000,
    "1m": 12_000,
    "5m": 12_000,
    "15m": 12_000,
    "30m": 12_000,
    "1h": 12_000,
    "4h": 12_000,
}

_HEAVY_ENGINE_MAX_BARS: dict[str, int] = {
    "1s": 2_000,
    "1m": 3_000,
    "5m": 4_000,
    "15m": 4_000,
    "30m": 4_000,
    "1h": 5_000,
    "4h": 5_000,
    "1d": 5_000,
    "1w": 2_000,
}


def _normalize_timeframe(timeframe: str) -> str:
    raw = str(timeframe or "1d").strip()
    return _TIMEFRAME_ALIASES.get(raw, _TIMEFRAME_ALIASES.get(raw.lower(), "1d"))


def _heavy_engine_frame(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Return the recent window used by optional CPU-heavy technical engines."""
    max_bars = _HEAVY_ENGINE_MAX_BARS.get(timeframe, 4_000)
    if len(df) <= max_bars:
        return df
    return df.tail(max_bars).reset_index(drop=True)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _engine_status(
    enabled: bool, ok: bool = False, error: str | None = None, **extra: object
) -> dict[str, Any]:
    out: dict[str, Any] = {"enabled": enabled, "ok": ok, "error": error}
    out.update(extra)
    return out


def _technical_flags() -> dict[str, bool]:
    """Runtime switches for the technical terminal engines."""
    return {
        "enable_composite_repo": _env_bool("DATA_ENABLE_COMPOSITE_PRICE_REPO", default=True),
        "enable_volume_engines": _env_bool("TECHNICAL_ENABLE_VOLUME_ENGINES", default=True),
        "enable_fvg_engine": _env_bool("TECHNICAL_ENABLE_FVG_ENGINE", default=True),
        "enable_structure_engines": _env_bool("TECHNICAL_ENABLE_STRUCTURE_ENGINES", default=True),
        "enable_order_flow_delta": _env_bool("TECHNICAL_ENABLE_ORDER_FLOW_DELTA", default=True),
        "enable_lob_dynamics": _env_bool("TECHNICAL_ENABLE_LOB_DYNAMICS", default=True),
        "enable_hmm_engine": _env_bool("TECHNICAL_ENABLE_HMM_ENGINE", default=True),
        "enable_footprint_engine": _env_bool("TECHNICAL_ENABLE_FOOTPRINT_ENGINE", default=True),
    }


def _initial_engine_status(flags: dict[str, bool]) -> dict[str, Any]:
    """Build the visible engine-status contract used by the terminal."""
    enable_volume_engines = flags["enable_volume_engines"]
    enable_lob_dynamics = flags["enable_lob_dynamics"]
    return {
        "data_provider": _engine_status(True, False, provider=None),
        "candle_geometry": _engine_status(flags["enable_structure_engines"]),
        "market_structure": _engine_status(flags["enable_structure_engines"]),
        "order_flow_delta": _engine_status(flags["enable_order_flow_delta"]),
        "lob_dynamics": _engine_status(
            enable_lob_dynamics,
            False,
            "L2 order-book feed not configured",
        ),
        "vsa": _engine_status(enable_volume_engines),
        "volume_profile": _engine_status(enable_volume_engines),
        "volume_nodes": _engine_status(enable_volume_engines),
        "vwap_advanced": _engine_status(enable_volume_engines),
        "delta_volume": _engine_status(enable_volume_engines),
        "vpoc_migration": _engine_status(enable_volume_engines),
        "ofi": _engine_status(enable_volume_engines),
        "fvg": _engine_status(flags["enable_fvg_engine"]),
        "tpo_skewness": _engine_status(enable_volume_engines),
        "single_prints": _engine_status(enable_volume_engines),
        "hmm_regime": _engine_status(flags["enable_hmm_engine"]),
        "vsa_footprint": _engine_status(flags["enable_footprint_engine"]),
    }


def _df_from_fmp_rows(rows: list[FMPHistoricalPrice]) -> pd.DataFrame:
    """Convierte rows de FMP a DataFrame para análisis."""
    recs: list[dict[str, Any]] = []
    for r in rows:
        if r.date is None:
            continue
        close = r.adjClose if r.adjClose is not None else r.close
        if close is None:
            continue
        recs.append(
            {
                "date": r.date,
                "open": float(r.open or close),
                "high": float(r.high or close),
                "low": float(r.low or close),
                "close": float(close),
                "volume": float(r.volume or 0.0),
            }
        )
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _df_from_intraday_bars(bars: list[dict[str, Any]]) -> pd.DataFrame:
    """Convierte barras Massive/Polygon intradia a DataFrame tecnico."""
    recs: list[dict[str, Any]] = []
    for row in bars:
        t_raw = row.get("t")
        try:
            ts_ms = int(t_raw) if t_raw is not None else 0
            open_price = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            volume = float(row.get("volume") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if ts_ms <= 0:
            continue
        recs.append(
            {
                "date": datetime.fromtimestamp(ts_ms / 1000, tz=UTC).replace(tzinfo=None),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": max(volume, 0.0),
            }
        )
    if not recs:
        return pd.DataFrame()
    return pd.DataFrame(recs).sort_values("date").reset_index(drop=True)


def _df_from_generated_candles(candles: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert generated chart candles into the technical OHLCV DataFrame shape."""
    recs: list[dict[str, Any]] = []
    for row in candles:
        t_raw = row.get("time", row.get("t"))
        try:
            ts_ms = int(t_raw) if t_raw is not None else 0
            if ts_ms > 0 and ts_ms < 1_000_000_000_000:
                ts_ms *= 1000
            open_price = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            volume = float(row.get("volume") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if ts_ms <= 0 or min(open_price, high, low, close) <= 0:
            continue
        recs.append(
            {
                "date": datetime.fromtimestamp(ts_ms / 1000, tz=UTC).replace(tzinfo=None),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": max(volume, 0.0),
            }
        )
    if not recs:
        return pd.DataFrame()
    return pd.DataFrame(recs).sort_values("date").drop_duplicates("date").reset_index(drop=True)


def _chart_times_from_df(df: pd.DataFrame, timeframe: str) -> list[str]:
    """Formato compatible con el frontend: diario YYYY-MM-DD, intradia ISO UTC."""
    if timeframe in _INTRADAY_TIMEFRAMES:
        return [pd.Timestamp(x).strftime("%Y-%m-%dT%H:%M:%SZ") for x in df["date"]]
    return [pd.Timestamp(x).strftime("%Y-%m-%d") for x in df["date"]]


def _series_to_chart(
    dates: list[str],
    arr: np.ndarray,
) -> list[dict[str, Any]]:
    """Convierte array NumPy a formato para lightweight-charts."""
    out: list[dict[str, Any]] = []
    for i, t in enumerate(dates):
        if i >= len(arr):
            break
        v = float(arr[i])
        if np.isnan(v):
            continue
        out.append({"time": t, "value": v})
    return out


def _calculate_indicators_cpu_bound(
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    v: np.ndarray,
) -> dict[str, np.ndarray]:
    """Calcula indicadores técnicos en CPU (thread-safe).

    Esta función se ejecuta en un thread separado para no bloquear asyncio.
    """
    vwap = TechnicalMath.vwap(h, lo, c, v)
    sma20 = TechnicalMath.sma(c, 20)
    sma50 = TechnicalMath.sma(c, 50)
    sma200 = TechnicalMath.sma(c, 200)
    ema21 = TechnicalMath.ema(c, 21)
    avwap, _av_std = AVWAPMath.compute_anchored(h, lo, c, v, anchor_idx=0)

    return {
        "vwap": vwap,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "ema21": ema21,
        "avwap": avwap,
    }


def _calculate_indicators_with_pool(
    tech_arrays: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Calcula indicadores usando arrays pre-asignados del pool.

    Esta función asume que los arrays ya están asignados y solo
    necesita llenar los resultados.

    Args:
        tech_arrays: Diccionario con arrays: h, lo, c, v, y arrays vacíos para resultados

    Returns:
        Mismo diccionario con arrays de resultados llenos
    """
    h = tech_arrays["h"]
    lo = tech_arrays["lo"]
    c = tech_arrays["c"]
    v = tech_arrays["v"]

    # Calcular en los arrays pre-asignados
    tech_arrays["vwap"][:] = TechnicalMath.vwap(h, lo, c, v)
    tech_arrays["sma20"][:] = TechnicalMath.sma(c, 20)
    tech_arrays["sma50"][:] = TechnicalMath.sma(c, 50)
    tech_arrays["sma200"][:] = TechnicalMath.sma(c, 200)
    tech_arrays["ema21"][:] = TechnicalMath.ema(c, 21)
    tech_arrays["avwap"][:] = AVWAPMath.compute_anchored(h, lo, c, v, anchor_idx=0)[0]

    return tech_arrays


def _analyze_smc_cpu_bound(df: pd.DataFrame, symbol: str, timeframe: str) -> tuple[Any, Any]:
    """Ejecuta análisis SMC y fractal en CPU (thread-safe)."""
    smc = SMCEngine().analyze(df, ticker=symbol, timeframe=timeframe)
    fract = SMCFractalEngine().analyze(df, symbol)
    return smc, fract


def _technical_volume_engines_cpu_bound(
    df: pd.DataFrame, symbol: str, timeframe: str
) -> dict[str, Any]:
    """Run optional volume engines and return compact JSON-safe summaries."""
    frame = df.copy()
    if "date" in frame.columns:
        frame = frame.set_index(pd.to_datetime(frame["date"]))
        frame.index.name = None

    out: dict[str, Any] = {}
    try:
        vsa = VSAEngine().analyze(frame, ticker=symbol, timeframe=timeframe)
        labels = [getattr(lbl, "value", str(lbl)) for lbl in getattr(vsa, "recent_labels", [])]
        out["vsa"] = {
            "enabled": True,
            "ok": bool(getattr(vsa, "ok", False)),
            "error": getattr(vsa, "error", None),
            "signal": getattr(
                getattr(vsa, "signal", None), "value", str(getattr(vsa, "signal", ""))
            ),
            "recent_labels": labels,
            "composite_score": getattr(vsa, "composite_score", None),
            "rvol": getattr(vsa, "rvol", None),
            "buy_absorption": getattr(vsa, "buy_absorption", None),
            "sell_absorption": getattr(vsa, "sell_absorption", None),
            "long_signal_active": getattr(vsa, "long_signal_active", None),
            "vfi_value": getattr(vsa, "vfi_value", None),
            "vfi_slope": getattr(vsa, "vfi_slope", None),
            "cvd_last": getattr(vsa, "cvd_last", None),
            "cvd_slope": getattr(vsa, "cvd_slope", None),
        }
    except Exception as exc:
        out["vsa"] = {"enabled": True, "ok": False, "error": str(exc)}

    try:
        vp = VolumeProfileEngine.calculate(frame)
        out["volume_profile"] = {
            "enabled": True,
            "ok": bool(getattr(vp, "ok", False)),
            "error": getattr(vp, "error", None),
            "poc": getattr(vp, "poc", None),
            "vah": getattr(vp, "vah", None),
            "val": getattr(vp, "val", None),
            "avwap": getattr(vp, "avwap", None),
            "avwap_anchor_date": getattr(vp, "avwap_anchor_date", None),
            "is_above_avwap": getattr(vp, "is_above_avwap", None),
            "is_above_poc": getattr(vp, "is_above_poc", None),
            "volume_bias": getattr(vp, "volume_bias", None),
        }
    except Exception as exc:
        out["volume_profile"] = {"enabled": True, "ok": False, "error": str(exc)}

    try:
        volume_nodes = analyze_volume_nodes_from_ohlcv(frame)
        out["volume_nodes"] = {
            "enabled": True,
            "ok": bool(volume_nodes.ok),
            "error": volume_nodes.error,
            "timestamp": volume_nodes.timestamp,
            "poc_price": volume_nodes.poc_price,
            "last_close": volume_nodes.last_close,
            "node_count": volume_nodes.node_count,
            "hvn_count": volume_nodes.hvn_count,
            "lvn_count": volume_nodes.lvn_count,
            "nearest_hvn_above": (
                volume_nodes.nearest_hvn_above.model_dump(mode="json")
                if volume_nodes.nearest_hvn_above
                else None
            ),
            "nearest_hvn_below": (
                volume_nodes.nearest_hvn_below.model_dump(mode="json")
                if volume_nodes.nearest_hvn_below
                else None
            ),
            "nearest_lvn_above": (
                volume_nodes.nearest_lvn_above.model_dump(mode="json")
                if volume_nodes.nearest_lvn_above
                else None
            ),
            "nearest_lvn_below": (
                volume_nodes.nearest_lvn_below.model_dump(mode="json")
                if volume_nodes.nearest_lvn_below
                else None
            ),
            "nodes": [node.model_dump(mode="json") for node in volume_nodes.nodes],
        }
    except Exception as exc:
        out["volume_nodes"] = {"enabled": True, "ok": False, "error": str(exc)}

    try:
        vwap = analyze_vwap_from_ohlcv(frame)
        snapshot = vwap.snapshot
        out["vwap_advanced"] = {
            "enabled": True,
            "ok": bool(vwap.ok),
            "error": vwap.error,
            "current_vwap": snapshot.current_vwap if snapshot else None,
            "standard_deviation": snapshot.standard_deviation if snapshot else None,
            "bands": snapshot.bands.model_dump(mode="json") if snapshot else None,
            "cumulative_volume": snapshot.cumulative_volume if snapshot else None,
            "tick_count": snapshot.tick_count if snapshot else 0,
            "last_close": vwap.last_close,
            "price_vs_vwap": vwap.price_vs_vwap,
            "price_zscore": vwap.price_zscore,
            "above_vwap": vwap.above_vwap,
            "history": vwap.history,
        }
    except Exception as exc:
        out["vwap_advanced"] = {"enabled": True, "ok": False, "error": str(exc)}

    try:
        delta = DeltaVolumeProfile.compute(frame)
        out["delta_volume"] = {
            "enabled": True,
            "ok": bool(getattr(delta, "ok", False)),
            "error": getattr(delta, "error", None),
            "poc_price": getattr(delta, "poc_price", None),
            "poc_delta_bias": getattr(getattr(delta, "poc_delta_bias", None), "value", None),
            "delta_skew": getattr(delta, "delta_skew", None),
            "total_bull": getattr(delta, "total_bull", None),
            "total_bear": getattr(delta, "total_bear", None),
            "nodes": [
                {
                    "price": node.price,
                    "net_delta": node.net_delta,
                    "delta_bias": node.delta_bias.value,
                    "bin_index": node.bin_index,
                }
                for node in list(getattr(delta, "nodes", ()))[-25:]
            ],
        }
    except Exception as exc:
        out["delta_volume"] = {"enabled": True, "ok": False, "error": str(exc)}

    try:
        vpoc_signal = VPOCMigrationEngine().build_rolling_signal(frame, window_size=3)
        current_profile = vpoc_signal.profiles[-1] if vpoc_signal.profiles else None
        out["vpoc_migration"] = {
            "enabled": True,
            "ok": bool(vpoc_signal.ok),
            "error": vpoc_signal.error,
            "state": vpoc_signal.state.value,
            "current_poc": vpoc_signal.current_poc,
            "reference_poc": vpoc_signal.reference_poc,
            "poc_delta": vpoc_signal.poc_delta,
            "value_area_width_delta": vpoc_signal.value_area_width_delta,
            "value_area_midpoint_delta": vpoc_signal.value_area_midpoint_delta,
            "window_count": vpoc_signal.window_count,
            "current_value_area_high": current_profile.value_area_high if current_profile else None,
            "current_value_area_low": current_profile.value_area_low if current_profile else None,
            "value_area_coverage": current_profile.value_area_coverage if current_profile else None,
        }
    except Exception as exc:
        out["vpoc_migration"] = {"enabled": True, "ok": False, "error": str(exc)}

    try:
        ofi = OFIEngine().analyze_ohlcv_proxy(frame)
        out["ofi"] = {
            "enabled": True,
            "ok": bool(ofi.ok),
            "error": ofi.error,
            "regime": ofi.regime.value,
            "latest_raw_ofi": ofi.latest_raw_ofi,
            "latest_accumulated_ofi": ofi.latest_accumulated_ofi,
            "latest_delta_bid": ofi.latest_delta_bid,
            "latest_delta_ask": ofi.latest_delta_ask,
            "window_tick_count": ofi.window_tick_count,
            "history": [
                {
                    "timestamp": row.timestamp,
                    "raw_ofi": row.raw_ofi,
                    "accumulated_ofi": row.accumulated_ofi,
                    "regime": row.regime.value,
                }
                for row in ofi.history[-60:]
            ],
        }
    except Exception as exc:
        out["ofi"] = {"enabled": True, "ok": False, "error": str(exc)}

    try:
        tpo_engine = TPOSkewnessEngine(
            symbol,
            TPOSkewnessConfig(compact_level_limit=2500),
        )
        tpo_engine.ingest_frame(frame)
        tpo = tpo_engine.evaluate()
        snapshot = tpo.snapshot
        out["tpo_skewness"] = {
            "enabled": True,
            "ok": bool(tpo.ok),
            "error": tpo.error,
            "skewness_value": tpo.skewness_value,
            "profile_shape": tpo.profile_shape.value,
            "tick_size": tpo.tick_size,
            "bracket_count": tpo.bracket_count,
            "is_intraday_input": tpo.is_intraday_input,
            "poc_price": snapshot.poc_price if snapshot else None,
            "mean_price": snapshot.mean_price if snapshot else None,
            "standard_deviation": snapshot.standard_deviation if snapshot else None,
            "highest_price": snapshot.highest_price if snapshot else None,
            "lowest_price": snapshot.lowest_price if snapshot else None,
            "total_tpos": snapshot.total_tpos if snapshot else 0,
            "level_count": snapshot.level_count if snapshot else 0,
            "levels": [
                {
                    "price": level.price,
                    "tpo_count": level.tpo_count,
                }
                for level in (snapshot.levels if snapshot else ())
            ],
        }
        if snapshot is None:
            out["single_prints"] = {
                "enabled": True,
                "ok": False,
                "error": tpo.error or "TPO snapshot unavailable",
                "zones": [],
                "active_count": 0,
            }
        else:
            single_prints = scan_single_prints_from_tpo_profile(
                snapshot,
                SinglePrintConfig(tick_size=tpo.tick_size),
            )
            out["single_prints"] = {
                "enabled": True,
                "ok": True,
                "error": None,
                "session_id": single_prints.session_id,
                "scanned_at": single_prints.scanned_at,
                "active_count": len(single_prints.new_zones),
                "zones": [
                    {
                        "id": zone.id,
                        "type": zone.type.value,
                        "status": zone.status.value,
                        "bottom_price": zone.bottom_price,
                        "top_price": zone.top_price,
                        "size": zone.size,
                        "source_session_id": zone.source_session_id,
                    }
                    for zone in single_prints.new_zones
                ],
            }
    except Exception as exc:
        out["tpo_skewness"] = {"enabled": True, "ok": False, "error": str(exc)}
        out["single_prints"] = {"enabled": True, "ok": False, "error": str(exc)}
    return out


def _technical_hmm_engine_cpu_bound(df: pd.DataFrame) -> dict[str, Any]:
    """Run the optional HMM regime engine and return a compact payload."""
    result = analyze_hmm_regime_from_ohlcv(df)
    return {
        "enabled": True,
        "ok": bool(result.ok),
        "error": result.error,
        "current_state": result.current_state,
        "current_label": result.current_label,
        "state_probabilities": list(result.state_probabilities),
        "transition_risk": result.transition_risk,
        "regime_signal": result.regime_signal,
        "history": [
            {
                "time": row.timestamp,
                "current_state": row.current_state,
                "current_label": row.current_label,
                "state_probabilities": list(row.state_probabilities),
                "transition_risk": row.transition_risk,
                "regime_signal": row.regime_signal,
            }
            for row in result.history[-60:]
        ],
    }


def _technical_footprint_cpu_bound(df: pd.DataFrame, symbol: str) -> dict[str, Any]:
    """Run the optional footprint engine and return a compact payload."""
    frame = df.copy()
    if "date" in frame.columns:
        frame = frame.set_index(pd.to_datetime(frame["date"]))
    footprint = VSAFootprintEngine().analyze_footprints(frame, ticker=symbol)
    return {
        "enabled": True,
        "ok": bool(getattr(footprint, "ok", False)),
        "error": getattr(footprint, "error", None),
        "nearest_support": getattr(footprint, "nearest_support", None),
        "nearest_resistance": getattr(footprint, "nearest_resistance", None),
        "active_levels": [
            {
                "price": node.price,
                "volume": node.volume,
                "bar_index": node.bar_index,
                "is_support": node.is_support,
                "is_active": node.is_active,
            }
            for node in getattr(footprint, "active_levels", [])
        ],
    }


def _technical_fvg_engine_cpu_bound(df: pd.DataFrame) -> dict[str, Any]:
    """Run the FVG engine and return a compact payload."""
    fvg = analyze_fvg_from_ohlcv(df)
    return {
        "enabled": True,
        "ok": bool(fvg.ok),
        "error": fvg.error,
        "active_count": fvg.active_count,
        "history_count": fvg.history_count,
        "bullish_active_count": fvg.bullish_active_count,
        "bearish_active_count": fvg.bearish_active_count,
        "partial_count": fvg.partial_count,
        "consequent_encroachment_count": fvg.consequent_encroachment_count,
        "iofed_count": fvg.iofed_count,
        "tick_size": fvg.tick_size,
        "min_gap_size": fvg.min_gap_size,
        "active_zones": [
            zone.model_dump(mode="json", exclude={"mitigated_at_index"})
            for zone in fvg.active_zones
        ],
        "recent_events": [
            {
                "type": event.type,
                "zone_id": event.zone.id,
                "zone_type": event.zone.type.value,
                "status": event.zone.status.value,
                "timestamp": event.candle.timestamp,
                "price_range": [event.zone.bottom_price, event.zone.top_price],
                "mitigation_pct": event.zone.mitigation_pct,
            }
            for event in fvg.recent_events
        ],
    }


def _technical_structure_engines_cpu_bound(df: pd.DataFrame) -> dict[str, Any]:
    """Run OHLCV-backed structure engines and return compact payloads."""
    candle_geometry = analyze_candle_geometry_from_ohlcv(df)
    market_structure = analyze_market_structure_from_ohlcv(df)
    return {
        "candle_geometry": candle_geometry.model_dump(mode="json"),
        "market_structure": market_structure.model_dump(mode="json"),
    }


def _technical_order_flow_delta_cpu_bound(df: pd.DataFrame) -> dict[str, Any]:
    """Run OHLCV-proxy order-flow delta engine and return a compact payload."""
    order_flow_delta = analyze_order_flow_delta_from_ohlcv(df)
    return order_flow_delta.model_dump(mode="json", by_alias=True)


# Maximum number of OHLCV bars serialised into the JSON chart payload.
# All analytical engines (SMC, HMM, VSA, FVG …) still run on the *full*
# DataFrame — only the HTTP response array is capped to limit payload size.
CHART_CANDLE_CAP: int = 1_500


async def _build_technical_payload_from_df(
    sym: str,
    df: pd.DataFrame,
    normalized_timeframe: str,
    engine_status: dict[str, Any],
    flags: dict[str, bool],
    *,
    use_cpu_offload: bool,
    candle_source: str,
    generated_candles: int | None = None,
    live_partial_bar: bool = False,
    analysis_cadence: str = "request",
    last_candle_time: int | str | None = None,
    source_meta: dict[str, Any] | None = None,
    chart_cap: int = 0,
) -> dict[str, Any]:
    """Run the complete technical terminal calculation on an already-loaded OHLCV frame."""
    enable_volume_engines = flags["enable_volume_engines"]
    enable_fvg_engine = flags["enable_fvg_engine"]
    enable_structure_engines = flags["enable_structure_engines"]
    enable_order_flow_delta = flags["enable_order_flow_delta"]
    enable_lob_dynamics = flags["enable_lob_dynamics"]
    enable_hmm_engine = flags["enable_hmm_engine"]
    enable_footprint_engine = flags["enable_footprint_engine"]

    meta_base: dict[str, Any] = {
        "candle_source": candle_source,
        "generated_candles": generated_candles,
        "live_partial_bar": live_partial_bar,
        "last_candle_time": last_candle_time,
        "analysis_cadence": analysis_cadence,
    }
    if source_meta:
        meta_base.update(source_meta)

    if df.empty:
        return {
            "ok": False,
            "symbol": sym,
            "timeframe": normalized_timeframe,
            "error": "No historical prices - verify symbol or data provider configuration.",
            "engine_status": engine_status,
            "meta": meta_base | {"bars": 0},
        }
    if len(df) < 35:
        return {
            "ok": False,
            "symbol": sym,
            "timeframe": normalized_timeframe,
            "error": f"Insufficient bars ({len(df)}); need at least 35 for SMC pipeline.",
            "engine_status": engine_status,
            "meta": meta_base | {"bars": len(df)},
        }

    bars = len(df)
    tech_arrays = allocate_technical_arrays(bars=bars, dtype=np.float64)
    tech_arrays["h"][:] = df["high"].to_numpy(dtype=np.float64)
    tech_arrays["lo"][:] = df["low"].to_numpy(dtype=np.float64)
    tech_arrays["c"][:] = df["close"].to_numpy(dtype=np.float64)
    tech_arrays["v"][:] = df["volume"].to_numpy(dtype=np.float64)

    dates = _chart_times_from_df(df, normalized_timeframe)

    try:
        if use_cpu_offload:
            indicators = await run_cpu_bound(
                _calculate_indicators_with_pool,
                tech_arrays,
                timeout=10.0,
            )
        else:
            indicators = _calculate_indicators_with_pool(tech_arrays)

        vwap = indicators["vwap"].copy()
        sma20 = indicators["sma20"].copy()
        sma50 = indicators["sma50"].copy()
        sma200 = indicators["sma200"].copy()
        ema21 = indicators["ema21"].copy()
        avwap = indicators["avwap"].copy()
    finally:
        release_technical_arrays(tech_arrays)

    if use_cpu_offload:
        smc, fract = await run_cpu_bound(
            _analyze_smc_cpu_bound,
            df,
            sym,
            normalized_timeframe,
            timeout=15.0,
        )
    else:
        smc, fract = _analyze_smc_cpu_bound(df, sym, normalized_timeframe)

    optional_payload: dict[str, Any] = {
        "lob_dynamics": unavailable_lob_dynamics_payload()
        | {
            "enabled": enable_lob_dynamics,
            "error": "L2 order-book feed not configured",
        }
    }
    heavy_df = _heavy_engine_frame(df, normalized_timeframe)
    optional_payload["engine_window"] = {
        "enabled": True,
        "ok": True,
        "source_bars": len(df),
        "analysis_bars": len(heavy_df),
        "windowed": len(heavy_df) < len(df),
        "timeframe": normalized_timeframe,
    }

    if enable_structure_engines:
        try:
            structure_payload = await run_cpu_bound(
                _technical_structure_engines_cpu_bound,
                heavy_df,
                timeout=8.0,
            )
            optional_payload.update(structure_payload)
            for key in ("candle_geometry", "market_structure"):
                block = structure_payload.get(key) or {}
                engine_status[key] = _engine_status(
                    True,
                    bool(block.get("ok")),
                    block.get("error"),
                )
        except Exception as exc:
            logger.exception("technical structure engines failed for %s: %s", sym, exc)
            for key in ("candle_geometry", "market_structure"):
                engine_status[key] = _engine_status(True, False, str(exc))

    if enable_order_flow_delta:
        try:
            order_flow_payload = await run_cpu_bound(
                _technical_order_flow_delta_cpu_bound,
                heavy_df,
                timeout=8.0,
            )
            optional_payload["order_flow_delta"] = order_flow_payload
            engine_status["order_flow_delta"] = _engine_status(
                True,
                bool(order_flow_payload.get("ok")),
                order_flow_payload.get("error"),
            )
        except Exception as exc:
            logger.exception("technical order-flow delta engine failed for %s: %s", sym, exc)
            engine_status["order_flow_delta"] = _engine_status(True, False, str(exc))

    if enable_fvg_engine:
        try:
            fvg_payload = await run_cpu_bound(
                _technical_fvg_engine_cpu_bound,
                heavy_df,
                timeout=8.0,
            )
            optional_payload["fvg"] = fvg_payload
            engine_status["fvg"] = _engine_status(
                True,
                bool(fvg_payload.get("ok")),
                fvg_payload.get("error"),
            )
        except Exception as exc:
            logger.exception("technical FVG engine failed for %s: %s", sym, exc)
            engine_status["fvg"] = _engine_status(True, False, str(exc))

    if enable_volume_engines:
        try:
            volume_payload = await run_cpu_bound(
                _technical_volume_engines_cpu_bound,
                heavy_df,
                sym,
                normalized_timeframe,
                timeout=10.0,
            )
            optional_payload.update(volume_payload)
            for key in (
                "vsa",
                "volume_profile",
                "volume_nodes",
                "vwap_advanced",
                "delta_volume",
                "ofi",
                "tpo_skewness",
                "single_prints",
            ):
                block = volume_payload.get(key) or {}
                engine_status[key] = _engine_status(
                    True,
                    bool(block.get("ok")),
                    block.get("error"),
                )
            block = volume_payload.get("vpoc_migration") or {}
            engine_status["vpoc_migration"] = _engine_status(
                True,
                bool(block.get("ok")),
                block.get("error"),
            )
        except Exception as exc:
            logger.exception("technical volume engines failed for %s: %s", sym, exc)
            for key in (
                "vsa",
                "volume_profile",
                "volume_nodes",
                "vwap_advanced",
                "delta_volume",
                "vpoc_migration",
                "ofi",
                "tpo_skewness",
                "single_prints",
            ):
                engine_status[key] = _engine_status(True, False, str(exc))

    if enable_footprint_engine:
        try:
            footprint_payload = await run_cpu_bound(
                _technical_footprint_cpu_bound,
                heavy_df,
                sym,
                timeout=8.0,
            )
            optional_payload["vsa_footprint"] = footprint_payload
            engine_status["vsa_footprint"] = _engine_status(
                True,
                bool(footprint_payload.get("ok")),
                footprint_payload.get("error"),
            )
        except Exception as exc:
            logger.exception("technical footprint engine failed for %s: %s", sym, exc)
            engine_status["vsa_footprint"] = _engine_status(True, False, str(exc))

    if enable_hmm_engine:
        try:
            hmm_payload = await run_cpu_bound(
                _technical_hmm_engine_cpu_bound,
                heavy_df,
                timeout=8.0,
            )
            optional_payload["hmm_regime"] = hmm_payload
            engine_status["hmm_regime"] = _engine_status(
                True,
                bool(hmm_payload.get("ok")),
                hmm_payload.get("error"),
            )
        except Exception as exc:
            logger.exception("technical HMM regime engine failed for %s: %s", sym, exc)
            engine_status["hmm_regime"] = _engine_status(True, False, str(exc))

    structure_markers: list[dict[str, Any]] = []
    for ev in smc.structure_events[-25:]:
        bi = ev.bar_index
        if 0 <= bi < len(dates):
            structure_markers.append(
                {
                    "time": dates[bi],
                    "event_type": ev.event_type,
                    "price": ev.level,
                }
            )

    meta = {
        "bars": len(df),
        "optional_engine_bars": len(heavy_df),
        "optional_engine_windowed": len(heavy_df) < len(df),
        "sesgo_smc": smc.sesgo.value,
        "composite_score": smc.composite_score,
    }
    meta.update(meta_base)
    if meta["last_candle_time"] is None and dates:
        meta["last_candle_time"] = dates[-1]

    # ── Chart serialisation cap ───────────────────────────────────────────────
    # Engines computed on the *full* df above.  Only the JSON chart arrays are
    # sliced to chart_cap so the HTTP response stays bounded regardless of how
    # many bars are stored in the GeneratedCandleStore.
    effective_cap = int(chart_cap) if chart_cap and chart_cap > 0 else len(df)
    chart_start = max(0, len(df) - effective_cap)
    chart_df = df.iloc[chart_start:]
    chart_dates = dates[chart_start:]
    chart_vwap = vwap[chart_start:]
    chart_sma20 = sma20[chart_start:]
    chart_sma50 = sma50[chart_start:]
    chart_sma200 = sma200[chart_start:]
    chart_ema21 = ema21[chart_start:]
    chart_avwap = avwap[chart_start:]
    if effective_cap < len(df):
        meta["chart_capped_bars"] = effective_cap
        meta["chart_cap_applied"] = True
        logger.debug(
            "technical.chart_cap symbol=%s tf=%s total_bars=%d chart_bars=%d",
            sym,
            normalized_timeframe,
            len(df),
            effective_cap,
        )

    payload = {
        "ok": True,
        "symbol": sym,
        "timeframe": normalized_timeframe,
        "as_of": datetime.now(tz=None).isoformat(),
        "candles": [
            {
                "time": chart_dates[i],
                "open": float(chart_df["open"].iloc[i]),
                "high": float(chart_df["high"].iloc[i]),
                "low": float(chart_df["low"].iloc[i]),
                "close": float(chart_df["close"].iloc[i]),
            }
            for i in range(len(chart_df))
        ],
        "overlays": {
            "vwap": _series_to_chart(chart_dates, chart_vwap),
            "sma20": _series_to_chart(chart_dates, chart_sma20),
            "sma50": _series_to_chart(chart_dates, chart_sma50),
            "sma200": _series_to_chart(chart_dates, chart_sma200),
            "ema21": _series_to_chart(chart_dates, chart_ema21),
            "avwap": _series_to_chart(chart_dates, chart_avwap),
        },
        "structure_markers": structure_markers,
        "smc": smc.model_dump(mode="json"),
        "fractal": fract.model_dump(mode="json"),
        "meta": meta,
        "engine_status": engine_status,
    }
    payload.update(optional_payload)
    return payload


async def build_technical_terminal_payload_from_candles(
    symbol: str,
    candles: list[dict[str, Any]],
    timeframe: str = "1D",
    *,
    live_partial_bar: bool = False,
    analysis_cadence: str = "generated_snapshot",
    source: str = "generated_candles",
    last_candle_time: int | str | None = None,
    use_cpu_offload: bool = True,
) -> dict[str, Any]:
    """Build the full technical terminal payload from already-generated OHLCV candles."""
    sym = symbol.upper().strip()
    normalized_timeframe = _normalize_timeframe(timeframe)
    flags = _technical_flags()
    engine_status = _initial_engine_status(flags)
    df = _df_from_generated_candles(candles)
    engine_status["data_provider"] = _engine_status(
        True,
        not df.empty,
        None if not df.empty else "generated candles unavailable",
        provider=source,
        source=source,
        count=len(df),
        interval=normalized_timeframe,
    )
    return await _build_technical_payload_from_df(
        sym,
        df,
        normalized_timeframe,
        engine_status,
        flags,
        use_cpu_offload=use_cpu_offload,
        candle_source="generated",
        generated_candles=len(df),
        live_partial_bar=live_partial_bar,
        analysis_cadence=analysis_cadence,
        last_candle_time=last_candle_time,
        source_meta={"source": source},
        chart_cap=CHART_CANDLE_CAP,
    )


async def build_technical_terminal_payload(
    symbol: str,
    days: int = 320,
    timeframe: str = "1D",
    price_repo: PriceRepository | None = None,
    use_cpu_offload: bool = True,  # Nuevo parámetro para controlar offload
) -> dict[str, Any]:
    """OHLCV + indicadores + SMC + fractal; listo para JSON y lightweight-charts.

    Args:
        symbol: Ticker del activo (ej: "AAPL", "SPY")
        days: Cantidad de días a recuperar (mínimo 60 para SMC pipeline)
        price_repo: Repositorio de precios (inyección de dependencias)
        use_cpu_offload: Si True, usa ThreadPoolExecutor para CPU-bound

    Returns:
        Diccionario con:
        - ok: bool indicando éxito
        - symbol: Ticker procesado
        - candles: Lista de OHLCV para gráfico
        - overlays: Indicadores técnicos (VWAP, MAs, AVWAP)
        - smc: Análisis SMC (BOS/CHOCH, Order Blocks, FVG)
        - fractal: Análisis fractal con entropía
        - meta: Metadatos del análisis

    Clean Architecture:
        Esta función depende de PriceRepository (abstracción de dominio),
        no de FMPClient (implementación concreta). Esto permite:
        - Testing unitario sin conexión a FMP
        - Intercambiar proveedores (FMP → Polygon) sin cambiar lógica
        - Múltiples implementaciones (ej: FMP con fallback a cache local)

    Performance (HFT):
        Los cálculos pesados (indicadores, SMC, fractales) se ejecutan
        en ThreadPoolExecutor para no bloquear el event loop de asyncio.
        Esto permite:
        - Múltiples requests concurrentes sin jitter
        - Mejor uso de CPU cores en sistemas multi-core
        - Latencia predecible en alta concurrencia
    """
    sym = symbol.upper().strip()
    normalized_timeframe = _normalize_timeframe(timeframe)
    flags = _technical_flags()
    enable_composite_repo = flags["enable_composite_repo"]
    engine_status = _initial_engine_status(flags)

    df = pd.DataFrame()
    fetch_error: str | None = None

    if normalized_timeframe in _INTRADAY_TIMEFRAMES:
        try:
            from backend.layer_1_data.datos.intraday_bars_fetcher import fetch_intraday_bars

            result = await run_cpu_bound(
                partial(
                    fetch_intraday_bars,
                    sym,
                    normalized_timeframe,
                    max_bars=_TECHNICAL_MAX_BARS[normalized_timeframe],
                    lookback_days=max(days, 1),
                ),
                timeout=45.0,
            )
            df = _df_from_intraday_bars(list(result.get("bars") or []))
            if not df.empty:
                engine_status["data_provider"] = _engine_status(
                    True,
                    True,
                    provider=result.get("source") or "intraday_bars",
                    source=result.get("source"),
                    count=result.get("count"),
                    interval=result.get("interval"),
                )
            else:
                fetch_error = str(result.get("error") or "no_intraday_prices")
                engine_status["data_provider"] = _engine_status(
                    True,
                    False,
                    error=fetch_error,
                    provider="intraday_bars",
                    interval=normalized_timeframe,
                )
        except Exception as e:
            logger.exception(
                "Intraday technical prices failed for %s/%s: %s",
                sym,
                normalized_timeframe,
                e,
            )
            fetch_error = str(e)
            engine_status["data_provider"] = _engine_status(
                True,
                False,
                error=fetch_error,
                provider="intraday_bars",
                interval=normalized_timeframe,
            )
    else:
        # Inyección de dependencia: el caller debe proveer el repositorio.
        # En producción, siempre inyectar explícitamente.
        if price_repo is None:
            from backend.infrastructure.repositories.fmp_price_repository import FMPPriceRepository
            from backend.layer_1_data.fetchers.fmp_client import FMPClient

            price_repo = FMPPriceRepository(FMPClient())
            logger.warning("No price_repo provided - created default FMPPriceRepository")

        date_from = (datetime.now() - timedelta(days=max(days, 60))).strftime("%Y-%m-%d")
        rows: list[FMPHistoricalPrice] = []
        try:
            rows = await price_repo.get_historical_prices(sym, date_from=date_from)
        except Exception as e:
            logger.exception("Error fetching prices for %s: %s", sym, e)
            fetch_error = str(e)

        df = _df_from_fmp_rows(rows) if rows else pd.DataFrame()
        if not df.empty:
            engine_status["data_provider"] = _engine_status(True, True, provider="fmp")
        elif enable_composite_repo:
            try:
                from backend.layer_1_data.datos.massive_equity_bars_fetcher import (
                    fetch_equity_daily_bars,
                )

                _closes, fallback_df, meta = await run_cpu_bound(
                    fetch_equity_daily_bars,
                    sym,
                    timeout=30.0,
                )
                if fallback_df is not None and not fallback_df.empty:
                    df = fallback_df.copy()
                    if "date" not in df.columns:
                        if "t" in df.columns:
                            df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(
                                None
                            )
                        else:
                            df["date"] = pd.to_datetime(df.index)
                    engine_status["data_provider"] = _engine_status(
                        True,
                        True,
                        provider="massive_polygon_fallback",
                        source=(meta or {}).get("source"),
                        meta=meta,
                    )
                else:
                    engine_status["data_provider"] = _engine_status(
                        True,
                        False,
                        error=(meta or {}).get("error") or fetch_error or "no_historical_prices",
                        provider="fmp_then_massive_polygon",
                        meta=meta,
                    )
            except Exception as e:
                logger.exception("Composite technical price fallback failed for %s: %s", sym, e)
                engine_status["data_provider"] = _engine_status(
                    True,
                    False,
                    error=str(e),
                    provider="fmp_then_massive_polygon",
                )
        else:
            engine_status["data_provider"] = _engine_status(
                True,
                False,
                error=fetch_error or "no_historical_prices",
                provider="fmp",
            )

    # Keep the fetch path and generated-candle path on the same calculation contract.
    return await _build_technical_payload_from_df(
        sym,
        df,
        normalized_timeframe,
        engine_status,
        flags,
        use_cpu_offload=use_cpu_offload,
        candle_source="fetch",
        analysis_cadence="request",
        source_meta={"fetch_error": fetch_error},
    )

    enable_volume_engines = flags["enable_volume_engines"]
    enable_fvg_engine = flags["enable_fvg_engine"]
    enable_structure_engines = flags["enable_structure_engines"]
    enable_order_flow_delta = flags["enable_order_flow_delta"]
    enable_lob_dynamics = flags["enable_lob_dynamics"]
    enable_hmm_engine = flags["enable_hmm_engine"]
    enable_footprint_engine = flags["enable_footprint_engine"]

    if df.empty:
        return {
            "ok": False,
            "symbol": sym,
            "timeframe": normalized_timeframe,
            "error": "No historical prices - verify symbol or data provider configuration.",
            "engine_status": engine_status,
        }
    if len(df) < 35:
        return {
            "ok": False,
            "symbol": sym,
            "timeframe": normalized_timeframe,
            "error": f"Insufficient bars ({len(df)}); need at least 35 for SMC pipeline.",
            "engine_status": engine_status,
        }

    # 3. Extraer arrays NumPy para cálculos (usando memory pool)
    bars = len(df)
    tech_arrays = allocate_technical_arrays(bars=bars, dtype=np.float64)

    # Llenar arrays con datos reales
    tech_arrays["h"][:] = df["high"].to_numpy(dtype=np.float64)
    tech_arrays["lo"][:] = df["low"].to_numpy(dtype=np.float64)
    tech_arrays["c"][:] = df["close"].to_numpy(dtype=np.float64)
    tech_arrays["v"][:] = df["volume"].to_numpy(dtype=np.float64)

    dates = _chart_times_from_df(df, normalized_timeframe)

    # 4. Calcular indicadores técnicos (CPU-bound) con memory pool
    if use_cpu_offload:
        # Offload a ThreadPoolExecutor
        indicators = await run_cpu_bound(
            _calculate_indicators_with_pool,
            tech_arrays,
            timeout=10.0,  # Timeout para evitar bloqueos
        )
    else:
        # Ejecución sincrónica (legacy)
        indicators = _calculate_indicators_with_pool(tech_arrays)

    vwap = indicators["vwap"]
    sma20 = indicators["sma20"]
    sma50 = indicators["sma50"]
    sma200 = indicators["sma200"]
    ema21 = indicators["ema21"]
    avwap = indicators["avwap"]

    # Liberar arrays del pool (reutilización)
    release_technical_arrays(tech_arrays)

    # 5. Análisis SMC y fractal (CPU-bound pesado)
    if use_cpu_offload:
        # Offload a ThreadPoolExecutor
        smc, fract = await run_cpu_bound(
            _analyze_smc_cpu_bound,
            df,
            sym,
            normalized_timeframe,
            timeout=15.0,  # SMC puede tardar más
        )
    else:
        # Ejecución sincrónica (legacy)
        smc, fract = _analyze_smc_cpu_bound(df, sym, normalized_timeframe)

    optional_payload: dict[str, Any] = {
        "lob_dynamics": unavailable_lob_dynamics_payload()
        | {
            "enabled": enable_lob_dynamics,
            "error": "L2 order-book feed not configured",
        }
    }
    heavy_df = _heavy_engine_frame(df, normalized_timeframe)
    optional_payload["engine_window"] = {
        "enabled": True,
        "ok": True,
        "source_bars": len(df),
        "analysis_bars": len(heavy_df),
        "windowed": len(heavy_df) < len(df),
        "timeframe": normalized_timeframe,
    }

    if enable_structure_engines:
        try:
            structure_payload = await run_cpu_bound(
                _technical_structure_engines_cpu_bound,
                heavy_df,
                timeout=8.0,
            )
            optional_payload.update(structure_payload)
            for key in ("candle_geometry", "market_structure"):
                block = structure_payload.get(key) or {}
                engine_status[key] = _engine_status(
                    True,
                    bool(block.get("ok")),
                    block.get("error"),
                )
        except Exception as exc:
            logger.exception("technical structure engines failed for %s: %s", sym, exc)
            for key in ("candle_geometry", "market_structure"):
                engine_status[key] = _engine_status(True, False, str(exc))

    if enable_order_flow_delta:
        try:
            order_flow_payload = await run_cpu_bound(
                _technical_order_flow_delta_cpu_bound,
                heavy_df,
                timeout=8.0,
            )
            optional_payload["order_flow_delta"] = order_flow_payload
            engine_status["order_flow_delta"] = _engine_status(
                True,
                bool(order_flow_payload.get("ok")),
                order_flow_payload.get("error"),
            )
        except Exception as exc:
            logger.exception("technical order-flow delta engine failed for %s: %s", sym, exc)
            engine_status["order_flow_delta"] = _engine_status(True, False, str(exc))

    if enable_fvg_engine:
        try:
            fvg_payload = await run_cpu_bound(
                _technical_fvg_engine_cpu_bound,
                heavy_df,
                timeout=8.0,
            )
            optional_payload["fvg"] = fvg_payload
            engine_status["fvg"] = _engine_status(
                True,
                bool(fvg_payload.get("ok")),
                fvg_payload.get("error"),
            )
        except Exception as exc:
            logger.exception("technical FVG engine failed for %s: %s", sym, exc)
            engine_status["fvg"] = _engine_status(True, False, str(exc))

    if enable_volume_engines:
        try:
            volume_payload = await run_cpu_bound(
                _technical_volume_engines_cpu_bound,
                heavy_df,
                sym,
                normalized_timeframe,
                timeout=10.0,
            )
            optional_payload.update(volume_payload)
            for key in (
                "vsa",
                "volume_profile",
                "volume_nodes",
                "vwap_advanced",
                "delta_volume",
                "ofi",
                "tpo_skewness",
                "single_prints",
            ):
                block = volume_payload.get(key) or {}
                engine_status[key] = _engine_status(
                    True,
                    bool(block.get("ok")),
                    block.get("error"),
                )
            block = volume_payload.get("vpoc_migration") or {}
            engine_status["vpoc_migration"] = _engine_status(
                True,
                bool(block.get("ok")),
                block.get("error"),
            )
        except Exception as exc:
            logger.exception("technical volume engines failed for %s: %s", sym, exc)
            for key in (
                "vsa",
                "volume_profile",
                "volume_nodes",
                "vwap_advanced",
                "delta_volume",
                "vpoc_migration",
                "ofi",
                "tpo_skewness",
                "single_prints",
            ):
                engine_status[key] = _engine_status(True, False, str(exc))

    if enable_footprint_engine:
        try:
            footprint_payload = await run_cpu_bound(
                _technical_footprint_cpu_bound,
                heavy_df,
                sym,
                timeout=8.0,
            )
            optional_payload["vsa_footprint"] = footprint_payload
            engine_status["vsa_footprint"] = _engine_status(
                True,
                bool(footprint_payload.get("ok")),
                footprint_payload.get("error"),
            )
        except Exception as exc:
            logger.exception("technical footprint engine failed for %s: %s", sym, exc)
            engine_status["vsa_footprint"] = _engine_status(True, False, str(exc))

    if enable_hmm_engine:
        try:
            hmm_payload = await run_cpu_bound(
                _technical_hmm_engine_cpu_bound,
                heavy_df,
                timeout=8.0,
            )
            optional_payload["hmm_regime"] = hmm_payload
            engine_status["hmm_regime"] = _engine_status(
                True,
                bool(hmm_payload.get("ok")),
                hmm_payload.get("error"),
            )
        except Exception as exc:
            logger.exception("technical HMM regime engine failed for %s: %s", sym, exc)
            engine_status["hmm_regime"] = _engine_status(True, False, str(exc))

    # 6. Generar structure markers
    structure_markers: list[dict[str, Any]] = []
    for ev in smc.structure_events[-25:]:
        bi = ev.bar_index
        if 0 <= bi < len(dates):
            structure_markers.append(
                {
                    "time": dates[bi],
                    "event_type": ev.event_type,
                    "price": ev.level,
                }
            )

    # 7. Ensamblar payload final
    payload = {
        "ok": True,
        "symbol": sym,
        "timeframe": normalized_timeframe,
        "as_of": datetime.now(tz=None).isoformat(),
        "candles": [
            {
                "time": dates[i],
                "open": float(df["open"].iloc[i]),
                "high": float(df["high"].iloc[i]),
                "low": float(df["low"].iloc[i]),
                "close": float(df["close"].iloc[i]),
            }
            for i in range(len(df))
        ],
        "overlays": {
            "vwap": _series_to_chart(dates, vwap),
            "sma20": _series_to_chart(dates, sma20),
            "sma50": _series_to_chart(dates, sma50),
            "sma200": _series_to_chart(dates, sma200),
            "ema21": _series_to_chart(dates, ema21),
            "avwap": _series_to_chart(dates, avwap),
        },
        "structure_markers": structure_markers,
        "smc": smc.model_dump(mode="json"),
        "fractal": fract.model_dump(mode="json"),
        "meta": {
            "bars": len(df),
            "optional_engine_bars": len(heavy_df),
            "optional_engine_windowed": len(heavy_df) < len(df),
            "sesgo_smc": smc.sesgo.value,
            "composite_score": smc.composite_score,
        },
        "engine_status": engine_status,
    }
    payload.update(optional_payload)
    return payload
