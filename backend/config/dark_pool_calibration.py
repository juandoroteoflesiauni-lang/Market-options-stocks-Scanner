"""Calibration for Motor ⑭ — Dark Pool detector. # [PD-8][TH]

Thresholds for turning aggregated dark-pool prints into a directional bias and
a confidence score. Env overrides allow recalibration without a redeploy.
"""

from __future__ import annotations

import os

# Signed net notional (USD) beyond which the bias turns directional.
DARK_POOL_BIAS_THRESHOLD_USD: float = 1_000_000.0

# Reference print count for full confidence (count / ref, capped at 1.0).
DARK_POOL_CONFIDENCE_REF_PRINTS: int = 20


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def bias_threshold_usd() -> float:
    return _env_float("DARK_POOL_BIAS_THRESHOLD_USD", DARK_POOL_BIAS_THRESHOLD_USD)


def confidence_ref_prints() -> int:
    return _env_int("DARK_POOL_CONFIDENCE_REF_PRINTS", DARK_POOL_CONFIDENCE_REF_PRINTS)


__all__ = [
    "DARK_POOL_BIAS_THRESHOLD_USD",
    "DARK_POOL_CONFIDENCE_REF_PRINTS",
    "bias_threshold_usd",
    "confidence_ref_prints",
]
