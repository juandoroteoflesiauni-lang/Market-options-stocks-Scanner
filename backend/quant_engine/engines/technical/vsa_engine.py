from __future__ import annotations
from typing import Any
"""Motor Cuantitativo VSA (Volume Spread Analysis) — Sector Técnico.

Implementa la lógica cuantitativa de Tom Williams para la detección de anomalías
de volumen y precio, incluyendo absorción institucional y Weis Wave.
"""


import logging
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from ...domain.technical.vsa_models import (
    BEARISH_TRIGGER_LABELS,
    BULLISH_LABELS,
    BULLISH_TRIGGER_LABELS,
    DirectionalBias,
    VSABarResult,
    VSALabel,
    VSAResult,
)
from .vsa_forecast import VSAForecastEngine

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# §0  CALIBRATED PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

_EPSILON: float = 1e-9  # regularización global para divisiones


# ─────────────────────────────────────────────────────────────────────────────
# §1  VSAConfig — Configuración del Motor
# ─────────────────────────────────────────────────────────────────────────────


class VSAConfig:
    """Parámetros configurables del motor VSA."""

    __slots__ = (
        "absorption_threshold",
        "absorption_window",
        "climax_vol_percentile",
        "climax_vol_window",
        "close_ratio_high",
        "close_ratio_low",
        "consolidation_window",
        "mfi_period",
        "spread_narrow_ratio",
        "vol_window",
        "vz_climax",
        "vz_effort",
        "vz_low",
        "weis_wave_threshold",
    )

    def __init__(
        self,
        vol_window: int = 20,
        absorption_window: int = 20,
        absorption_threshold: float = 2.0,
        climax_vol_percentile: float = 90.0,
        climax_vol_window: int = 50,
        weis_wave_threshold: float = 0.02,
        vz_climax: float = 2.5,
        vz_low: float = -1.0,
        vz_effort: float = 1.5,
        close_ratio_high: float = 0.70,
        close_ratio_low: float = 0.70,
        spread_narrow_ratio: float = 0.70,
        consolidation_window: int = 5,
        mfi_period: int = 3,
    ) -> None:
        self.vol_window = vol_window
        self.absorption_window = absorption_window
        self.absorption_threshold = absorption_threshold
        self.climax_vol_percentile = climax_vol_percentile
        self.climax_vol_window = climax_vol_window
        self.weis_wave_threshold = weis_wave_threshold
        self.vz_climax = vz_climax
        self.vz_low = vz_low
        self.vz_effort = vz_effort
        self.close_ratio_high = close_ratio_high
        self.close_ratio_low = close_ratio_low
        self.spread_narrow_ratio = spread_narrow_ratio
        self.consolidation_window = consolidation_window
        self.mfi_period = mfi_period


# ─────────────────────────────────────────────────────────────────────────────
# §2  VSAEngine — Motor Analítico Puro
# ─────────────────────────────────────────────────────────────────────────────


class VSAEngine:
    """Motor Cuantitativo de VSA."""

    def __init__(self, config: VSAConfig | None = None, forecast_engine: VSAForecastEngine | None = None) -> None:
        self.cfg = config or VSAConfig()
        self.forecast_eng = forecast_engine

    def analyze(
        self,
        df_raw: pd.DataFrame,
        ticker: str = "UNKNOWN",
        timeframe: str = "UNKNOWN",
        include_bar_results: bool = False,
    ) -> VSAResult:
        """Pipeline completo de análisis VSA."""
        ts = datetime.now(tz=UTC)
        try:
            df = _normalize_columns(df_raw)
            _validate_columns(df, self.cfg.vol_window)

            df = _compute_base_variables(df, self.cfg)
            df = _classify_bars(df, self.cfg)
            df = _compute_a_index(df, self.cfg)
            df = _detect_buying_climax(df, self.cfg)
            df = _compute_mfi_kinetic(df, self.cfg.mfi_period)
            df = _compute_weis_wave(df, self.cfg.weis_wave_threshold)
            mfi_col = f"mfi_{self.cfg.mfi_period}"

            cvd_line = self._compute_cvd_approx(df)
            df["cvd"] = cvd_line
            cvd_last = float(cvd_line[-1])
            cvd_slope = float(np.polyfit(np.arange(len(cvd_line[-5:])), cvd_line[-5:], 1)[0])

            vfi_val: float = 0.0
            vfi_slope: float = 0.0
            is_forecast_climax: bool = False
            fs_support: float | None = None
            fs_resistance: float | None = None
            
            if self.forecast_eng:
                try:
                    ohlcv = df[["open", "high", "low", "close", "volume"]].to_numpy(dtype=np.float64)
                    vfi_res = self.forecast_eng.calculate_vfi(ohlcv, period=14)
                    if vfi_res.is_success and vfi_res.value:
                        vfi_val = vfi_res.value.get("vfi", 0.0)
                        vfi_slope = vfi_res.value.get("slope", 0.0)
                        
                    fp_res = self.forecast_eng.detect_footprint_clusters(ohlcv)
                    if fp_res.is_success and fp_res.value:
                        fs_support, fs_resistance = fp_res.value
                except Exception as ex:
                    logger.warning(f"VSAForecastEngine interaction failed: {ex}")

            signal, intercepted, recent_labels = _consolidate_signal(
                df, self.cfg.consolidation_window, mfi_col
            )

            last = df.iloc[-1]
            long_signal = _evaluate_long_signal_0dte(df, mfi_col)
            short_signal = _evaluate_short_signal_0dte(df, mfi_col)
            bearish_label_count = sum(1 for lbl in recent_labels if lbl in BEARISH_TRIGGER_LABELS)

            bar_results: list[VSABarResult] = []
            if include_bar_results:
                bar_results = _build_bar_results(df, mfi_col)

            score = _vsa_composite_score(
                df,
                signal,
                recent_labels,
                last,
                mfi_col,
                long_signal,
                short_signal,
            )

            buy_abs, sell_abs = self.detect_directional_absorption(df)

            return VSAResult(
                ticker=ticker,
                timeframe=timeframe,
                timestamp=ts,
                signal=signal,
                recent_labels=recent_labels,
                last_vz_score=round(float(last["vz"]), 6),
                last_absorption_index=round(float(last["absorption_index"]), 6),
                last_a_index_zscore=round(float(last["a_index_zscore"]), 6),
                last_relative_position=round(float(last.get("relative_position", 0.0)), 6),
                last_mfi_kinetic=_safe_float(last.get(mfi_col)),
                last_close_location=round(float(last["close_location"]), 6),
                last_spread_pct=round(float(last["spread_pct"]), 6),
                last_weis_wave_volume=_safe_float(last.get("weis_wave_volume")),
                last_weis_wave_direction=_safe_int(last.get("weis_wave_direction")),
                bullish_signals_count=sum(1 for lbl in recent_labels if lbl in BULLISH_LABELS),
                intercepted_bearish_count=intercepted,
                bearish_label_count=bearish_label_count,
                is_absorption_active=bool(last["is_absorption_anomalous"]),
                is_buying_climax_active=bool(last["is_buying_climax"]),
                long_signal_active=bool(long_signal),
                short_0dte=bool(short_signal),
                bar_results=bar_results,
                composite_score=score,
                # Pro metrics
                rvol=self.compute_rvol(df, self.cfg.vol_window),
                vol_velocity=self.compute_volume_velocity(df, 5),
                buy_absorption=bool(buy_abs),
                sell_absorption=bool(sell_abs),
                effort_result_ratio=self.compute_effort_result_ratio(df),
                adv=self.compute_adv_last(df),
                weis_wave_peak=bool(self.detect_weis_wave_peak(df)),
                vfi_value=vfi_val,
                vfi_slope=vfi_slope,
                is_forecast_climax=bool(is_forecast_climax),
                footprint_support=fs_support,
                footprint_resistance=fs_resistance,
                cvd_last=cvd_last,
                cvd_slope=cvd_slope,
                ok=True,
            )

        except Exception as exc:
            logger.exception("[VSAEngine] Error en %s/%s: %s", ticker, timeframe, exc)
            return VSAResult(
                ticker=ticker,
                timeframe=timeframe,
                timestamp=ts,
                signal=DirectionalBias.NEUTRAL,
                recent_labels=[],
                error=str(exc),
                ok=False,
            )

    def compute_rvol(self, df: pd.DataFrame, window: int = 20) -> float:
        if len(df) < window:
            return 1.0
        avg_vol = df["volume"].rolling(window).mean().iloc[-1]
        return float(df["volume"].iloc[-1] / (avg_vol + 1e-9))

    def compute_volume_velocity(self, df: pd.DataFrame, n: int = 5) -> float:
        if len(df) < n * 2:
            return 0.0
        v_recent = df["volume"].iloc[-n:].mean()
        v_prev = df["volume"].iloc[-2 * n : -n].mean()
        return float(v_recent / (v_prev + 1e-9))

    def detect_directional_absorption(self, df: pd.DataFrame) -> tuple[bool, bool]:
        if "close_location" not in df.columns:
            return (False, False)
        last = df.iloc[-1]
        if not bool(last.get("is_absorption_anomalous", False)):
            return (False, False)
        cl = last["close_location"]
        return (bool(cl > 0.6), bool(cl < 0.4))

    def compute_effort_result_ratio(self, df: pd.DataFrame) -> float:
        last = df.iloc[-1]
        move = abs(last["close"] - last["open"])
        vol = last["volume"]
        return float(move / (vol + 1e-9))

    def compute_adv_last(self, df: pd.DataFrame) -> float:
        last = df.iloc[-1]
        cloc = (last["close"] - last["low"]) / (last["high"] - last["low"] + 1e-9)
        return float(last["volume"] * (2 * cloc - 1))

    def detect_weis_wave_peak(self, df: pd.DataFrame) -> bool:
        if "weis_wave_volume" not in df.columns or len(df) < 20:
            return False
        v = df["weis_wave_volume"].values
        wave_ends = np.where(v[:-1] > v[1:])[0]
        if len(wave_ends) < 2:
            return False
        peaks = v[wave_ends]
        return bool(v[-1] > peaks[-5:].mean() * 1.5)

    def _compute_cvd_approx(self, df: pd.DataFrame) -> np.ndarray[Any, Any]:
        """Cálculo aproximado de CVD ante ausencia de TechnicalMath."""
        open_price = df["open"].values
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        volume = df["volume"].values
        diff = (close - open_price) / (high - low + 1e-9)
        return np.cumsum(volume * diff)


# ─────────────────────────────────────────────────────────────────────────────
# §3  PRIVATE IMPLEMENTATION BLOCKS
# ─────────────────────────────────────────────────────────────────────────────


def _compute_base_variables(df: pd.DataFrame, cfg: VSAConfig) -> pd.DataFrame:
    w = cfg.vol_window
    df["spread"] = df["high"] - df["low"]
    df["spread_pct"] = df["spread"] / (df["close"].replace(0, np.nan) + _EPSILON)
    safe_spread = df["spread"].replace(0, np.nan)
    df["close_location"] = ((df["close"] - df["low"]) / safe_spread).clip(0.0, 1.0)
    df["cierre_alto"] = df["close_location"] > cfg.close_ratio_high
    df["cierre_bajo"] = ((df["high"] - df["close"]) / safe_spread) > cfg.close_ratio_low
    df["is_bullish"] = df["close"] > df["open"]
    df["vol_mean"] = df["volume"].rolling(w, min_periods=w).mean()
    df["vol_std"] = df["volume"].rolling(w, min_periods=w).std(ddof=1)
    df["vz"] = (df["volume"] - df["vol_mean"]) / (df["vol_std"].replace(0, np.nan) + _EPSILON)
    df["spread_mean"] = df["spread"].rolling(w, min_periods=w).mean()
    return df


def _classify_bars(df: pd.DataFrame, cfg: VSAConfig) -> pd.DataFrame:
    vz, bullish = df["vz"], df["is_bullish"]
    c_alto, c_bajo = df["cierre_alto"], df["cierre_bajo"]
    spread, spread_mean = df["spread"], df["spread_mean"]

    label = pd.Series([VSALabel.NORMAL.value] * len(df), dtype=object, index=df.index)

    mask_evr = (vz > cfg.vz_effort) & (spread < spread_mean * cfg.spread_narrow_ratio)
    label[mask_evr] = VSALabel.EFFORT_VS_RESULT.value
    mask_nd = (vz < cfg.vz_low) & bullish
    label[mask_nd] = VSALabel.NO_DEMAND.value
    mask_ns = (vz < cfg.vz_low) & ~bullish
    label[mask_ns] = VSALabel.NO_SUPPLY.value
    mask_cs = (vz > cfg.vz_climax) & ~bullish & c_bajo
    label[mask_cs] = VSALabel.CLIMAX_SELL.value
    mask_cb = (vz > cfg.vz_climax) & bullish & c_bajo
    label[mask_cb] = VSALabel.CLIMAX_BUY.value
    mask_sv = (vz > cfg.vz_climax) & ~bullish & c_alto
    label[mask_sv] = VSALabel.STOPPING_VOLUME.value

    df["VSA_Label"] = label
    return df


def _compute_a_index(df: pd.DataFrame, cfg: VSAConfig) -> pd.DataFrame:
    w, thr = cfg.absorption_window, cfg.absorption_threshold
    df["absorption_index"] = df["volume"] / (df["spread"].clip(lower=0.0) + _EPSILON)
    df["relative_position"] = df["close_location"] * 100.0
    roll_mean = df["absorption_index"].rolling(w, min_periods=w).mean()
    roll_std = df["absorption_index"].rolling(w, min_periods=w).std(ddof=1)
    df["a_index_zscore"] = (
        (df["absorption_index"] - roll_mean) / (roll_std.replace(0, np.nan) + _EPSILON)
    ).fillna(0.0)
    df["is_absorption_anomalous"] = (df["absorption_index"] > (roll_mean + thr * roll_std)).fillna(
        False
    )
    return df


def _detect_buying_climax(df: pd.DataFrame, cfg: VSAConfig) -> pd.DataFrame:
    pct, w = cfg.climax_vol_percentile / 100.0, cfg.climax_vol_window
    vol_thr = df["volume"].rolling(w, min_periods=10).quantile(pct)
    cond1 = df["volume"] > vol_thr
    cond2 = df["high"] > df["high"].shift(1)
    cond3 = df["close"] <= (df["high"] + df["low"]) / 2.0
    df["is_buying_climax"] = (cond1 & cond2 & cond3).fillna(False)
    return df


def _compute_mfi_kinetic(df: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    rmf = tp * df["volume"]
    tp_p = tp.shift(1)
    pmf, nmf = rmf.where(tp > tp_p, 0.0), rmf.where(tp < tp_p, 0.0)
    sum_p = pmf.rolling(n, min_periods=n).sum()
    sum_n = nmf.rolling(n, min_periods=n).sum()
    mfr = sum_p / (sum_n.replace(0, np.nan) + _EPSILON)
    df[f"mfi_{n}"] = (100.0 - (100.0 / (1.0 + mfr))).clip(0.0, 100.0)
    return df


def _compute_weis_wave(df: pd.DataFrame, threshold: float = 0.02) -> pd.DataFrame:
    closes, volumes = df["close"].values.astype(np.float64), df["volume"].values.astype(np.float64)
    n = len(closes)
    wave_vol, wave_dir = np.zeros(n), np.ones(n, dtype=np.int8)
    if n > 0:
        cv, cd, wsp = volumes[0], 1, closes[0]
        wave_vol[0], wave_dir[0] = cv, cd
        for i in range(1, n):
            pch = (closes[i] - wsp) / (wsp + _EPSILON)
            if (cd == 1 and pch < -threshold) or (cd == -1 and pch > threshold):
                cd, wsp, cv = -cd, closes[i], volumes[i]
            else:
                cv += volumes[i]
            wave_vol[i], wave_dir[i] = cv, cd
    df["weis_wave_volume"], df["weis_wave_direction"] = wave_vol, wave_dir
    return df


def _consolidate_signal(
    df: pd.DataFrame,
    window: int = 5,
    mfi_col: str | None = None,
) -> tuple[DirectionalBias, int, list[VSALabel]]:
    """Consolida labels VSA recientes en sesgo direccional simétrico."""
    recent = df["VSA_Label"].iloc[-window:]
    labels = [VSALabel(v) for v in recent.tolist()]
    bullish_hit = any(lbl in BULLISH_TRIGGER_LABELS for lbl in labels)
    bearish_hit = any(lbl in BEARISH_TRIGGER_LABELS for lbl in labels)
    bearish_count = sum(1 for lbl in labels if lbl in BEARISH_TRIGGER_LABELS)

    cvd_last = _last_numeric(df, "cvd")
    a_index_zscore = _last_numeric(df, "a_index_zscore")
    mfi_kinetic = _mfi_kinetic_delta(df, mfi_col)
    absorption_active = bool(
        df.get("is_absorption_anomalous", pd.Series([False])).fillna(False).iloc[-1]
    )
    absorption_strength = absorption_active or a_index_zscore > 0.5

    if bullish_hit and (cvd_last > 0.0 or (absorption_strength and mfi_kinetic > 0.0)):
        return DirectionalBias.BULLISH, bearish_count, labels
    if bullish_hit:
        return DirectionalBias.BULLISH, bearish_count, labels

    if bearish_hit and (cvd_last < 0.0 or (absorption_strength and mfi_kinetic < 0.0)):
        return DirectionalBias.BEARISH, bearish_count, labels
    if bearish_hit:
        return DirectionalBias.BEARISH, bearish_count, labels

    return DirectionalBias.NEUTRAL, bearish_count, labels


def _evaluate_long_signal_0dte(df: pd.DataFrame, mfi_col: str) -> bool:
    if mfi_col not in df.columns or len(df) < 3:
        return False
    mfi = df[mfi_col].dropna()
    if len(mfi) < 2:
        return False
    mfi_now, mfi_prev = float(mfi.iloc[-1]), float(mfi.iloc[-2])
    bounce = (mfi_prev < 20.0) and (mfi_now > mfi_prev)
    absorb = bool(df["is_absorption_anomalous"].fillna(False).iloc[-1])
    no_cli = not bool(df["is_buying_climax"].fillna(False).iloc[-1])
    return bool(bounce and absorb and no_cli)


def _evaluate_short_signal_0dte(df: pd.DataFrame, mfi_col: str) -> bool:
    """Señal short intraday por distribución: espejo operativo del long 0DTE."""
    if mfi_col not in df.columns or len(df) < 3:
        return False

    labels = [VSALabel(v) for v in df["VSA_Label"].iloc[-3:].tolist()]
    no_capitulation = VSALabel.CLIMAX_SELL not in labels
    distribution_trigger = any(lbl in BEARISH_TRIGGER_LABELS for lbl in labels)
    mfi_distributing = _mfi_kinetic_delta(df, mfi_col) < -0.2
    absorption_by_sellers = (
        bool(df["is_absorption_anomalous"].fillna(False).iloc[-1])
        or _last_numeric(df, "a_index_zscore") > 0.8
    )
    sell_pressure = _last_numeric(df, "cvd") < 0.0

    return bool(
        no_capitulation
        and mfi_distributing
        and absorption_by_sellers
        and sell_pressure
        and distribution_trigger
    )


def _vsa_composite_score(
    df: pd.DataFrame,
    signal: DirectionalBias,
    labels: list[VSALabel],
    last: pd.Series,
    mfi_col: str,
    long_0dte: bool = False,
    short_0dte: bool = False,
) -> float:
    """Score VSA simétrico: positivo long, negativo short."""
    a_z = max(float(last.get("a_index_zscore", 0.0)), 0.0)
    absorption_score = (
        1.0 if bool(last.get("is_absorption_anomalous", False)) else min(a_z / 3.0, 1.0)
    )
    mfi_kinetic = _mfi_kinetic_delta(df, mfi_col)
    bullish_count = sum(1 for lbl in labels if lbl in BULLISH_TRIGGER_LABELS)
    bearish_count = sum(1 for lbl in labels if lbl in BEARISH_TRIGGER_LABELS)

    if signal == DirectionalBias.BULLISH:
        score = 30.0
        score += min(a_z, 2.0) * 10.0
        score += bullish_count * 10.0
        score += absorption_score * 15.0
        score += 10.0 if long_0dte else 0.0
        score += max(mfi_kinetic, 0.0) * 15.0
        return round(float(min(score, 100.0)), 4)

    if signal == DirectionalBias.BEARISH:
        score = 30.0
        score += min(a_z, 2.0) * 10.0
        score += bearish_count * 10.0
        score += absorption_score * 15.0
        score += 10.0 if short_0dte else 0.0
        score += abs(min(mfi_kinetic, 0.0)) * 15.0
        return round(float(-min(score, 100.0)), 4)

    return 0.0


def _build_bar_results(df: pd.DataFrame, mfi_col: str) -> list[VSABarResult]:
    results: list[VSABarResult] = []
    valid = df.dropna(subset=["vz"])
    for idx, row in valid.iterrows():
        results.append(
            VSABarResult(
                index=int(valid.index.get_loc(idx)),
                label=VSALabel(row["VSA_Label"]),
                vz_score=round(float(row["vz"]), 6),
                absorption_index=round(float(row["absorption_index"]), 6),
                a_index_zscore=round(float(row["a_index_zscore"]), 6),
                relative_position=round(float(row["relative_position"]), 6),
                close_location=round(float(row["close_location"]), 6),
                spread_pct=round(float(row["spread_pct"]), 6),
                is_bullish_candle=bool(row["is_bullish"]),
                is_anomalous_absorption=bool(row["is_absorption_anomalous"]),
                is_buying_climax=bool(row["is_buying_climax"]),
                mfi_kinetic=_safe_float(row.get(mfi_col)),
                weis_wave_volume=_safe_float(row.get("weis_wave_volume")),
                weis_wave_direction=_safe_int(row.get("weis_wave_direction")),
            )
        )
    return results


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower().strip() for c in df.columns]
    alias = {
        "adj close": "close",
        "adj_close": "close",
        "vol": "volume",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
    }
    df.rename(columns={k: v for k, v in alias.items() if k in df.columns}, inplace=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(np.float64)
    return df


def _validate_columns(df: pd.DataFrame, vol_window: int) -> None:
    req = {"open", "high", "low", "close", "volume"}
    miss = req - set(df.columns)
    if miss:
        raise ValueError(f"VSAEngine: missing columns: {miss}")
    df.dropna(subset=list(req), inplace=True)
    if len(df) < vol_window:
        raise ValueError(f"VSAEngine: insufficient data ({len(df)})")


def _last_numeric(df: pd.DataFrame, column: str, default: float = 0.0) -> float:
    if column not in df.columns or df.empty:
        return default
    return _safe_float(df[column].iloc[-1]) or default


def _mfi_kinetic_delta(df: pd.DataFrame, mfi_col: str | None) -> float:
    if mfi_col is None or mfi_col not in df.columns:
        return 0.0
    mfi = df[mfi_col].dropna()
    if len(mfi) < 2:
        return 0.0
    return float((float(mfi.iloc[-1]) - float(mfi.iloc[-2])) / 100.0)


def _safe_float(val: str | bytes | SupportsFloat | SupportsIndex | None) -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _safe_int(val: str | bytes | SupportsFloat | SupportsIndex | None) -> int | None:
    if val is None:
        return None
    try:
        v = float(val)
        return None if np.isnan(v) else int(v)
    except (TypeError, ValueError):
        return None
