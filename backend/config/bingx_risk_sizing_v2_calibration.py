"""Calibration for Risk & Sizing Engines v2 (BingX scalping)."""

from __future__ import annotations

import os

# VEX-CHEX composite (motor 12)
VEX_CHEX_VEX_WEIGHT: float = 0.60
VEX_CHEX_CHEX_WEIGHT: float = 0.40
VEX_CHEX_TAILWIND: float = 0.50
VEX_CHEX_HEADWIND: float = -0.50
VEX_REF_DEFAULT: float = 1_000_000.0
CHEX_REF_DEFAULT: float = 50_000.0

# IV Rank + VEX override (motor 2)
IVR_VEX_OVERRIDE_CAP: float = 0.25
IVR_HIGH_TAILWIND_CAP: float = 1.30

# Gamma regime survival (motor 10)
GEX_NEG_0DTE_BLOCK: bool = False  # decide() already gates; sizing only reduces
DTE0_DOMINANT_PCT: float = 0.50

# VRP + term structure (motor 8)
VRP_BLOCK_NEGATIVE: bool = True
BACKWARDATION_RATIO: float = 1.05
BACKWARDATION_SIZE_CAP: float = 0.40

# Composite clamps
RISK_SIZING_MIN_MULT: float = 0.10
RISK_SIZING_MAX_MULT: float = 1.50

# Bayesian Kelly (motor ⑬) — operational mapping of the raw fraction [0,1] to a
# usable sizing multiplier: fraction=0 → OPS_MIN, fraction=1 → 1.0.
BAYESIAN_KELLY_OPS_MIN: float = 0.35


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def vex_ref() -> float:
    return _env_float("BINGX_VEX_REF", VEX_REF_DEFAULT)


def chex_ref() -> float:
    return _env_float("BINGX_CHEX_REF", CHEX_REF_DEFAULT)


def bayesian_kelly_ops_min() -> float:
    return _env_float("BINGX_BAYESIAN_KELLY_OPS_MIN", BAYESIAN_KELLY_OPS_MIN)
