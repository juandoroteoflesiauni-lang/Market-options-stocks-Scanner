"""OBV-OI: On-Balance Volume fused with net open-interest delta (intraday)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_OBV_OI_COLUMNS = (
    "timestamp",
    "close",
    "obv",
    "oi_net",
    "delta_oi_net",
    "obv_oi",
    "obv_oi_sma",
    "iv_ratio",
    "signal",
)


@dataclass(frozen=True)
class ObvOiFrame:
    """Last-bar OBV-OI outputs for scanner / desk fusion."""

    obv: float | None
    oi_net: float | None
    delta_oi_net: float | None
    obv_oi: float | None
    obv_oi_sma: float | None
    iv_ratio: float | None
    signal: int
    cross_signal: int


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Classic OBV from close and volume columns."""
    direction = np.sign(close.diff().fillna(0.0))
    volume_signed = volume.astype(np.float64) * direction
    return volume_signed.cumsum().rename("obv")


def compute_oi_net(options_df: pd.DataFrame) -> pd.DataFrame:
    """Derive net OI, delta, rolling average magnitude, and IV ratio."""
    df = options_df.copy().sort_values("timestamp").reset_index(drop=True)
    df["oi_net"] = df["oi_calls"].astype(np.float64) - df["oi_puts"].astype(np.float64)
    df["delta_oi_net"] = df["oi_net"].diff().fillna(0.0)
    df["oi_net_avg"] = df["oi_net"].abs().rolling(window=20, min_periods=1).mean()
    puts_iv = df["iv_puts_avg"].astype(np.float64).replace(0.0, np.nan)
    df["iv_ratio"] = (df["iv_calls_avg"].astype(np.float64) / puts_iv).fillna(1.0)
    return df[["timestamp", "oi_net", "delta_oi_net", "oi_net_avg", "iv_ratio"]]


def compute_obv_oi(
    price_df: pd.DataFrame,
    options_df: pd.DataFrame,
    *,
    sma_period: int = 9,
) -> pd.DataFrame:
    """Fuse OBV with signed, magnitude-scaled OI delta (merge_asof, backward)."""
    price = price_df.copy().sort_values("timestamp").reset_index(drop=True)
    opts = compute_oi_net(options_df)

    merged = pd.merge_asof(
        price.sort_values("timestamp"),
        opts.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    ).reset_index(drop=True)

    merged["obv"] = compute_obv(merged["close"], merged["volume"])

    safe_avg = merged["oi_net_avg"].replace(0.0, np.nan).fillna(1.0)
    amplifier = 1.0 + merged["delta_oi_net"].abs() / safe_avg

    oi_sign = np.sign(merged["delta_oi_net"])
    oi_sign = oi_sign.replace(0.0, 1.0)

    merged["obv_oi"] = merged["obv"] * oi_sign * amplifier
    merged["obv_oi_sma"] = merged["obv_oi"].rolling(window=sma_period, min_periods=1).mean()

    return merged[
        [
            "timestamp",
            "close",
            "obv",
            "oi_net",
            "delta_oi_net",
            "obv_oi",
            "obv_oi_sma",
            "iv_ratio",
        ]
    ]


def generate_obv_oi_signals(
    df: pd.DataFrame,
    *,
    iv_ratio_long_min: float = 1.0,
    iv_ratio_short_max: float = 1.0,
) -> pd.DataFrame:
    """SMA cross signals with IV-ratio filter (+1 long, -1 short, 0 flat)."""
    result = df.copy()
    cross_up = (result["obv_oi"] > result["obv_oi_sma"]) & (
        result["obv_oi"].shift(1) <= result["obv_oi_sma"].shift(1)
    )
    cross_down = (result["obv_oi"] < result["obv_oi_sma"]) & (
        result["obv_oi"].shift(1) >= result["obv_oi_sma"].shift(1)
    )
    result["signal"] = 0
    result.loc[cross_up & (result["iv_ratio"] > iv_ratio_long_min), "signal"] = 1
    result.loc[cross_down & (result["iv_ratio"] < iv_ratio_short_max), "signal"] = -1
    return result


def run_obv_oi_pipeline(
    price_df: pd.DataFrame,
    options_df: pd.DataFrame,
    *,
    sma_period: int = 9,
    iv_ratio_long_min: float = 1.0,
    iv_ratio_short_max: float = 1.0,
) -> pd.DataFrame:
    """Full OBV-OI pipeline including signal column."""
    base = compute_obv_oi(price_df, options_df, sma_period=sma_period)
    return generate_obv_oi_signals(
        base,
        iv_ratio_long_min=iv_ratio_long_min,
        iv_ratio_short_max=iv_ratio_short_max,
    )


def last_obv_oi_frame(df: pd.DataFrame) -> ObvOiFrame | None:
    """Extract the final bar; returns None when the frame is empty."""
    if df.empty:
        return None
    row = df.iloc[-1]
    signal_val = int(row["signal"]) if "signal" in df.columns and pd.notna(row["signal"]) else 0
    cross = signal_val

    def _f(key: str) -> float | None:
        val = row.get(key)
        if val is None or (isinstance(val, float) and not np.isfinite(val)):
            return None
        return float(val)

    return ObvOiFrame(
        obv=_f("obv"),
        oi_net=_f("oi_net"),
        delta_oi_net=_f("delta_oi_net"),
        obv_oi=_f("obv_oi"),
        obv_oi_sma=_f("obv_oi_sma"),
        iv_ratio=_f("iv_ratio"),
        signal=signal_val,
        cross_signal=cross,
    )


def obv_oi_bias_from_frame(frame: ObvOiFrame | None) -> str:
    if frame is None or frame.obv_oi is None or frame.obv_oi_sma is None:
        return "neutral"
    if frame.cross_signal > 0 or frame.obv_oi > frame.obv_oi_sma:
        return "bullish"
    if frame.cross_signal < 0 or frame.obv_oi < frame.obv_oi_sma:
        return "bearish"
    return "neutral"


def obv_oi_score_from_frame(frame: ObvOiFrame | None, *, options_amplified: bool) -> float:
    """Map OBV-OI state to a 0-100 desk score (50 = neutral)."""
    if frame is None or frame.obv_oi is None or frame.obv_oi_sma is None:
        return 50.0
    score = 50.0
    spread = frame.obv_oi - frame.obv_oi_sma
    denom = max(abs(frame.obv_oi_sma), 1.0)
    normalized = float(np.clip(spread / denom, -1.0, 1.0))
    score += normalized * 22.0
    if frame.cross_signal > 0:
        score += 12.0
    elif frame.cross_signal < 0:
        score -= 12.0
    if options_amplified and frame.delta_oi_net is not None and abs(frame.delta_oi_net) > 0:
        score += 4.0 if frame.delta_oi_net > 0 else -4.0
    if frame.iv_ratio is not None:
        if frame.iv_ratio > 1.05:
            score += 3.0
        elif frame.iv_ratio < 0.95:
            score -= 3.0
    return float(np.clip(score, 0.0, 100.0))
