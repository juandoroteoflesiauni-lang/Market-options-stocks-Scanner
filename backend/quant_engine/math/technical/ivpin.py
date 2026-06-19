"""Improved VPIN (iVPIN) via MLE-inspired volume-time estimation. # [PD-2][TH]

Reference: Ke & Lin (2017) — stabilized VPIN for small buckets / infrequent trades.
"""

from __future__ import annotations

import math
from typing import Any


def _safe_positive(value: float) -> float:
    if not math.isfinite(value) or value < 0:
        return 0.0
    return value


def compute_ivpin(
    buy_volumes: list[float],
    sell_volumes: list[float],
    *,
    bucket_target: float | None = None,
    mle_smoothing: float = 0.15,
) -> dict[str, Any]:
    """Compute improved VPIN with MLE-style volume-time smoothing.

    Args:
        buy_volumes: Per-bucket buy volume series.
        sell_volumes: Per-bucket sell volume series.
        bucket_target: Target volume per bucket; auto-derived if None.
        mle_smoothing: Blend weight toward MLE estimate (0=pure moments, 1=full MLE).

    Returns:
        Dict with ivpin, vpin (legacy alias), volume_imbalance, bucket_count, method.
    """
    if not buy_volumes or not sell_volumes or len(buy_volumes) != len(sell_volumes):
        return {
            "ivpin": None,
            "vpin": None,
            "volume_imbalance": None,
            "bucket_count": 0,
            "method": "ivpin_mle_v1",
        }

    pairs = [
        (_safe_positive(float(b)), _safe_positive(float(s)))
        for b, s in zip(buy_volumes, sell_volumes, strict=False)
    ]
    total_buy = sum(b for b, _ in pairs)
    total_sell = sum(s for _, s in pairs)
    total = total_buy + total_sell
    if total <= 0:
        return {
            "ivpin": None,
            "vpin": None,
            "volume_imbalance": None,
            "bucket_count": 0,
            "method": "ivpin_mle_v1",
        }

    imb = (total_buy - total_sell) / total
    if bucket_target is None or bucket_target <= 0:
        bucket_target = total / max(len(pairs), 1)

    moment_toxicity: list[float] = []
    mle_toxicity: list[float] = []
    bucket_buy = bucket_sell = bucket_vol = 0.0
    for buy_vol, sell_vol in pairs:
        bucket_buy += buy_vol
        bucket_sell += sell_vol
        bucket_vol += buy_vol + sell_vol
        if bucket_vol >= bucket_target:
            imbalance = abs(bucket_buy - bucket_sell)
            moment_toxicity.append(imbalance / max(bucket_vol, 1e-9))
            # MLE-inspired: penalize low-volume buckets with prior toward global imb
            prior = abs(imb)
            obs = imbalance / max(bucket_vol, 1e-9)
            mle_est = (1.0 - mle_smoothing) * obs + mle_smoothing * prior
            mle_toxicity.append(mle_est)
            bucket_buy = bucket_sell = bucket_vol = 0.0

    if mle_toxicity:
        ivpin = float(sum(mle_toxicity) / len(mle_toxicity))
    elif moment_toxicity:
        ivpin = float(sum(moment_toxicity) / len(moment_toxicity))
    else:
        ivpin = abs(imb)

    ivpin_clamped = round(min(1.0, max(0.0, ivpin)), 4)
    return {
        "ivpin": ivpin_clamped,
        "vpin": ivpin_clamped,
        "volume_imbalance": round(max(-1.0, min(1.0, imb)), 4),
        "bucket_count": len(mle_toxicity) or len(moment_toxicity),
        "method": "ivpin_mle_v1",
    }


__all__ = ["compute_ivpin"]
