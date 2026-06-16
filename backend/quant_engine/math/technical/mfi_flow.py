"""MFI-Flow: Money Flow Index fused with normalized options premium flow ratio."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_MFI_FLOW_COLUMNS = (
    "timestamp",
    "close",
    "mfi",
    "flow_ratio",
    "flow_ratio_norm",
    "delta_net",
    "mfi_flow",
)


@dataclass(frozen=True)
class MfiFlowFrame:
    """Last-bar MFI-Flow outputs for scanner / desk fusion."""

    mfi: float | None
    flow_ratio: float | None
    flow_ratio_norm: float | None
    delta_net: float | None
    mfi_flow: float | None
    signal: int
    entry_signal: int


def compute_mfi(df: pd.DataFrame, *, period: int = 14) -> pd.Series:
    """Classic MFI on high/low/close/volume (0–100)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    rmf = tp * df["volume"]
    tp_diff = tp.diff()
    pos_flow = rmf.where(tp_diff > 0, 0.0)
    neg_flow = rmf.where(tp_diff < 0, 0.0)
    pmf = pos_flow.rolling(window=period, min_periods=1).sum()
    nmf = neg_flow.rolling(window=period, min_periods=1).sum()
    money_ratio = pmf / nmf.replace(0, np.nan)
    mfi = 100.0 - (100.0 / (1.0 + money_ratio))
    return mfi.fillna(50.0).rename("mfi")


def compute_flow_ratio(options_df: pd.DataFrame, *, norm_window: int = 20) -> pd.DataFrame:
    """Call$/Put$ ratio and SMA-normalized flow_ratio_norm."""
    df = options_df.copy().sort_values("timestamp").reset_index(drop=True)
    safe_put = df["put_flow_usd"].replace(0, np.nan).fillna(1.0)
    df["flow_ratio"] = df["call_flow_usd"] / safe_put
    flow_sma = df["flow_ratio"].rolling(window=norm_window, min_periods=1).mean()
    df["flow_ratio_norm"] = df["flow_ratio"] / flow_sma.replace(0, np.nan).fillna(1.0)
    return df[
        [
            "timestamp",
            "call_flow_usd",
            "put_flow_usd",
            "flow_ratio",
            "flow_ratio_norm",
            "delta_net",
        ]
    ]


def compute_mfi_flow(
    price_df: pd.DataFrame,
    options_df: pd.DataFrame,
    *,
    mfi_period: int = 14,
    norm_window: int = 20,
) -> pd.DataFrame:
    """MFI_Flow[t] = clamp(MFI[t] × flow_ratio_norm[t], 0, 100)."""
    price = price_df.copy().sort_values("timestamp").reset_index(drop=True)
    flow = compute_flow_ratio(options_df, norm_window=norm_window)

    merged = pd.merge_asof(
        price.sort_values("timestamp"),
        flow.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    ).reset_index(drop=True)

    merged["flow_ratio_norm"] = merged["flow_ratio_norm"].fillna(1.0)
    merged["delta_net"] = merged["delta_net"].fillna(0.0)
    merged["mfi"] = compute_mfi(merged, period=mfi_period)
    merged["mfi_flow"] = (merged["mfi"] * merged["flow_ratio_norm"]).clip(lower=0.0, upper=100.0)

    return merged[list(_MFI_FLOW_COLUMNS)]


def generate_mfi_flow_signals(
    df: pd.DataFrame,
    *,
    long_threshold: float = 60.0,
    short_threshold: float = 40.0,
    delta_confirm: bool = True,
) -> pd.DataFrame:
    """Long/short when MFI-Flow crosses thresholds with optional delta_net confirm."""
    result = df.copy()
    result["signal"] = 0

    long_cond = result["mfi_flow"] > long_threshold
    short_cond = result["mfi_flow"] < short_threshold
    if delta_confirm:
        long_cond = long_cond & (result["delta_net"] > 0)
        short_cond = short_cond & (result["delta_net"] < 0)

    result.loc[long_cond, "signal"] = 1
    result.loc[short_cond, "signal"] = -1

    signal_change = result["signal"].diff().ne(0) & result["signal"].ne(0)
    result["entry_signal"] = result["signal"].where(signal_change, 0)
    return result


def run_mfi_flow_pipeline(
    price_df: pd.DataFrame,
    options_df: pd.DataFrame,
    *,
    mfi_period: int = 14,
    norm_window: int = 20,
    long_threshold: float = 60.0,
    short_threshold: float = 40.0,
    delta_confirm: bool = True,
) -> pd.DataFrame:
    """Full MFI-Flow pipeline including signal columns."""
    base = compute_mfi_flow(
        price_df,
        options_df,
        mfi_period=mfi_period,
        norm_window=norm_window,
    )
    return generate_mfi_flow_signals(
        base,
        long_threshold=long_threshold,
        short_threshold=short_threshold,
        delta_confirm=delta_confirm,
    )


def last_mfi_flow_frame(df: pd.DataFrame) -> MfiFlowFrame | None:
    if df.empty:
        return None
    row = df.iloc[-1]

    def _f(key: str) -> float | None:
        val = row.get(key)
        if val is None or (isinstance(val, float) and not np.isfinite(val)):
            return None
        return float(val)

    signal_val = int(row["signal"]) if "signal" in df.columns and pd.notna(row["signal"]) else 0
    entry_val = (
        int(row["entry_signal"])
        if "entry_signal" in df.columns and pd.notna(row["entry_signal"])
        else 0
    )
    return MfiFlowFrame(
        mfi=_f("mfi"),
        flow_ratio=_f("flow_ratio"),
        flow_ratio_norm=_f("flow_ratio_norm"),
        delta_net=_f("delta_net"),
        mfi_flow=_f("mfi_flow"),
        signal=signal_val,
        entry_signal=entry_val,
    )


def mfi_flow_bias_from_frame(frame: MfiFlowFrame | None) -> str:
    if frame is None or frame.mfi_flow is None:
        return "neutral"
    if frame.mfi_flow > 60.0 or frame.signal > 0:
        return "bullish"
    if frame.mfi_flow < 40.0 or frame.signal < 0:
        return "bearish"
    return "neutral"


def mfi_flow_score_from_frame(frame: MfiFlowFrame | None, *, flow_amplified: bool) -> float:
    """Map MFI-Flow (0–100 scale) to desk score; 50 = neutral."""
    if frame is None or frame.mfi_flow is None:
        return 50.0
    score = float(frame.mfi_flow)
    if frame.signal > 0:
        score = min(100.0, score + 6.0)
    elif frame.signal < 0:
        score = max(0.0, score - 6.0)
    if flow_amplified and frame.flow_ratio_norm is not None:
        norm = frame.flow_ratio_norm
        if norm > 1.15:
            score = min(100.0, score + 4.0)
        elif norm < 0.85:
            score = max(0.0, score - 4.0)
    return float(np.clip(score, 0.0, 100.0))


def obv_mfi_double_conviction_active(
    *,
    obv_oi_signal: int,
    obv_oi_bias: str,
    mfi_flow: float | None,
    mfi_flow_long_threshold: float = 70.0,
    mfi_flow_short_threshold: float = 30.0,
) -> bool:
    """OBV-OI bullish cross + MFI-Flow > 70 (or bearish mirror)."""
    if mfi_flow is None:
        return False
    obv_bull = obv_oi_signal > 0 or obv_oi_bias == "bullish"
    obv_bear = obv_oi_signal < 0 or obv_oi_bias == "bearish"
    if obv_bull and mfi_flow >= mfi_flow_long_threshold:
        return True
    return bool(obv_bear and mfi_flow <= mfi_flow_short_threshold)
