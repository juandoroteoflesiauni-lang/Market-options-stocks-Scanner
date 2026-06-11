"""risk_neutral_density_engine.py
=================================
Extracts the complete risk-neutral (Q-measure) density from an options chain
using the Breeden-Litzenberger second-derivative method.

Public API
----------
get_risk_neutral_density(options_chain, spot, rate, time_to_expiry) -> dict
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]
from scipy.interpolate import CubicSpline  # type: ignore[import-not-found, import-untyped]

from backend.config.logger_setup import get_logger

warnings.filterwarnings("ignore", category=RuntimeWarning)

logger = get_logger(__name__)

# Grid resolution for RND evaluation
_GRID_POINTS = 300
# Minimum strikes required
_MIN_STRIKES = 5
# Integration tolerance check bounds
_INTEGRAL_LO = 0.85
_INTEGRAL_HI = 1.15
# Bimodal detection: peaks must be > 5% of strike range apart
_BIMODAL_GAP_FRACTION = 0.05


def get_risk_neutral_density(
    options_chain: pd.DataFrame,
    spot: float,
    rate: float,
    time_to_expiry: float,
) -> dict[str, Any]:
    """Extract risk-neutral density via Breeden-Litzenberger.

    Parameters
    ----------
    options_chain    : DataFrame with columns [strike, call_price, put_price,
                       implied_vol, open_interest]
    spot             : Current spot price
    rate             : Risk-free rate (annualised, e.g. 0.05)
    time_to_expiry   : Time to expiry in years (e.g. 30/365)

    Returns
    -------
    dict with keys: rnd_strikes, rnd_density, modal_price, q_mean, q_std,
    q_skewness, q_kurtosis, is_bimodal, bimodal_peaks, directional_signal,
    percentile_05, percentile_95.
    On error (< 5 strikes): dict with error_msg key.
    """
    required = {"strike", "call_price"}
    missing = required - set(options_chain.columns)
    if missing:
        return {"error_msg": f"Missing columns: {missing}"}

    chain = options_chain.copy()
    chain = chain.dropna(subset=["strike", "call_price"])
    chain = chain.sort_values("strike").reset_index(drop=True)
    chain = chain.drop_duplicates("strike", keep="first")

    strikes = chain["strike"].to_numpy(dtype=float)
    calls = chain["call_price"].to_numpy(dtype=float)

    if len(strikes) < _MIN_STRIKES:
        return {"error_msg": (f"Insufficient strikes: {len(strikes)} < {_MIN_STRIKES} required")}

    if spot <= 0:
        return {"error_msg": "Invalid spot for RND extraction"}

    discount = np.exp(rate * time_to_expiry)

    # Breeden-Litzenberger is highly sensitive to noisy, irregular real-market
    # strike spacing. Clean extreme tails, interpolate, then evaluate a uniform grid.
    clean_mask = (strikes > spot * 0.70) & (strikes < spot * 1.30)
    strikes_clean = strikes[clean_mask]
    calls_clean = calls[clean_mask]
    if len(strikes_clean) < _MIN_STRIKES:
        return {"error_msg": "Insuficientes strikes en rango +/-30%"}
    cs = CubicSpline(strikes_clean, calls_clean, extrapolate=False)

    k_min = float(strikes_clean.min())
    k_max = float(strikes_clean.max())
    grid = np.linspace(k_min, k_max, _GRID_POINTS)
    dk = grid[1] - grid[0]

    # Breeden-Litzenberger: RND(K) = e^(rT) × d²C/dK²
    call_interp = np.nan_to_num(cs(grid), nan=0.0, posinf=0.0, neginf=0.0)
    rnd_raw = discount * np.gradient(np.gradient(call_interp, dk), dk)

    # Clip negatives (numerical artifacts) and normalise
    rnd_raw = np.clip(rnd_raw, 0.0, None)

    # Compute integral for normalisation check
    total_mass = np.trapezoid(rnd_raw, grid)

    if total_mass <= 0:
        return {"error_msg": "RND integrates to zero — check call price data"}

    rnd = rnd_raw / total_mass  # normalised density
    normalised_mass = np.trapezoid(rnd, grid)

    if not (_INTEGRAL_LO <= normalised_mass <= _INTEGRAL_HI):
        logger.warning(
            "rnd_engine: density integrates to %.4f (expected ~1.0) — proceeding",
            normalised_mass,
        )

    # --- Moments (numerical integration via trapezoidal rule) ----------------
    q_mean = float(np.trapezoid(grid * rnd, grid))
    q_var = float(np.trapezoid((grid - q_mean) ** 2 * rnd, grid))
    q_std = float(np.sqrt(max(q_var, 0.0)))

    if q_std > 0:
        q_skewness = float(np.trapezoid(((grid - q_mean) / q_std) ** 3 * rnd, grid))
        q_kurtosis = float(np.trapezoid(((grid - q_mean) / q_std) ** 4 * rnd, grid) - 3.0)
    else:
        q_skewness = 0.0
        q_kurtosis = 0.0

    # --- Modal price ---------------------------------------------------------
    peak_idx = int(np.argmax(rnd))
    modal_price = float(grid[peak_idx])

    # --- Bimodal detection ---------------------------------------------------
    is_bimodal, bimodal_peaks = _detect_bimodal(grid, rnd, k_min, k_max)

    # --- Percentiles (CDF via cumulative trapz) -------------------------------
    cdf = np.cumsum(rnd) * dk
    cdf = np.clip(cdf / cdf[-1], 0.0, 1.0)  # normalise CDF to [0,1]

    percentile_05 = float(_interpolate_percentile(grid, cdf, 0.05))
    percentile_95 = float(_interpolate_percentile(grid, cdf, 0.95))

    # --- Directional signal from skewness ------------------------------------
    # Clip skewness to ±3 then normalise to [-1, 1]
    directional_signal = float(np.clip(q_skewness / 3.0, -1.0, 1.0))

    return {
        "rnd_strikes": grid.tolist(),
        "rnd_density": rnd.tolist(),
        "modal_price": round(modal_price, 4),
        "q_mean": round(q_mean, 4),
        "q_std": round(q_std, 4),
        "q_skewness": round(q_skewness, 6),
        "q_kurtosis": round(q_kurtosis, 6),
        "is_bimodal": is_bimodal,
        "bimodal_peaks": [round(p, 4) for p in bimodal_peaks],
        "directional_signal": round(directional_signal, 4),
        "percentile_05": round(percentile_05, 4),
        "percentile_95": round(percentile_95, 4),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_bimodal(
    grid: np.ndarray[Any, np.dtype[Any]],
    rnd: np.ndarray[Any, np.dtype[Any]],
    k_min: float,
    k_max: float,
) -> tuple[bool, list[float]]:
    """Detect two local density maxima separated by > 5% of the strike range."""
    strike_range = k_max - k_min
    min_gap = _BIMODAL_GAP_FRACTION * strike_range

    # Local maxima: interior points higher than both neighbours
    local_max_idx = [
        i for i in range(1, len(grid) - 1) if rnd[i] > rnd[i - 1] and rnd[i] > rnd[i + 1]
    ]

    if len(local_max_idx) < 2:
        return False, []

    # Sort maxima by density height descending
    local_max_idx.sort(key=lambda i: rnd[i], reverse=True)

    # Greedy pair: pick dominant peak, find next that is far enough away
    primary = local_max_idx[0]
    for candidate in local_max_idx[1:]:
        if abs(grid[candidate] - grid[primary]) >= min_gap:
            peaks = sorted([float(grid[primary]), float(grid[candidate])])
            return True, peaks

    return False, []


def _interpolate_percentile(
    grid: np.ndarray[Any, np.dtype[Any]], cdf: np.ndarray[Any, np.dtype[Any]], p: float
) -> float:
    """Return strike at given CDF probability via linear interpolation."""
    idx = np.searchsorted(cdf, p)
    if idx == 0:
        return float(grid[0])
    if idx >= len(grid):
        return float(grid[-1])
    # Linear interpolation between idx-1 and idx
    slope = (grid[idx] - grid[idx - 1]) / max(cdf[idx] - cdf[idx - 1], 1e-12)
    return float(grid[idx - 1] + slope * (p - cdf[idx - 1]))
