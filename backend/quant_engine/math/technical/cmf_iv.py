"""CMF-IV: Chaikin Money Flow divided by IV percentile, scaled by Vega sign."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_CMF_IV_COLUMNS = (
    "timestamp",
    "close",
    "cmf",
    "iv_pct",
    "iv_pct_norm",
    "vega_net",
    "vega_sign",
    "cmf_iv",
)


@dataclass(frozen=True)
class CmfIvFrame:
    """Last-bar CMF-IV outputs for scanner / desk fusion."""

    cmf: float | None
    iv_pct: float | None
    iv_pct_norm: float | None
    vega_net: float | None
    vega_sign: float | None
    cmf_iv: float | None
    signal: int
    iv_crush_active: bool


def compute_cmf(df: pd.DataFrame, *, period: int = 20) -> pd.Series:
    """Chaikin Money Flow in [-1, +1]."""
    hl_range = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = (2.0 * df["close"] - df["high"] - df["low"]) / hl_range
    mfm = mfm.fillna(0.0)
    mf_volume = mfm * df["volume"]
    vol_sum = df["volume"].rolling(window=period, min_periods=1).sum()
    cmf = mf_volume.rolling(window=period, min_periods=1).sum() / vol_sum
    return cmf.fillna(0.0).rename("cmf")


def compute_iv_percentile(iv_current: float, iv_history: list[float] | np.ndarray) -> float:
    """Min-max IV percentile in [0, 1] over supplied history window."""
    if iv_history is None or len(iv_history) < 2:
        return 0.5
    hist = np.asarray(iv_history, dtype=np.float64)
    hist = hist[np.isfinite(hist)]
    if len(hist) < 2:
        return 0.5
    iv_min = float(hist.min())
    iv_max = float(hist.max())
    if abs(iv_max - iv_min) < 1e-12:
        return 0.5
    return float(np.clip((iv_current - iv_min) / (iv_max - iv_min), 0.0, 1.0))


def process_volatility_snapshots(snapshots_df: pd.DataFrame) -> pd.DataFrame:
    """Derive iv_pct, iv_pct_norm, vega_net, vega_sign per vol snapshot row."""
    df = snapshots_df.copy().sort_values("timestamp").reset_index(drop=True)

    def _hist_list(raw: object) -> list[float]:
        if isinstance(raw, list | tuple):
            return [float(x) for x in raw if isinstance(x, int | float)]
        return []

    df["iv_pct"] = df.apply(
        lambda row: compute_iv_percentile(
            float(row["iv_atm"]), _hist_list(row.get("iv_30d_history"))
        ),
        axis=1,
    )
    df["iv_pct_norm"] = df["iv_pct"].clip(lower=0.1, upper=1.0)
    df["vega_net"] = df["vega_calls"].astype(np.float64) - df["vega_puts"].astype(np.float64)
    threshold = df.get("vega_min_threshold", pd.Series([100.0] * len(df))).astype(np.float64)
    significant = df["vega_net"].abs() > threshold
    df["vega_sign"] = np.where(significant, np.sign(df["vega_net"]), 1.0)
    return df[["timestamp", "iv_atm", "iv_pct", "iv_pct_norm", "vega_net", "vega_sign"]]


def compute_cmf_iv(
    price_df: pd.DataFrame,
    vol_snapshots_df: pd.DataFrame,
    *,
    cmf_period: int = 20,
) -> pd.DataFrame:
    """CMF_IV = (CMF / iv_pct_norm) × vega_sign — unbounded by design."""
    price = price_df.copy().sort_values("timestamp").reset_index(drop=True)
    vols = process_volatility_snapshots(vol_snapshots_df)

    merged = pd.merge_asof(
        price.sort_values("timestamp"),
        vols.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    ).reset_index(drop=True)

    merged["iv_pct_norm"] = merged["iv_pct_norm"].fillna(0.5)
    merged["vega_sign"] = merged["vega_sign"].fillna(1.0)
    merged["iv_pct"] = merged["iv_pct"].fillna(0.5)
    merged["vega_net"] = merged["vega_net"].fillna(0.0)

    merged["cmf"] = compute_cmf(merged, period=cmf_period)
    merged["cmf_iv"] = (merged["cmf"] / merged["iv_pct_norm"]) * merged["vega_sign"]

    return merged[list(_CMF_IV_COLUMNS)]


def generate_cmf_iv_signals(
    df: pd.DataFrame,
    *,
    long_threshold: float = 0.10,
    short_threshold: float = -0.10,
    iv_pct_max: float = 0.80,
) -> pd.DataFrame:
    """Entry signals with IV crush filter (iv_pct >= iv_pct_max → no trade)."""
    result = df.copy()
    result["signal"] = 0
    vol_ok = result["iv_pct"] < iv_pct_max
    long_cond = vol_ok & (result["cmf_iv"] > long_threshold) & (result["vega_sign"] >= 0)
    short_cond = vol_ok & (result["cmf_iv"] < short_threshold) & (result["vega_sign"] <= 0)
    result.loc[long_cond, "signal"] = 1
    result.loc[short_cond, "signal"] = -1
    result["iv_crush_filter"] = (result["iv_pct"] >= iv_pct_max).astype(int)
    return result


def run_cmf_iv_pipeline(
    price_df: pd.DataFrame,
    vol_snapshots_df: pd.DataFrame,
    *,
    cmf_period: int = 20,
    long_threshold: float = 0.10,
    short_threshold: float = -0.10,
    iv_pct_max: float = 0.80,
) -> pd.DataFrame:
    base = compute_cmf_iv(price_df, vol_snapshots_df, cmf_period=cmf_period)
    return generate_cmf_iv_signals(
        base,
        long_threshold=long_threshold,
        short_threshold=short_threshold,
        iv_pct_max=iv_pct_max,
    )


def last_cmf_iv_frame(df: pd.DataFrame) -> CmfIvFrame | None:
    if df.empty:
        return None
    row = df.iloc[-1]
    signal_val = int(row["signal"]) if "signal" in df.columns and pd.notna(row["signal"]) else 0
    crush = bool(int(row.get("iv_crush_filter", 0)))

    def _f(key: str) -> float | None:
        val = row.get(key)
        if val is None or (isinstance(val, float) and not np.isfinite(val)):
            return None
        return float(val)

    return CmfIvFrame(
        cmf=_f("cmf"),
        iv_pct=_f("iv_pct"),
        iv_pct_norm=_f("iv_pct_norm"),
        vega_net=_f("vega_net"),
        vega_sign=_f("vega_sign"),
        cmf_iv=_f("cmf_iv"),
        signal=signal_val,
        iv_crush_active=crush,
    )


def cmf_iv_bias_from_frame(frame: CmfIvFrame | None) -> str:
    if frame is None or frame.cmf_iv is None or frame.iv_crush_active:
        return "neutral"
    if frame.cmf_iv > 0.10 or frame.signal > 0:
        return "bullish"
    if frame.cmf_iv < -0.10 or frame.signal < 0:
        return "bearish"
    return "neutral"


def cmf_iv_score_from_frame(frame: CmfIvFrame | None) -> float:
    """Map unbounded CMF-IV to desk score; IV crush → neutral."""
    if frame is None or frame.cmf_iv is None:
        return 50.0
    if frame.iv_crush_active:
        return 48.0
    score = 50.0 + float(frame.cmf_iv) * 14.0
    if frame.signal > 0:
        score += 5.0
    elif frame.signal < 0:
        score -= 5.0
    if frame.iv_pct is not None and frame.iv_pct < 0.35:
        score += 4.0 if frame.cmf_iv > 0 else -4.0
    return float(np.clip(score, 0.0, 100.0))
