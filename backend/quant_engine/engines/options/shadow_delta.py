from __future__ import annotations
"""
backend/engine/metrics/shadow_delta.py
Sector: Options / Shadow Delta Engine
[ARCH-1, PD-4]

Theoretical basis:
    Dealer Gamma and shadow delta analysis based on skew expansion:
    shadow_delta = bs_delta + Vanna * skew_slope
    Purely stateless, synchronous, offline, and pandas/plotly-free.
"""


import logging
import warnings

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.special import ndtr


from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.shadow_delta")

type FloatArray = npt.NDArray[np.float64]

warnings.filterwarnings("ignore")


# ── Black-Scholes Greeks Helper Functions ───────────────────────────────────────


def _d1_vectorized(
    spot: float,
    strike: FloatArray,
    tte: FloatArray,
    rate: FloatArray,
    sigma: FloatArray,
) -> FloatArray:
    """Calculates Black-Scholes d1 vectorially."""
    eps = 1e-8
    t_val = np.maximum(tte, eps)
    sig_val = np.maximum(sigma, eps)
    return (np.log(spot / strike) + (rate + 0.5 * sig_val**2) * t_val) / (sig_val * np.sqrt(t_val))


def bs_delta_vectorized(
    spot: float,
    strike: FloatArray,
    tte: FloatArray,
    rate: FloatArray,
    sigma: FloatArray,
    is_call: FloatArray,
) -> FloatArray:
    """Calculates Black-Scholes Delta vectorially."""
    d1 = _d1_vectorized(spot, strike, tte, rate, sigma)
    cdf_d1 = ndtr(d1)
    return np.where(is_call == 1.0, cdf_d1, cdf_d1 - 1.0)


def bs_vanna_vectorized(
    spot: float,
    strike: FloatArray,
    tte: FloatArray,
    rate: FloatArray,
    sigma: FloatArray,
) -> FloatArray:
    """Calculates Black-Scholes Vanna vectorially."""
    eps = 1e-8
    sig_val = np.maximum(sigma, eps)
    t_val = np.maximum(tte, eps)
    d1 = _d1_vectorized(spot, strike, tte, rate, sigma)
    d2 = d1 - sig_val * np.sqrt(t_val)
    pdf_d1 = np.exp(-0.5 * d1**2) / np.sqrt(2.0 * np.pi)
    return -pdf_d1 * d2 / sig_val


def bs_vega_vectorized(
    spot: float,
    strike: FloatArray,
    tte: FloatArray,
    rate: FloatArray,
    sigma: FloatArray,
) -> FloatArray:
    """Calculates Black-Scholes Vega vectorially."""
    eps = 1e-8
    t_val = np.maximum(tte, eps)
    d1 = _d1_vectorized(spot, strike, tte, rate, sigma)
    pdf_d1 = np.exp(-0.5 * d1**2) / np.sqrt(2.0 * np.pi)
    return spot * pdf_d1 * np.sqrt(t_val)


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class PositionMultiplierResult(BaseModel):
    """Result of shadow delta position sizing multiplier sizing engine."""

    model_config = ConfigDict(frozen=True)

    multiplier: float
    edge_signal: float
    reason: str
    delta_divergence: float


class NetPortfolioDelta(BaseModel):
    """Aggregated portfolio delta metrics."""

    model_config = ConfigDict(frozen=True)

    net_bs_delta: float
    net_shadow_delta: float
    total_delta_gap: float
    hedge_shares_needed: float
    spot_price: float
    n_options: int


class ShadowDeltaNode(BaseModel):
    """Metrics and stress test outcomes for an individual option node."""

    model_config = ConfigDict(frozen=True)

    strike: float
    option_type: str
    iv: float
    bs_delta: float
    shadow_delta: float
    delta_gap: float
    delta_gap_pct: float
    vanna: float
    skew_slope: float
    quantity: float
    hedge_adj_shares: float

    # Post-shock metrics
    post_shock_bs_delta: float
    post_shock_shadow_delta: float
    post_shock_iv: float
    post_shock_delta_error: float

    # Position sizing multiplier
    multiplier_result: PositionMultiplierResult


class ShadowDeltaReport(BaseModel):
    """Fitted shadow delta and risk analysis portfolio report."""

    model_config = ConfigDict(frozen=True)

    spot_price: float
    net_portfolio: NetPortfolioDelta
    nodes: list[ShadowDeltaNode]


# ── Position Sizing Multiplier Function ──────────────────────────────────────────


def shadow_delta_position_multiplier(
    shadow_delta: float,
    bs_delta: float,
    vanna: float,
    option_type: str,
    skew_slope: float,
    base_multiplier: float = 1.0,
    edge_threshold: float = 0.10,
    max_leverage: float = 1.40,
) -> PositionMultiplierResult:
    """Calculates position sizing amplification based on delta divergence from skew."""
    delta_divergence = abs(shadow_delta - bs_delta) / max(abs(bs_delta), 0.01)

    if delta_divergence > edge_threshold:
        edge_strength = min(1.0, delta_divergence / 0.50)

        if (option_type == "CALL" and shadow_delta > bs_delta) or (
            option_type == "PUT" and shadow_delta < bs_delta
        ):
            multiplier = 1.0 + (edge_strength * 0.40)
        else:
            multiplier = 1.0
    else:
        multiplier = 1.0
        edge_strength = 0.0

    if edge_strength > 0.85 and vanna > 0.05:
        multiplier = max_leverage

    multiplier = float(max(1.0, min(max_leverage, multiplier * base_multiplier)))

    reason = "Normal delta alignment"
    if multiplier > 1.0:
        reason = f"OTM {option_type.lower()}s amplified by positive skew slope"

    return PositionMultiplierResult(
        multiplier=float(multiplier),
        edge_signal=float(edge_strength),
        reason=reason,
        delta_divergence=float(delta_divergence),
    )


# ── Shadow Delta Engine ──────────────────────────────────────────────────────────


class ShadowDeltaEngine:
    """Stateless computation engine for shadow delta portfolio exposure analysis."""

    def __init__(self, contract_size: int = 100) -> None:
        self.contract_size = contract_size

    def analyze_shadow_delta(
        self,
        chain_data: FloatArray,
        spot_price: float,
        tte: float,
        rate: float,
        skew_window: int = 2,
        regularize_skew: bool = True,
        skew_cap: float = 0.05,
        shock_pct: float = -0.05,
    ) -> Result[ShadowDeltaReport]:
        """Performs shadow delta calculations, stress testing, and position sizing.

        Parameters
        ----------
        chain_data : 2D NumPy array of shape (N, 4) where columns represent:
                     [strike, is_call (1.0 or 0.0), iv (decimal), quantity]
        spot_price : Spot price of the underlying
        tte        : Time to expiry in years
        rate       : Risk-free rate
        skew_window : Neighbor count for calculating IV skew slope
        regularize_skew : If True, clips skew slope values to prevent explosion
        skew_cap   : Maximum absolute value for skew slope
        shock_pct  : Underlying price stress shock fraction

        Returns
        -------
        Result[ShadowDeltaReport]
        """
        if chain_data is None:
            return Result.failure(reason="chain_data must not be None")
        if chain_data.ndim != 2 or chain_data.shape[1] < 4:
            return Result.failure(
                reason=(
                    f"chain_data must be a 2D array with at least 4 columns. "
                    f"Got shape {chain_data.shape if chain_data is not None else 'None'}"
                )
            )
        if spot_price <= 0.0:
            return Result.failure(reason=f"spot price must be greater than zero. Got {spot_price}")
        if tte <= 0.0:
            return Result.failure(reason=f"time to expiry must be greater than zero. Got {tte}")

        try:
            n = chain_data.shape[0]
            if n == 0:
                return Result.failure(reason="chain_data options portfolio is empty")

            # Sort by strike to align neighbor slope calculations
            sort_idx = np.argsort(chain_data[:, 0])
            sorted_chain = chain_data[sort_idx]

            strikes = sorted_chain[:, 0]
            is_calls = sorted_chain[:, 1]
            ivs = sorted_chain[:, 2]
            quantities = sorted_chain[:, 3]

            tte_arr = np.full(n, tte, dtype=np.float64)
            rate_arr = np.full(n, rate, dtype=np.float64)

            # 1. Recalculate baseline Delta, Vanna, Vega
            bs_deltas = bs_delta_vectorized(spot_price, strikes, tte_arr, rate_arr, ivs, is_calls)
            vannas = bs_vanna_vectorized(spot_price, strikes, tte_arr, rate_arr, ivs)

            # 2. Compute local IV skew slope vectorially
            lo = np.clip(np.arange(n) - skew_window, 0, n - 1)
            hi = np.clip(np.arange(n) + skew_window, 0, n - 1)
            div = ivs[hi] - ivs[lo]
            dk = strikes[hi] - strikes[lo]
            skew_slopes = np.where(dk != 0.0, div / dk, 0.0)

            if regularize_skew:
                skew_slopes = np.clip(skew_slopes, -skew_cap, skew_cap)

            # 3. Calculate shadow delta metrics
            shadow_deltas = bs_deltas + vannas * skew_slopes
            delta_gaps = shadow_deltas - bs_deltas
            delta_gap_pcts = np.where(
                np.abs(bs_deltas) > 1e-6,
                (delta_gaps / np.abs(bs_deltas)) * 100.0,
                0.0,
            )

            # 4. Portfolio net delta metrics
            net_bs_delta = float(np.sum(bs_deltas * quantities * self.contract_size))
            net_shadow_delta = float(np.sum(shadow_deltas * quantities * self.contract_size))
            total_delta_gap = net_shadow_delta - net_bs_delta
            hedge_shares_needed = -total_delta_gap

            net_portfolio = NetPortfolioDelta(
                net_bs_delta=round(net_bs_delta, 4),
                net_shadow_delta=round(net_shadow_delta, 4),
                total_delta_gap=round(total_delta_gap, 4),
                hedge_shares_needed=round(hedge_shares_needed, 4),
                spot_price=float(spot_price),
                n_options=n,
            )

            # 5. Stress test price shock metrics vectorially
            shocked_spot = spot_price * (1.0 + shock_pct)
            delta_s = shocked_spot - spot_price
            ivs_adjusted = np.maximum(ivs + skew_slopes * delta_s, 0.001)

            post_shock_bs_deltas = bs_delta_vectorized(
                shocked_spot, strikes, tte_arr, rate_arr, ivs, is_calls
            )
            vannas_post = bs_vanna_vectorized(
                shocked_spot, strikes, tte_arr, rate_arr, ivs_adjusted
            )
            post_shadow_deltas = bs_delta_vectorized(
                shocked_spot,
                strikes,
                tte_arr,
                rate_arr,
                ivs_adjusted,
                is_calls,
            ) + (vannas_post * skew_slopes)

            post_shock_delta_errors = post_shock_bs_deltas - post_shadow_deltas

            # 6. Build report node structures
            nodes = []
            for i in range(n):
                opt_type = "CALL" if is_calls[i] == 1.0 else "PUT"
                hedge_adj = -delta_gaps[i] * quantities[i] * self.contract_size

                # Compute multiplier sizing
                multiplier_res = shadow_delta_position_multiplier(
                    shadow_delta=float(shadow_deltas[i]),
                    bs_delta=float(bs_deltas[i]),
                    vanna=float(vannas[i]),
                    option_type=opt_type,
                    skew_slope=float(skew_slopes[i]),
                )

                nodes.append(
                    ShadowDeltaNode(
                        strike=float(strikes[i]),
                        option_type=opt_type,
                        iv=float(ivs[i]),
                        bs_delta=float(bs_deltas[i]),
                        shadow_delta=float(shadow_deltas[i]),
                        delta_gap=float(delta_gaps[i]),
                        delta_gap_pct=float(delta_gap_pcts[i]),
                        vanna=float(vannas[i]),
                        skew_slope=float(skew_slopes[i]),
                        quantity=float(quantities[i]),
                        hedge_adj_shares=float(hedge_adj),
                        post_shock_bs_delta=float(post_shock_bs_deltas[i]),
                        post_shock_shadow_delta=float(post_shadow_deltas[i]),
                        post_shock_iv=float(ivs_adjusted[i]),
                        post_shock_delta_error=float(post_shock_delta_errors[i]),
                        multiplier_result=multiplier_res,
                    )
                )

            return Result.success(
                ShadowDeltaReport(
                    spot_price=float(spot_price),
                    net_portfolio=net_portfolio,
                    nodes=nodes,
                )
            )

        except Exception as e:
            logger.error(f"Shadow delta analysis failed: {e}")
            return Result.failure(reason=f"Shadow delta analysis failed: {e}")
