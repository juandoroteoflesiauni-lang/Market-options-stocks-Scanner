"""Calibration for Motor ④ — GEX Wall Stop + Color Decay (BingX). # [PD-8][TH]

All tunables live here (no magic numbers in the service). Every constant has
an env override so the desk can recalibrate without a redeploy. Buffers and
thresholds are expressed as documented units to avoid ambiguity:

- ``GEX_WALL_BASE_BUFFER_PCT`` is a *fraction* of the wall price (0.005 = 0.5%).
- ``GEX_WALL_PROXIMITY_PCT`` is a *percent* value, matching the units of
  ``wall_distance_pct`` already produced by the options bridge.
"""

from __future__ import annotations

import os

# Master switch — when False the service degrades to a no-op neutral result.
GEX_WALL_STOP_ENABLED: bool = True

# Base buffer as a fraction of the wall price (0.005 = 0.5%). The stop is
# anchored just inside the wall by this buffer.
GEX_WALL_BASE_BUFFER_PCT: float = 0.005

# Proximity threshold (percent units, same scale as ``wall_distance_pct``).
# When the directional wall is within this distance the stop becomes active
# and forces SIZE_DOWN in decide().
GEX_WALL_PROXIMITY_PCT: float = 1.5

# Color-decay sensitivity: higher k → faster buffer erosion as spot departs
# from zero gamma while in a negative-GEX (dealer-short-gamma) regime.
GEX_COLOR_DECAY_K: float = 2.0

# Cap on erosion so the adaptive buffer never collapses fully to zero.
GEX_EROSION_MAX: float = 0.80

# Sizing multiplier applied when the wall stop is active (proximity hit but
# direction still valid).
GEX_WALL_SIZE_DOWN_MULT: float = 0.60


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def gex_wall_stop_enabled() -> bool:
    return _env_bool("BINGX_GEX_WALL_STOP_ENABLED", GEX_WALL_STOP_ENABLED)


def base_buffer_pct() -> float:
    return _env_float("BINGX_GEX_WALL_BASE_BUFFER_PCT", GEX_WALL_BASE_BUFFER_PCT)


def proximity_pct() -> float:
    return _env_float("BINGX_GEX_WALL_PROXIMITY_PCT", GEX_WALL_PROXIMITY_PCT)


def color_decay_k() -> float:
    return _env_float("BINGX_GEX_COLOR_DECAY_K", GEX_COLOR_DECAY_K)


def erosion_max() -> float:
    return _env_float("BINGX_GEX_EROSION_MAX", GEX_EROSION_MAX)


def size_down_mult() -> float:
    return _env_float("BINGX_GEX_WALL_SIZE_DOWN_MULT", GEX_WALL_SIZE_DOWN_MULT)


__all__ = [
    "GEX_COLOR_DECAY_K",
    "GEX_EROSION_MAX",
    "GEX_WALL_BASE_BUFFER_PCT",
    "GEX_WALL_PROXIMITY_PCT",
    "GEX_WALL_SIZE_DOWN_MULT",
    "GEX_WALL_STOP_ENABLED",
    "base_buffer_pct",
    "color_decay_k",
    "erosion_max",
    "gex_wall_stop_enabled",
    "proximity_pct",
    "size_down_mult",
]
