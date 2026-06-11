"""gamma_exposure_engine.py
==========================
Unified dealer gamma / delta exposure engine.

Merges three previously separate engines:
  - gamma_flip_engine.py  → flip point detection, OI heatmap
  - dex_engine.py         → delta exposure (DEX) per strike
  - dealer_flow_dynamics_engine.py → NDDE, gamma walls

Public API
----------
get_gamma_exposure(options_chain, spot, rate, tte) -> dict

Migration note: gamma_flip_engine.py and dex_engine.py are deprecated.
  Their classes (GammaFlipEngine, DeltaExposureEngine) remain importable
  from their original modules for backward compatibility with existing
  router / frontend consumers.  New code should call get_gamma_exposure().
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy import stats  # type: ignore[import-untyped]
from scipy.optimize import brentq  # type: ignore[import-untyped]

from backend.config.logger_setup import get_logger

warnings.filterwarnings("ignore", category=RuntimeWarning)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CONTRACT_MULTIPLIER = 100
_GEX_SPOT_SCALE = 0.01
_WALL_MIN_FRACTION = 0.10  # wall must be ≥ 10 % of peak |GEX|
_NDDE_NORM_CAP = 1e6
_FLIP_SCAN_RANGE = 0.18  # ±18 % around spot
_FLIP_SCAN_POINTS = 140
_NEUTRAL_BAND = 0.10  # ±10 % of natural_scale → NEUTRAL_GAMMA
_FLIP_SIGNAL_SCALE = 0.15  # spot distance at which flip_signal saturates


# ---------------------------------------------------------------------------
# Black-Scholes gamma (identical to gamma_flip_engine.bs_gamma)
# ---------------------------------------------------------------------------


def _bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """N'(d1) / (S σ √T) — Black-Scholes gamma, call = put."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return float(stats.norm.pdf(d1) / (S * sigma * np.sqrt(T)))


def _net_gamma_at_price(
    price: float,
    strikes: np.ndarray[Any, np.dtype[Any]],
    call_oi: np.ndarray[Any, np.dtype[Any]],
    put_oi: np.ndarray[Any, np.dtype[Any]],
    T: float,
    r: float,
    sigma: float,
    contract_size: int = _CONTRACT_MULTIPLIER,
) -> float:
    """Net system gamma at a hypothetical spot level (re-priced via BS)."""
    gammas = np.array([_bs_gamma(price, K, T, r, sigma) for K in strikes])
    call_gamma = float(np.sum(gammas * call_oi))
    put_gamma = float(np.sum(gammas * put_oi))
    return (call_gamma - put_gamma) * contract_size


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def get_gamma_exposure(
    options_chain: pd.DataFrame,
    spot: float,
    rate: float,
    tte: float,
    sigma: float | None = None,
    contract_size: int = _CONTRACT_MULTIPLIER,
) -> dict[str, Any]:
    """Unified dealer gamma / delta exposure analysis.

    Parameters
    ----------
    options_chain : DataFrame with columns:
                    strike, call_oi, put_oi, delta (call delta ∈ (0,1]),
                    gamma (per-strike BS gamma)
                    Optional: implied_vol (used for sigma if sigma not given)
    spot          : Current underlying spot price
    rate          : Risk-free rate (annualised)
    tte           : Time to expiry in years
    sigma         : Implied vol for flip-point repricing (defaults to chain median IV
                    or 0.20 if unavailable)
    contract_size : Shares per contract (default 100)

    Returns
    -------
    dict with keys:
        flip_point       : float | None  — strike where net gamma crosses zero
        flip_signal      : float ∈ [-1,1] — positive = long gamma (anchored market)
        dex_net          : float          — net delta exposure across full chain ($)
        ndde             : float          — Net Dealer Delta Exposure (contract-count units)
        gamma_wall_up    : float | None   — nearest gamma wall above spot
        gamma_wall_down  : float | None   — nearest gamma wall at/below spot
        gex_by_strike    : dict           — {strike_str: gex_value} for heatmap
        regime_context   : str            — "LONG_GAMMA" | "SHORT_GAMMA" | "NEUTRAL_GAMMA"
        directional_signal: float ∈ [-1,1] — combined dealer directional signal
        error_msg        : str            — present only on validation failure
    """
    required = {"strike", "call_oi", "put_oi", "delta", "gamma"}
    missing = required - set(options_chain.columns)
    if missing:
        return {"error_msg": f"Missing columns: {missing}"}

    chain = (
        options_chain.copy()
        .dropna(subset=["strike", "call_oi", "put_oi", "delta", "gamma"])
        .sort_values("strike")
        .reset_index(drop=True)
    )
    if len(chain) < 4:
        return {"error_msg": f"Insufficient valid rows: {len(chain)} (need ≥ 4)"}

    strikes = chain["strike"].to_numpy(dtype=float)
    call_oi = chain["call_oi"].to_numpy(dtype=float)
    put_oi = chain["put_oi"].to_numpy(dtype=float)
    delta = chain["delta"].to_numpy(dtype=float)  # call-side delta ∈ (0,1]
    gamma_v = chain["gamma"].to_numpy(dtype=float)

    # Resolve sigma for flip-point repricing
    if sigma is None:
        if "implied_vol" in chain.columns:
            iv = chain["implied_vol"].dropna()
            sigma = float(iv.median()) if not iv.empty else 0.20
        else:
            sigma = 0.20
    sigma = float(max(0.05, min(sigma, 2.0)))

    # ------------------------------------------------------------------ #
    # 1. DELTA EXPOSURE (DEX) — SpotGamma convention                      #
    # ------------------------------------------------------------------ #
    # DEX = Σ_strike  (call_OI × delta_call - put_OI × |delta_put|) × 100 × spot
    # delta from chain is call-side; put_delta ≈ -(1 - delta) for same strike.
    put_delta_abs = np.clip(1.0 - delta, 0.0, 1.0)  # |Δ_put| approximation
    dex_per_strike = (call_oi * delta - put_oi * put_delta_abs) * contract_size * spot
    dex_net = float(np.sum(dex_per_strike))

    # ------------------------------------------------------------------ #
    # 2. NET DEALER DELTA EXPOSURE (NDDE)                                  #
    # ------------------------------------------------------------------ #
    # Dealers as counterparty: NDDE = -Σ(call_OI × Δ - put_OI × |Δ_put|)
    # Positive NDDE → dealers net long delta → buy pressure from hedging.
    raw_ndde = -float(np.sum(call_oi * delta - put_oi * put_delta_abs))
    ndde = raw_ndde

    total_oi = float(np.sum(call_oi + put_oi))
    natural_scale = total_oi * 0.5 if total_oi > 0 else _NDDE_NORM_CAP
    ndde_signal = float(np.clip(ndde / max(natural_scale, 1.0), -1.0, 1.0))

    # ------------------------------------------------------------------ #
    # 3. GEX BY STRIKE & GAMMA WALLS                                       #
    # ------------------------------------------------------------------ #
    gex = (call_oi - put_oi) * gamma_v * spot**2 * _GEX_SPOT_SCALE
    gex_by_strike: dict[str, float] = {
        str(round(float(k), 4)): round(float(g), 4) for k, g in zip(strikes, gex, strict=False)
    }

    abs_gex = np.abs(gex)
    peak_threshold = float(abs_gex.max()) * _WALL_MIN_FRACTION

    gex_series = pd.Series(gex, index=strikes)
    above_spot = gex_series[strikes > spot]
    at_or_below = gex_series[strikes <= spot]

    gamma_wall_up = _find_wall(above_spot, peak_threshold, "up")
    gamma_wall_down = _find_wall(at_or_below, peak_threshold, "down")

    # ------------------------------------------------------------------ #
    # 4. FLIP POINT — zero-crossing of net gamma via Brentq               #
    # ------------------------------------------------------------------ #
    p_lo = spot * (1 - _FLIP_SCAN_RANGE)
    p_hi = spot * (1 + _FLIP_SCAN_RANGE)
    price_range = np.linspace(p_lo, p_hi, _FLIP_SCAN_POINTS)

    net_gammas = np.array(
        [
            _net_gamma_at_price(p, strikes, call_oi, put_oi, tte, rate, sigma, contract_size)
            for p in price_range
        ]
    )

    flip_point = _find_flip_point(
        price_range, net_gammas, strikes, call_oi, put_oi, tte, rate, sigma, contract_size
    )

    # ------------------------------------------------------------------ #
    # 5. FLIP SIGNAL ∈ [-1, 1]                                             #
    # ------------------------------------------------------------------ #
    # Positive = spot above flip = dealers long gamma = market anchored.
    # Negative = spot below flip = dealers short gamma = market amplified.
    if flip_point is not None:
        dist_frac = (spot - flip_point) / (spot * _FLIP_SIGNAL_SCALE)
        flip_signal = float(np.clip(dist_frac, -1.0, 1.0))
    else:
        # No flip in scan range: use sign of net gamma at spot
        ng_at_spot = _net_gamma_at_price(
            spot, strikes, call_oi, put_oi, tte, rate, sigma, contract_size
        )
        flip_signal = 1.0 if ng_at_spot >= 0 else -1.0

    # ------------------------------------------------------------------ #
    # 6. REGIME CONTEXT                                                    #
    # ------------------------------------------------------------------ #
    ng_at_spot = _net_gamma_at_price(
        spot, strikes, call_oi, put_oi, tte, rate, sigma, contract_size
    )
    neutral_band = natural_scale * _NEUTRAL_BAND
    if abs(ng_at_spot) < neutral_band:
        regime_context = "NEUTRAL_GAMMA"
    elif ng_at_spot > 0:
        regime_context = "LONG_GAMMA"
    else:
        regime_context = "SHORT_GAMMA"

    # ------------------------------------------------------------------ #
    # 7. COMBINED DIRECTIONAL SIGNAL                                       #
    # ------------------------------------------------------------------ #
    # flip_signal (60 %) + ndde_signal (40 %)
    directional_signal = float(np.clip(0.60 * flip_signal + 0.40 * ndde_signal, -1.0, 1.0))

    logger.debug(
        "gamma_exposure flip=%.2f flip_signal=%.3f dex_net=%.0f ndde=%.0f regime=%s",
        flip_point or float("nan"),
        flip_signal,
        dex_net,
        ndde,
        regime_context,
    )

    return {
        "flip_point": round(flip_point, 4) if flip_point is not None else None,
        "flip_signal": round(flip_signal, 4),
        "dex_net": round(dex_net, 2),
        "ndde": round(ndde, 2),
        "gamma_wall_up": round(gamma_wall_up, 4) if gamma_wall_up is not None else None,
        "gamma_wall_down": round(gamma_wall_down, 4) if gamma_wall_down is not None else None,
        "gex_by_strike": gex_by_strike,
        "regime_context": regime_context,
        "directional_signal": round(directional_signal, 4),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_flip_point(
    price_range: np.ndarray[Any, np.dtype[Any]],
    net_gammas: np.ndarray[Any, np.dtype[Any]],
    strikes: np.ndarray[Any, np.dtype[Any]],
    call_oi: np.ndarray[Any, np.dtype[Any]],
    put_oi: np.ndarray[Any, np.dtype[Any]],
    T: float,
    r: float,
    sigma: float,
    contract_size: int,
) -> float | None:
    sign_changes = np.where(np.diff(np.sign(net_gammas)))[0]
    if len(sign_changes) == 0:
        return None

    idx = sign_changes[0]
    p_lo, p_hi = price_range[idx], price_range[idx + 1]

    def f(p: float) -> float:
        return _net_gamma_at_price(p, strikes, call_oi, put_oi, T, r, sigma, contract_size)

    try:
        return float(brentq(f, p_lo, p_hi, xtol=1e-6, maxiter=200))
    except ValueError:
        g_lo, g_hi = net_gammas[idx], net_gammas[idx + 1]
        return float(p_lo - g_lo * (p_hi - p_lo) / (g_hi - g_lo))


def _find_wall(gex_slice: pd.Series, threshold: float, direction: str) -> float | None:
    """Highest-GEX strike meeting threshold, directionally selected."""
    qualified = gex_slice[gex_slice >= threshold]
    if qualified.empty:
        return None
    return float(qualified.index.min()) if direction == "up" else float(qualified.index.max())
