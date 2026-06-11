"""dealer_flow_dynamics_engine.py
==================================
Models dealer (market-maker) positioning and hedging flow dynamics.

Dealers MUST delta-hedge their options book — that forced hedging creates
predictable, structural price pressure that can be modelled from the OI chain.

Four analytical components:
  1. Net Dealer Delta Exposure (NDDE)  — directional bias from delta hedging
  2. Charm Flow Intraday               — time-decay rebalancing pressure per hour
  3. Vanna Flow                        — delta shift from implied-vol movement
  4. Gamma Pinning Zone                — strike that acts as gravitational price anchor

Public API
----------
get_dealer_flow_dynamics(options_chain, spot, vix, time_to_expiry, rate) -> dict
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]

from backend.config.logger_setup import get_logger

warnings.filterwarnings("ignore", category=RuntimeWarning)

logger = get_logger(__name__)

# Intraday hourly grid: 9am, 10am, …, 4pm (7 hourly intervals including open)
_INTRADAY_HOURS = [9, 10, 11, 12, 13, 14, 15]  # hour labels (integer)
_TRADING_HOURS = 6.5  # total session length in hours
_HOURS_PER_YEAR = 252 * _TRADING_HOURS

# Spot² GEX multiplier — converts dealer gamma exposure to dollar-gamma units
_GEX_SPOT_SCALE = 0.01

# Normalisation cap: extreme NDDE beyond ±1 gets clipped, not scaled
_NDDE_NORM_CAP = 1e6  # fallback if no historical max available

# Minimum GEX to consider a level a "wall" (avoids noise near zero)
_WALL_MIN_FRACTION = 0.10  # must be at least 10% of peak GEX to qualify

# Weight split for combined dealer signal
_NDDE_WEIGHT = 0.60
_VANNA_WEIGHT = 0.40


def get_dealer_flow_dynamics(
    options_chain: pd.DataFrame,
    spot: float,
    vix: float,
    time_to_expiry: float,
    rate: float,
) -> dict[str, Any]:
    """Model dealer positioning and hedging flow from an options chain.

    Parameters
    ----------
    options_chain    : DataFrame with columns [strike, open_interest,
                       call_oi, put_oi, delta, gamma, vanna, charm, implied_vol]
    spot             : Current spot price
    vix              : VIX level (percentage points, e.g. 20.0 for 20%)
    time_to_expiry   : Time to expiry in years
    rate             : Risk-free rate (annualised)

    Returns
    -------
    dict with keys:
        ndde, ndde_signal, charm_flow_series, vanna_flow, vanna_pressure,
        pinning_strike, pinning_probability, gamma_wall_up, gamma_wall_down,
        gex_by_strike, dealer_directional_signal, error_msg (only on failure)
    """
    required = {"strike", "call_oi", "put_oi", "delta", "gamma", "vanna", "charm"}
    missing = required - set(options_chain.columns)
    if missing:
        return {"error_msg": f"Missing columns: {missing}"}

    chain = (
        options_chain.copy()
        .dropna(subset=["strike", "call_oi", "put_oi", "delta", "gamma"])
        .sort_values("strike")
        .reset_index(drop=True)
    )

    if len(chain) < 5:
        return {"error_msg": f"Insufficient valid strikes: {len(chain)} < 5"}

    strikes = chain["strike"].to_numpy(dtype=float)
    call_oi = chain["call_oi"].to_numpy(dtype=float)
    put_oi = chain["put_oi"].to_numpy(dtype=float)
    delta = chain["delta"].to_numpy(dtype=float)
    gamma = chain["gamma"].to_numpy(dtype=float)
    vanna = np.nan_to_num(chain["vanna"].to_numpy(dtype=float), nan=0.0)
    charm = np.nan_to_num(chain["charm"].to_numpy(dtype=float), nan=0.0)

    # ------------------------------------------------------------------ #
    # 1. NET DEALER DELTA EXPOSURE (NDDE)                                  #
    # ------------------------------------------------------------------ #
    # Dealers are counterparty: customer buys call → dealer short call →
    # dealer has negative delta → must BUY spot to hedge.
    # NDDE = -Σ (call_OI × Δ_call - put_OI × Δ_put)
    # Positive NDDE → net buying pressure from dealer hedging.
    raw_ndde = -np.sum(call_oi * delta - put_oi * np.abs(delta))
    ndde = float(raw_ndde)

    # Normalise to [-1, 1].  Use total OI × 0.5 as natural scale (max |delta|=0.5 ATM).
    total_oi = float(np.sum(call_oi + put_oi))
    natural_scale = total_oi * 0.5 if total_oi > 0 else _NDDE_NORM_CAP
    ndde_signal = float(np.clip(ndde / max(natural_scale, 1.0), -1.0, 1.0))

    logger.debug("dealer_flow.ndde=%.0f ndde_signal=%.4f", ndde, ndde_signal)

    # ------------------------------------------------------------------ #
    # 2. CHARM FLOW INTRADAY                                               #
    # ------------------------------------------------------------------ #
    # charm = ∂Δ/∂t — as time passes delta changes, forcing dealer rebalance.
    # charm_flow(t) = Σ OI × charm × Δt   (Δt = 1 hour as fraction of year)
    dt_hour = 1.0 / _HOURS_PER_YEAR
    total_oi_per_strike = call_oi + put_oi  # OI-weighted charm (net position)

    # Charm sign: for calls charm is negative (delta decays toward 0 for OTM),
    # for puts positive.  We use the chain's net OI and the reported charm value.
    net_charm_per_strike = (call_oi - put_oi) * charm

    per_hour_flow = float(np.sum(net_charm_per_strike) * dt_hour)
    # Series: each hour has the same marginal flow (linear decay model)
    charm_flow_series = [round(per_hour_flow * h, 6) for h in range(1, 8)]

    # ------------------------------------------------------------------ #
    # 3. VANNA FLOW                                                        #
    # ------------------------------------------------------------------ #
    # vanna = ∂Δ/∂σ — when IV moves, dealer delta shifts, forcing hedge.
    # Expected vol change proxy: vix / 100 (one standard-deviation move in vol).
    vix_change = float(vix) / 100.0
    vanna_flow = float(np.sum(total_oi_per_strike * vanna) * vix_change)

    # Normalise vanna_pressure to [-1, 1] using same natural scale
    vanna_pressure = float(np.clip(vanna_flow / max(natural_scale, 1.0), -1.0, 1.0))

    logger.debug("dealer_flow.vanna_flow=%.2f pressure=%.4f", vanna_flow, vanna_pressure)

    # ------------------------------------------------------------------ #
    # 4. GAMMA PINNING ZONE                                                #
    # ------------------------------------------------------------------ #
    # GEX_i = (call_OI_i - put_OI_i) × Γ_i × S² × 0.01
    # Positive GEX → dealers long gamma → stabilising (pinning) behaviour.
    gex = (call_oi - put_oi) * gamma * spot**2 * _GEX_SPOT_SCALE
    gex_by_strike: dict[str, float] = {
        str(round(k, 4)): round(float(g), 4) for k, g in zip(strikes, gex, strict=False)
    }

    # Pinning strike: maximum |GEX|
    abs_gex = np.abs(gex)
    pin_idx = int(np.argmax(abs_gex))
    pinning_strike = float(strikes[pin_idx])

    # Pinning probability: logistic function of peak GEX relative to total
    total_abs_gex = float(np.sum(abs_gex))
    peak_gex_fraction = float(abs_gex[pin_idx]) / max(total_abs_gex, 1e-12)
    # Maps [0, 1] concentration → [0, 1] probability via logistic
    pinning_probability = float(1.0 / (1.0 + np.exp(-10.0 * (peak_gex_fraction - 0.20))))
    pinning_probability = round(min(pinning_probability, 1.0), 4)

    # Gamma walls: highest positive GEX above/below spot
    gex_series = pd.Series(gex, index=strikes)
    strikes_above = gex_series[strikes > spot]
    strikes_below = gex_series[strikes <= spot]

    peak_threshold = float(abs_gex.max()) * _WALL_MIN_FRACTION

    gamma_wall_up = _find_wall(strikes_above, peak_threshold, direction="up")
    gamma_wall_down = _find_wall(strikes_below, peak_threshold, direction="down")

    # ------------------------------------------------------------------ #
    # 5. COMBINED DEALER DIRECTIONAL SIGNAL                                #
    # ------------------------------------------------------------------ #
    combined = _NDDE_WEIGHT * ndde_signal + _VANNA_WEIGHT * vanna_pressure
    dealer_directional_signal = float(np.clip(combined, -1.0, 1.0))

    return {
        "ndde": round(ndde, 2),
        "ndde_signal": round(ndde_signal, 4),
        "charm_flow_series": charm_flow_series,
        "vanna_flow": round(vanna_flow, 4),
        "vanna_pressure": round(vanna_pressure, 4),
        "pinning_strike": round(pinning_strike, 4),
        "pinning_probability": pinning_probability,
        "gamma_wall_up": round(gamma_wall_up, 4) if gamma_wall_up is not None else None,
        "gamma_wall_down": round(gamma_wall_down, 4) if gamma_wall_down is not None else None,
        "gex_by_strike": gex_by_strike,
        "dealer_directional_signal": round(dealer_directional_signal, 4),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_wall(
    gex_slice: pd.Series,
    threshold: float,
    direction: str,
) -> float | None:
    """Return the strike with the highest positive GEX in a directional slice."""
    qualified = gex_slice[gex_slice >= threshold]
    if qualified.empty:
        return None
    if direction == "up":
        # Lowest qualified strike above spot (first resistance)
        return float(qualified.index.min())
    else:
        # Highest qualified strike below spot (first support)
        return float(qualified.index.max())
