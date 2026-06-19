"""Calibration for Motor ⑬ — Bayesian Kelly sizer. # [PD-8][TH]

Closed-form Beta-Binomial posterior (no SciPy / no MCMC). All tunables here
with env overrides. The prior defaults to ``Beta(1, 1)`` (uniform) so a thin
journal stays conservative without collapsing to a degenerate estimate.
"""

from __future__ import annotations

import os

# Beta prior for the win-rate posterior. Beta(1, 1) = uniform (max entropy).
BAYESIAN_KELLY_PRIOR_ALPHA: float = 1.0
BAYESIAN_KELLY_PRIOR_BETA: float = 1.0

# Halve the Kelly fraction (half-Kelly) to cut variance / drawdown risk.
BAYESIAN_KELLY_HALF_KELLY: bool = True

# Minimum number of journal trades before the estimate is trusted; below this
# the scalar degrades to the neutral 1.0.
BAYESIAN_KELLY_MIN_SAMPLE: int = 12

# Clamp bounds for the resulting fraction.
BAYESIAN_KELLY_MIN_FRACTION: float = 0.0
BAYESIAN_KELLY_MAX_FRACTION: float = 1.0


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


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def prior_alpha() -> float:
    return _env_float("BAYESIAN_KELLY_PRIOR_ALPHA", BAYESIAN_KELLY_PRIOR_ALPHA)


def prior_beta() -> float:
    return _env_float("BAYESIAN_KELLY_PRIOR_BETA", BAYESIAN_KELLY_PRIOR_BETA)


def half_kelly() -> bool:
    return _env_bool("BAYESIAN_KELLY_HALF_KELLY", BAYESIAN_KELLY_HALF_KELLY)


def min_sample() -> int:
    return _env_int("BAYESIAN_KELLY_MIN_SAMPLE", BAYESIAN_KELLY_MIN_SAMPLE)


def min_fraction() -> float:
    return _env_float("BAYESIAN_KELLY_MIN_FRACTION", BAYESIAN_KELLY_MIN_FRACTION)


def max_fraction() -> float:
    return _env_float("BAYESIAN_KELLY_MAX_FRACTION", BAYESIAN_KELLY_MAX_FRACTION)


__all__ = [
    "BAYESIAN_KELLY_HALF_KELLY",
    "BAYESIAN_KELLY_MAX_FRACTION",
    "BAYESIAN_KELLY_MIN_FRACTION",
    "BAYESIAN_KELLY_MIN_SAMPLE",
    "BAYESIAN_KELLY_PRIOR_ALPHA",
    "BAYESIAN_KELLY_PRIOR_BETA",
    "half_kelly",
    "max_fraction",
    "min_fraction",
    "min_sample",
    "prior_alpha",
    "prior_beta",
]
