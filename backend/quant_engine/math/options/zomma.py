from __future__ import annotations
"""
backend/engine/metrics/zomma.py
Sector: Options / Zomma Engine
[ARCH-1, PD-4]

Theoretical basis:
    Zomma (d_gamma/d_sigma) portfolio surface and vol-crush diagnostics.
    Vectorized over (spot, IV) grid for a single chain snapshot.
    Purely stateless, synchronous, offline, and pandas-free.
"""


import logging
import warnings

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.stats import norm


from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.zomma")

type FloatArray = npt.NDArray[np.float64]

warnings.filterwarnings("ignore")


# ── Greeks Helper Functions ─────────────────────────────────────────────────────


def _safe_d1_d2(
    spot_val: FloatArray,
    strike_val: FloatArray,
    tte: float,
    rate: float,
    sigma: FloatArray,
) -> tuple[FloatArray, FloatArray]:
    """Broadcast-safe d1, d2. Invalid regions -> nan (caller masks)."""
    sqrt_t = np.sqrt(max(tte, 1e-12))
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(spot_val / strike_val) + (rate + 0.5 * sigma**2) * tte) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
    return d1, d2


def bs_gamma_zomma_vec(
    spot_val: FloatArray,
    strike_val: FloatArray,
    tte: float,
    rate: float,
    sigma: FloatArray,
) -> tuple[FloatArray, FloatArray]:
    """
    Per-unit gamma and zomma, same shape as np.broadcast_arrays(spot_val, strike_val, sigma).
    Zomma = d_gamma / d_sigma = Gamma * (d1 * d2 - 1.0) / sigma
    """
    sb_val, kb_val, sigb_val = np.broadcast_arrays(spot_val, strike_val, sigma)
    mask = (sb_val > 0) & (kb_val > 0) & (sigb_val > 1e-8) & (tte > 1e-12)
    gamma = np.zeros_like(sb_val, dtype=np.float64)
    zomma = np.zeros_like(sb_val, dtype=np.float64)
    if not np.any(mask):
        return gamma, zomma
    d1, d2 = _safe_d1_d2(sb_val, kb_val, tte, rate, sigb_val)
    pdf = norm.pdf(d1)
    sqrt_t = np.sqrt(max(tte, 1e-12))
    g = np.zeros_like(sb_val)
    with np.errstate(divide="ignore", invalid="ignore"):
        g[mask] = pdf[mask] / (sb_val[mask] * sigb_val[mask] * sqrt_t)
    z = np.zeros_like(sb_val)
    with np.errstate(divide="ignore", invalid="ignore"):
        z[mask] = g[mask] * (d1[mask] * d2[mask] - 1.0) / sigb_val[mask]
    g[~mask] = 0.0
    z[~mask] = 0.0
    g = np.nan_to_num(g, nan=0.0, posinf=0.0, neginf=0.0)
    z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
    return g, z


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class TopZommaStrike(BaseModel):
    """Represents a top strike with high absolute notional Zomma."""

    model_config = ConfigDict(frozen=True)

    strike: float
    option_type: str
    notional_zomma: float


class GammaCrushImpact(BaseModel):
    """Gamma exposure before and after vol crush for a group of options."""

    model_config = ConfigDict(frozen=True)

    gamma_before: float
    gamma_after: float


class GammaVolCrushReport(BaseModel):
    """Gamma shock impact report under volatility crush scenarios."""

    model_config = ConfigDict(frozen=True)

    atm_zomma_neg: GammaCrushImpact
    otm_zomma_pos: GammaCrushImpact


class ZommaReport(BaseModel):
    """Comprehensive portfolio zomma analysis and vol-crush metrics."""

    model_config = ConfigDict(frozen=True)

    current_iv: float
    post_crush_iv: float
    vol_crush_pct: float
    spot_axis: list[float]
    iv_axis: list[float]
    heatmap_z: list[list[float]]
    gamma_vol_crush: GammaVolCrushReport
    top_strikes: list[TopZommaStrike]


# ── Zomma Engine ────────────────────────────────────────────────────────────────


class ZommaEngine:
    """Stateless computation engine for portfolio Zomma and vol-crush analysis."""

    def __init__(self, contract_size: int = 100) -> None:
        self.contract_size = contract_size

    def analyze_zomma(
        self,
        chain_data: FloatArray,
        spot: float,
        tte: float,
        rate: float,
        vol_crush_pct: float = 0.20,
        spot_range_pct: float = 0.18,
        n_spot: int = 48,
        n_iv: int = 36,
        max_legs: int = 160,
    ) -> Result[ZommaReport]:
        """Calculates Zomma profile over a Spot/IV grid, stress testing and top strikes.

        Parameters
        ----------
        chain_data : 2D NumPy array of shape (N, 4) where columns represent:
                     [strike, iv (decimal), quantity, is_call (1.0 or 0.0)]
        spot       : Spot price of the underlying
        tte        : Time to expiration in years
        rate       : Risk-free rate
        vol_crush_pct : Shock fraction for implied volatility crush
        spot_range_pct : Scanned spot price range fraction
        n_spot     : Grid points for the spot axis
        n_iv       : Grid points for the IV axis
        max_legs   : Maximum number of options legs to analyze

        Returns
        -------
        Result[ZommaReport]
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
        if spot <= 0.0:
            return Result.failure(reason=f"spot price must be greater than zero. Got {spot}")
        if tte <= 0.0:
            return Result.failure(reason=f"time to expiry must be greater than zero. Got {tte}")

        try:
            n = chain_data.shape[0]
            if n == 0:
                return Result.failure(reason="empty_portfolio")

            # Check if we need to truncate the legs list by quantity (column index 2)
            if n > max_legs:
                sort_idx = np.argsort(-chain_data[:, 2])
                chain_data = chain_data[sort_idx[:max_legs]]
                n = max_legs

            strikes = chain_data[:, 0]
            iv0 = chain_data[:, 1]
            raw_quantity = np.clip(chain_data[:, 2], 0.0, None)
            qty = raw_quantity * float(self.contract_size)
            is_call = chain_data[:, 3]

            if np.sum(raw_quantity) <= 0.0:
                return Result.failure(reason="zero_quantity")

            w_atm = float(np.average(iv0, weights=np.maximum(qty, 1e-9)))
            w_atm = float(np.clip(w_atm, 0.06, 1.2))

            iv_lo = max(0.04, w_atm * 0.35)
            iv_hi = min(0.95, max(w_atm * 1.9, w_atm + 0.35))
            iv_axis = np.linspace(iv_lo, iv_hi, n_iv, dtype=np.float64)

            s_lo = spot * (1.0 - spot_range_pct)
            s_hi = spot * (1.0 + spot_range_pct)
            spot_axis = np.linspace(s_lo, s_hi, n_spot, dtype=np.float64)

            # (n_iv, n_spot, n_leg) grid construction
            s_grid = spot_axis[np.newaxis, :, np.newaxis]
            sig_grid = iv_axis[:, np.newaxis, np.newaxis]
            k_grid = strikes[np.newaxis, np.newaxis, :]

            g_grid, z_grid = bs_gamma_zomma_vec(s_grid, k_grid, tte, rate, sig_grid)
            w = qty[np.newaxis, np.newaxis, :]
            notional_z = (z_grid * w).sum(axis=2)

            # Baseline calculations
            s0_val = np.full_like(iv0, spot)
            g0, z0 = bs_gamma_zomma_vec(s0_val, strikes, tte, rate, iv0)
            nz0 = z0 * qty

            crush_iv = iv0 * (1.0 - vol_crush_pct)
            g1, _ = bs_gamma_zomma_vec(s0_val, strikes, tte, rate, crush_iv)
            ng0 = g0 * qty
            ng1 = g1 * qty

            atm_mask = z0 < 0
            otm_mask = z0 > 0

            def _sum(mask: npt.NDArray[np.bool_], arr: FloatArray) -> float:
                return float(arr[mask].sum()) if np.any(mask) else 0.0

            gamma_crush = GammaVolCrushReport(
                atm_zomma_neg=GammaCrushImpact(
                    gamma_before=_sum(atm_mask, ng0),
                    gamma_after=_sum(atm_mask, ng1),
                ),
                otm_zomma_pos=GammaCrushImpact(
                    gamma_before=_sum(otm_mask, ng0),
                    gamma_after=_sum(otm_mask, ng1),
                ),
            )

            post_crush_iv = float(w_atm * (1.0 - vol_crush_pct))
            labels = np.where(is_call == 1.0, "call", "put")
            top_idx = np.argsort(-np.abs(nz0))[:10]
            top_strikes: list[TopZommaStrike] = []
            for i in top_idx:
                top_strikes.append(
                    TopZommaStrike(
                        strike=float(strikes[i]),
                        option_type=str(labels[i]),
                        notional_zomma=float(nz0[i]),
                    )
                )

            z_mat = notional_z.tolist()

            return Result.success(
                ZommaReport(
                    current_iv=float(w_atm),
                    post_crush_iv=post_crush_iv,
                    vol_crush_pct=float(vol_crush_pct),
                    spot_axis=spot_axis.tolist(),
                    iv_axis=iv_axis.tolist(),
                    heatmap_z=z_mat,
                    gamma_vol_crush=gamma_crush,
                    top_strikes=top_strikes,
                )
            )

        except Exception as e:
            logger.error(f"Zomma analysis failed: {e}")
            return Result.failure(reason=f"Zomma analysis failed: {e}")
