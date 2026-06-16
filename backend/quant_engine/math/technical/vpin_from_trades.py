"""VPIN and signed-flow metrics from trade tape (Layer 2 — pure math)."""

from __future__ import annotations

import math
from typing import Any


def compute_vpin_from_signed_volume(
    buy_volumes: list[float],
    sell_volumes: list[float],
    *,
    bucket_target: float | None = None,
) -> dict[str, Any]:
    """VPIN-style toxicity from per-bucket buy/sell volume splits."""
    if not buy_volumes or not sell_volumes or len(buy_volumes) != len(sell_volumes):
        return {
            "vpin": None,
            "volume_imbalance": None,
            "bucket_count": 0,
            "method": "vpin_trade_tape_v1",
        }

    total_buy = sum(max(0.0, float(b)) for b in buy_volumes)
    total_sell = sum(max(0.0, float(s)) for s in sell_volumes)
    total = total_buy + total_sell
    if total <= 0:
        return {
            "vpin": None,
            "volume_imbalance": None,
            "bucket_count": 0,
            "method": "vpin_trade_tape_v1",
        }

    imb = (total_buy - total_sell) / total
    if bucket_target is None or bucket_target <= 0:
        bucket_target = total / max(len(buy_volumes), 1)

    toxicity: list[float] = []
    bucket_buy = bucket_sell = bucket_vol = 0.0
    for b, s in zip(buy_volumes, sell_volumes, strict=False):
        bi, si = max(0.0, float(b)), max(0.0, float(s))
        bucket_buy += bi
        bucket_sell += si
        bucket_vol += bi + si
        if bucket_vol >= bucket_target:
            toxicity.append(abs(bucket_buy - bucket_sell) / max(bucket_vol, 1e-9))
            bucket_buy = bucket_sell = bucket_vol = 0.0

    if toxicity:
        vpin = float(sum(toxicity) / len(toxicity))
    else:
        vpin = abs(imb)

    return {
        "vpin": round(min(1.0, max(0.0, vpin)), 4),
        "volume_imbalance": round(max(-1.0, min(1.0, imb)), 4),
        "bucket_count": len(toxicity),
        "method": "vpin_trade_tape_v1",
    }


def compute_cvd_from_trades(signed_volumes: list[float]) -> dict[str, Any]:
    """Cumulative volume delta from signed trade volumes (+ buy, - sell)."""
    if not signed_volumes:
        return {"cvd": None, "period_delta": None, "trade_count": 0}
    cvd = 0.0
    for vol in signed_volumes:
        if math.isfinite(float(vol)):
            cvd += float(vol)
    period = float(signed_volumes[-1]) if signed_volumes else 0.0
    return {
        "cvd": round(cvd, 4),
        "period_delta": round(period, 4),
        "trade_count": len(signed_volumes),
    }
