"""Volume-synchronized toxicity proxies from OHLCV (no trade tape).

VPIN-class metrics approximate informed trading pressure using bar classification.
Reference: Easley et al. VPIN; here we use a lightweight OHLCV-only variant suitable
for Layer 2 (pure math, no IO).
"""

from __future__ import annotations

from typing import Any

import numpy as np


def classify_bar_volume_lee_ready(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Split bar volume into buy vs sell estimates using mid-price tick rule."""
    mid = (high + low) / 2.0
    buy = np.where(close >= mid, volume, 0.0)
    sell = np.where(close < mid, volume, 0.0)
    return buy.astype(np.float64), sell.astype(np.float64)


def compute_vpin_proxy(
    close: np.ndarray, high: np.ndarray, low: np.ndarray, volume: np.ndarray
) -> dict[str, Any]:
    """Return VPIN-like toxicity in [0, 1] and signed imbalance [-1, 1].

    Uses fixed-volume buckets on the tail window; degrades gracefully on short series.
    """
    n = len(close)
    if n < 30:
        return {
            "vpin_proxy": None,
            "volume_imbalance": None,
            "bucket_count": 0,
            "method": "vpin_proxy_ohlcv_v1",
        }

    tail = min(n, 120)
    c = close[-tail:].astype(np.float64, copy=False)
    h = high[-tail:].astype(np.float64, copy=False)
    low_tail = low[-tail:].astype(np.float64, copy=False)
    v = volume[-tail:].astype(np.float64, copy=False)

    buy, sell = classify_bar_volume_lee_ready(c, h, low_tail, v)
    total_vol = float(np.sum(v))
    if total_vol <= 0:
        return {
            "vpin_proxy": None,
            "volume_imbalance": None,
            "bucket_count": 0,
            "method": "vpin_proxy_ohlcv_v1",
        }

    imb = float(np.sum(buy - sell) / total_vol)
    target_bucket = max(total_vol / 12.0, 1e-9)
    toxicity_accum: list[float] = []
    bucket_buy = 0.0
    bucket_sell = 0.0
    bucket_vol = 0.0

    for i in range(tail):
        bi, si, vi = float(buy[i]), float(sell[i]), float(v[i])
        bucket_buy += bi
        bucket_sell += si
        bucket_vol += vi
        if bucket_vol >= target_bucket:
            imbalance = abs(bucket_buy - bucket_sell) / max(bucket_vol, 1e-9)
            toxicity_accum.append(imbalance)
            bucket_buy = bucket_sell = bucket_vol = 0.0

    if toxicity_accum:
        vpin = float(np.clip(np.mean(toxicity_accum), 0.0, 1.0))
    else:
        vpin = float(np.clip(abs(imb), 0.0, 1.0))

    return {
        "vpin_proxy": round(vpin, 4),
        "volume_imbalance": round(float(np.clip(imb, -1.0, 1.0)), 4),
        "bucket_count": len(toxicity_accum),
        "method": "vpin_proxy_ohlcv_v1",
    }


def compute_ofi_proxy(close: np.ndarray, volume: np.ndarray, lookback: int = 20) -> float | None:
    """Cumulative microstructure pressure proxy: correlation of price change with signed volume."""
    if len(close) < lookback + 2:
        return None
    dc = np.diff(close[-(lookback + 1) :].astype(np.float64, copy=False))
    dv = volume[-lookback:].astype(np.float64, copy=False)
    signed = np.sign(dc) * dv
    if np.std(signed) < 1e-12:
        return 0.0
    z = (signed - np.mean(signed)) / (np.std(signed) + 1e-12)
    return round(float(np.clip(np.mean(z) / 3.0, -1.0, 1.0)), 4)
