from __future__ import annotations
"""
backend/engine/metrics/speed_instability.py
Sector: Options / Speed Instability Engine
[ARCH-1, PD-4]

Theoretical basis:
    Speed instability (d_gamma / d_spot) portfolio SWX profile, traps, decay, GEX vs SWX.
    Vectorized spot profile over options chain data.
    Purely stateless, synchronous, offline, and pandas-free.
"""


import logging
import warnings

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.stats import norm


from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.speed_instability")

type FloatArray = npt.NDArray[np.float64]

warnings.filterwarnings("ignore", category=RuntimeWarning)

MULTIPLIER = 100


# ── Black-Scholes Greeks Helper Functions ───────────────────────────────────────


def _d1(
    spot_val: FloatArray,
    strike_val: FloatArray,
    rate: float,
    sigma: FloatArray,
    tte: FloatArray,
) -> FloatArray:
    """Calculates Black-Scholes d1 vectorially, handling edge cases."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(
            (tte > 1e-10) & (sigma > 1e-10),
            (np.log(spot_val / strike_val) + (rate + 0.5 * sigma**2) * tte)
            / (sigma * np.sqrt(tte)),
            np.where(spot_val >= strike_val, np.inf, -np.inf),
        )


def bs_gamma(
    spot_val: FloatArray,
    strike_val: FloatArray,
    rate: float,
    sigma: FloatArray,
    tte: FloatArray,
) -> FloatArray:
    """Calculates Black-Scholes Gamma vectorially."""
    d1_val = _d1(spot_val, strike_val, rate, sigma, tte)
    sqrt_t = np.sqrt(np.maximum(tte, 1e-10))
    denom = spot_val * sigma * sqrt_t
    return np.where(denom > 1e-12, norm.pdf(d1_val) / denom, 0.0)


def bs_speed(
    spot_val: FloatArray,
    strike_val: FloatArray,
    rate: float,
    sigma: FloatArray,
    tte: FloatArray,
) -> FloatArray:
    """Calculates Black-Scholes Speed (d_gamma / d_spot) vectorially."""
    d1_val = _d1(spot_val, strike_val, rate, sigma, tte)
    gamma = bs_gamma(spot_val, strike_val, rate, sigma, tte)
    sqrt_t = np.sqrt(np.maximum(tte, 1e-10))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(
            (sigma > 1e-10) & (tte > 1e-10),
            -(gamma / np.maximum(spot_val, 1e-10)) * (1.0 + d1_val / (sigma * sqrt_t)),
            0.0,
        )


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class GammaTrap(BaseModel):
    """Option strike showing abnormally high Speed and SWX risk markers."""

    model_config = ConfigDict(frozen=True)

    strike: float
    option_type: str
    speed_bs: float
    swx: float
    net_swx: float
    speed_zscore: float
    open_interest: float
    gamma_bs: float
    sigma: float


class InstabilityZone(BaseModel):
    """Aggregate strike cluster showing structural acceleration or deceleration speed."""

    model_config = ConfigDict(frozen=True)

    strike: float
    total_net_swx: float
    abs_total_net_swx: float
    regime: str


class SpeedProfilePoint(BaseModel):
    """Net SWX and GEX value at a hypothetical underlying spot price coordinate."""

    model_config = ConfigDict(frozen=True)

    spot: float
    net_swx: float
    net_gex: float


class SpeedByStrike(BaseModel):
    """Grouped call and put option speed components at a given strike price."""

    model_config = ConfigDict(frozen=True)

    strike: float
    call_speed: float
    put_speed: float


class SpeedDecaySeries(BaseModel):
    """Speed sensitivity behavior across different time horizons to expiration."""

    model_config = ConfigDict(frozen=True)

    label: str
    strike: float
    days_to_expiry: list[float]
    abs_speed: list[float]


class SpeedScatterPoint(BaseModel):
    """Individual option position risk details for cross-metric scatter plotting."""

    model_config = ConfigDict(frozen=True)

    strike: float
    option_type: str
    gex: float
    net_swx: float
    speed_bs: float
    marker_norm: float


class SpeedInstabilitySummary(BaseModel):
    """Portfolio-wide speed instability summary report."""

    model_config = ConfigDict(frozen=True)

    total_net_swx: float
    max_abs_swx_single_strike: float
    n_gamma_traps: int
    top_gamma_trap_strike: float | None
    book_bias: str


class SpeedInstabilityReport(BaseModel):
    """Comprehensive speed instability and structural risk analysis report."""

    model_config = ConfigDict(frozen=True)

    spot: float
    summary: SpeedInstabilitySummary
    zones: list[InstabilityZone]
    profile: list[SpeedProfilePoint]
    speed_by_strike: list[SpeedByStrike]
    speed_decay: list[SpeedDecaySeries]
    scatter: list[SpeedScatterPoint]
    gamma_traps: list[GammaTrap]


# ── Speed Instability Analysis Function ─────────────────────────────────────────


def analyze_speed_instability(
    chain_data: FloatArray,
    spot: float,
    r: float,
    max_legs: int = 180,
    profile_points: int = 220,
    z_trap: float = 1.6,
    max_decay_curves: int = 3,
) -> Result[SpeedInstabilityReport]:
    """Calculates speed instability profile, traps, decay dynamics and scatter report.

    Parameters
    ----------
    chain_data : 2D NumPy array of shape (N, 5) where columns represent:
                 [strike, is_call (1.0 or 0.0), iv (sigma), time_to_expiry, open_interest]
    spot       : Current spot price of the underlying asset
    r          : Risk-free rate (interest rate)
    max_legs   : Maximum option contract lines to compute
    profile_points : Number of coordinates to scan for the spot price profile
    z_trap     : Standard deviation threshold for classification of Speed Traps
    max_decay_curves : Maximum decay curve coordinates to output

    Returns
    -------
    Result[SpeedInstabilityReport]
    """
    if chain_data is None:
        return Result.failure(reason="chain_data must not be None")
    if chain_data.ndim != 2 or chain_data.shape[1] < 5:
        return Result.failure(
            reason=(
                f"chain_data must be a 2D array with at least 5 columns. "
                f"Got shape {chain_data.shape if chain_data is not None else 'None'}"
            )
        )
    if spot <= 0.0:
        return Result.failure(reason=f"spot price must be greater than zero. Got {spot}")
    if r < 0.0:
        return Result.failure(reason=f"interest rate must be non-negative. Got {r}")

    try:
        n = chain_data.shape[0]
        if n == 0:
            return Result.failure(reason="empty_portfolio")

        # Round open interest and clip at 0
        open_interest = np.maximum(np.round(chain_data[:, 4]), 0.0)
        if np.sum(open_interest) <= 0.0:
            return Result.failure(reason="zero_oi")

        # Truncate options chain by open_interest descending if count > max_legs
        if n > max_legs:
            sort_idx = np.argsort(-open_interest)
            chain_data = chain_data[sort_idx[:max_legs]]
            open_interest = open_interest[sort_idx[:max_legs]]
            n = max_legs

        strikes = chain_data[:, 0]
        is_call = chain_data[:, 1]
        sigma = chain_data[:, 2]
        tte = chain_data[:, 3]

        oi_sign = np.where(is_call == 1.0, 1.0, -1.0)

        # Baseline calculations at current spot
        spot_arr = np.full(n, spot, dtype=np.float64)
        gamma_bs = bs_gamma(spot_arr, strikes, r, sigma, tte)
        speed_bs = bs_speed(spot_arr, strikes, r, sigma, tte)

        swx = speed_bs * open_interest * MULTIPLIER * spot
        net_swx = swx * oi_sign
        abs_speed = np.abs(speed_bs)
        abs_swx = np.abs(swx)

        mu = float(np.mean(abs_speed))
        sig = float(np.std(abs_speed))
        speed_zscore = (abs_speed - mu) / (sig + 1e-12)

        # 1. Extract Gamma Traps
        mask = speed_zscore >= z_trap
        indices = np.where(mask)[0]
        if len(indices) > 0:
            sort_order = np.argsort(-abs_swx[indices])
            sorted_indices = indices[sort_order]
        else:
            sorted_indices = np.array([], dtype=int)

        gamma_traps_list = []
        for idx in sorted_indices:
            gamma_traps_list.append(
                GammaTrap(
                    strike=float(strikes[idx]),
                    option_type="call" if is_call[idx] == 1.0 else "put",
                    speed_bs=float(speed_bs[idx]),
                    swx=float(swx[idx]),
                    net_swx=float(net_swx[idx]),
                    speed_zscore=float(speed_zscore[idx]),
                    open_interest=float(open_interest[idx]),
                    gamma_bs=float(gamma_bs[idx]),
                    sigma=float(sigma[idx]),
                )
            )
        gamma_traps_output = gamma_traps_list[:12]

        # 2. Vectorized Instability Zones Aggregation (groupby strike equivalent)
        unique_strikes, inverse_indices = np.unique(strikes, return_inverse=True)
        total_net_swx = np.bincount(inverse_indices, weights=net_swx)
        abs_total_net_swx = np.abs(total_net_swx)

        top_idx = np.argsort(-abs_total_net_swx)[:3]
        zones = []
        for idx in top_idx:
            strike_val = float(unique_strikes[idx])
            net_val = float(total_net_swx[idx])
            regime = "ACCELERATION (rally cliff)" if net_val > 0 else "DECELERATION (sell vacuum)"
            zones.append(
                InstabilityZone(
                    strike=strike_val,
                    total_net_swx=net_val,
                    abs_total_net_swx=abs(net_val),
                    regime=regime,
                )
            )

        # 3. Summary metrics
        total_swx = float(np.sum(net_swx))
        max_abs = float(np.max(abs_swx))
        top_trap = float(gamma_traps_output[0].strike) if len(gamma_traps_output) > 0 else None

        rep_summary = SpeedInstabilitySummary(
            total_net_swx=total_swx,
            max_abs_swx_single_strike=max_abs,
            n_gamma_traps=len(gamma_traps_list),
            top_gamma_trap_strike=top_trap,
            book_bias=(
                "LONG SPEED (buy-climax prone)"
                if total_swx > 0
                else "SHORT SPEED (sell-vacuum prone)"
            ),
        )

        # 4. Vectorized net SWX / GEX spot profile grid computation
        s_low = spot * 0.85
        s_high = spot * 1.15
        spot_grid = np.linspace(s_low, s_high, profile_points)

        grid_s = spot_grid[:, np.newaxis]
        grid_k = strikes[np.newaxis, :]
        grid_sigma = sigma[np.newaxis, :]
        grid_t = tte[np.newaxis, :]

        speed_m = bs_speed(grid_s, grid_k, r, grid_sigma, grid_t)
        gamma_m = bs_gamma(grid_s, grid_k, r, grid_sigma, grid_t)

        swx_m = speed_m * open_interest * MULTIPLIER * grid_s * oi_sign
        gex_m = gamma_m * open_interest * MULTIPLIER * grid_s * oi_sign

        sum_swx = swx_m.sum(axis=1)
        sum_gex = gex_m.sum(axis=1)

        profile = []
        for i in range(len(spot_grid)):
            profile.append(
                SpeedProfilePoint(
                    spot=float(spot_grid[i]),
                    net_swx=float(sum_swx[i]),
                    net_gex=float(sum_gex[i]),
                )
            )

        if len(profile) > 240:
            step = max(1, len(profile) // 240)
            profile = profile[::step]

        # 5. Speed by Strike coordinates
        speed_by_strike = []
        for k in unique_strikes:
            call_mask = (strikes == k) & (is_call == 1.0)
            put_mask = (strikes == k) & (is_call == 0.0)

            call_speed = float(speed_bs[call_mask][0]) if np.any(call_mask) else 0.0
            put_speed = float(speed_bs[put_mask][0]) if np.any(put_mask) else 0.0

            speed_by_strike.append(
                SpeedByStrike(
                    strike=float(k),
                    call_speed=call_speed,
                    put_speed=put_speed,
                )
            )

        # 6. Speed Decay Series
        if len(gamma_traps_list) > 0:
            top_strikes = [trap.strike for trap in gamma_traps_list[:max_decay_curves]]
        else:
            top_idx_speed = np.argsort(-abs_speed)[:max_decay_curves]
            top_strikes = strikes[top_idx_speed].tolist()

        decay_series = []
        for k in top_strikes[:max_decay_curves]:
            diffs = np.abs(strikes - k)
            closest_idx = np.argmin(diffs)
            closest_sigma = float(sigma[closest_idx])

            t_arr = np.linspace(1.0, 0.001, 100)
            k_arr = np.full_like(t_arr, k, dtype=float)
            s_arr = np.full_like(t_arr, spot, dtype=float)
            sig_arr = np.full_like(t_arr, closest_sigma, dtype=float)

            decay_speed = bs_speed(s_arr, k_arr, r, sig_arr, t_arr)

            decay_series.append(
                SpeedDecaySeries(
                    label=f"|Speed| K={float(k):.0f}",
                    strike=float(k),
                    days_to_expiry=(t_arr * 252.0).tolist(),
                    abs_speed=np.abs(decay_speed).tolist(),
                )
            )

        # 7. Scatter points
        swx_abs = np.abs(swx)
        lo_swx = float(np.min(swx_abs))
        hi_swx = float(np.max(swx_abs))

        scatter = []
        for i in range(n):
            gex_val = float(gamma_bs[i] * open_interest[i] * MULTIPLIER * spot)
            mn_val = (abs_swx[i] - lo_swx) / (hi_swx - lo_swx + 1e-12)
            scatter.append(
                SpeedScatterPoint(
                    strike=float(strikes[i]),
                    option_type="call" if is_call[i] == 1.0 else "put",
                    gex=gex_val,
                    net_swx=float(net_swx[i]),
                    speed_bs=float(speed_bs[i]),
                    marker_norm=float(np.clip(mn_val, 0.0, 1.0)),
                )
            )

        return Result.success(
            SpeedInstabilityReport(
                spot=float(spot),
                summary=rep_summary,
                zones=zones,
                profile=profile,
                speed_by_strike=speed_by_strike,
                speed_decay=decay_series,
                scatter=scatter,
                gamma_traps=gamma_traps_output,
            )
        )

    except Exception as e:
        logger.error(f"Speed instability analysis failed: {e}")
        return Result.failure(reason=f"Speed instability analysis failed: {e}")
