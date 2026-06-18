"""Calibration for SignalCombiner bridge into BingX decide()."""

from __future__ import annotations

import os

# Minimum |score| from combiner to assign LONG/SHORT (score range ±100)
DEFAULT_COMBINER_ENTRY_SCORE: float = 35.0

# Blend for _options_score: quality of snapshot vs directional strength
DEFAULT_COMBINER_QUALITY_WEIGHT: float = 0.4
DEFAULT_COMBINER_OPTIONS_SCORE_WEIGHT: float = 0.6

# Penalties applied in decide() when combiner flags risk
DEFAULT_COMBINER_CONTRADICTION_PENALTY: float = 0.12
DEFAULT_COMBINER_EXTREME_RISK_PENALTY: float = 0.25
DEFAULT_COMBINER_EXTREME_BLOCKS: bool = False


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def combiner_entry_score() -> float:
    return max(1.0, _env_float("BINGX_COMBINER_ENTRY_SCORE", DEFAULT_COMBINER_ENTRY_SCORE))


def combiner_quality_weight() -> float:
    return _env_float("BINGX_COMBINER_QUALITY_WEIGHT", DEFAULT_COMBINER_QUALITY_WEIGHT)


def combiner_options_score_weight() -> float:
    return _env_float("BINGX_COMBINER_OPTIONS_SCORE_WEIGHT", DEFAULT_COMBINER_OPTIONS_SCORE_WEIGHT)


def combiner_contradiction_penalty() -> float:
    return _env_float(
        "BINGX_COMBINER_CONTRADICTION_PENALTY",
        DEFAULT_COMBINER_CONTRADICTION_PENALTY,
    )


def combiner_extreme_risk_penalty() -> float:
    return _env_float(
        "BINGX_COMBINER_EXTREME_RISK_PENALTY",
        DEFAULT_COMBINER_EXTREME_RISK_PENALTY,
    )


def combiner_extreme_blocks() -> bool:
    return _env_bool("BINGX_COMBINER_EXTREME_BLOCKS", DEFAULT_COMBINER_EXTREME_BLOCKS)
