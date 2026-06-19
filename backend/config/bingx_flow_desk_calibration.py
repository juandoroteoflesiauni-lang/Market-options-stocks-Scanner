"""Calibration for Flow Desk — OBV-OI + MFI-Flow confluence (Motor scanner). # [PD-8][TH]

Weights here feed the technical consensus matrix in Turno 9. Kept small so the
flow desk nudges rather than dominates the 16-engine vote.
"""

from __future__ import annotations

import os

# Consensus weights (used by the technical matrix in Turno 9).
FLOW_OBV_OI_WEIGHT: float = 0.03
FLOW_MFI_FLOW_WEIGHT: float = 0.02

# Minimum bars to run the scanner pipelines (aligned with the scanner).
FLOW_DESK_MIN_BARS: int = 30

# Optional confluence agreement bonus surfaced on the snapshot.
FLOW_CONFLUENCE_AGREE_BONUS: float = 0.10


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


def flow_obv_oi_weight() -> float:
    return _env_float("BINGX_FLOW_OBV_OI_WEIGHT", FLOW_OBV_OI_WEIGHT)


def flow_mfi_flow_weight() -> float:
    return _env_float("BINGX_FLOW_MFI_FLOW_WEIGHT", FLOW_MFI_FLOW_WEIGHT)


def flow_total_weight() -> float:
    return flow_obv_oi_weight() + flow_mfi_flow_weight()


def flow_desk_min_bars() -> int:
    return _env_int("BINGX_FLOW_DESK_MIN_BARS", FLOW_DESK_MIN_BARS)


def flow_confluence_agree_bonus() -> float:
    return _env_float("BINGX_FLOW_CONFLUENCE_AGREE_BONUS", FLOW_CONFLUENCE_AGREE_BONUS)


__all__ = [
    "FLOW_CONFLUENCE_AGREE_BONUS",
    "FLOW_DESK_MIN_BARS",
    "FLOW_MFI_FLOW_WEIGHT",
    "FLOW_OBV_OI_WEIGHT",
    "flow_confluence_agree_bonus",
    "flow_desk_min_bars",
    "flow_mfi_flow_weight",
    "flow_obv_oi_weight",
    "flow_total_weight",
]
