from __future__ import annotations
from typing import Any
"""
Zomma (∂Γ/∂σ) portfolio surface and vol-crush diagnostics.
Vectorized over (spot, IV) grid for a single chain snapshot.
"""



import numpy as np
import pandas as pd

from scipy.stats import norm



def _safe_d1_d2(
    S: np.ndarray[Any, np.dtype[Any]],
    K: np.ndarray[Any, np.dtype[Any]],
    T: float,
    r: float,
    sigma: np.ndarray[Any, np.dtype[Any]],
) -> tuple[np.ndarray[Any, np.dtype[Any]], np.ndarray[Any, np.dtype[Any]]]:
    """Broadcast-safe d1, d2. Invalid regions -> nan (caller masks)."""
    sqrt_t = np.sqrt(max(T, 1e-12))
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
    return d1, d2


def bs_gamma_zomma_vec(
    S: np.ndarray[Any, np.dtype[Any]],
    K: np.ndarray[Any, np.dtype[Any]],
    T: float,
    r: float,
    sigma: np.ndarray[Any, np.dtype[Any]],
) -> tuple[np.ndarray[Any, np.dtype[Any]], np.ndarray[Any, np.dtype[Any]]]:
    """
    Per-unit gamma and zomma, same shape as np.broadcast_arrays(S, K, sigma).
    Zomma = ∂Γ/∂σ = Γ * (d1*d2 - 1) / σ
    """
    Sb, Kb, sigb = np.broadcast_arrays(S, K, sigma)
    mask = (Sb > 0) & (Kb > 0) & (sigb > 1e-8) & (T > 1e-12)
    gamma = np.zeros_like(Sb, dtype=np.float64)
    zomma = np.zeros_like(Sb, dtype=np.float64)
    if not np.any(mask):
        return gamma, zomma
    d1, d2 = _safe_d1_d2(Sb, Kb, T, r, sigb)
    pdf = norm.pdf(d1)
    sqrt_t = np.sqrt(T)
    g = np.zeros_like(Sb)
    with np.errstate(divide="ignore", invalid="ignore"):
        g[mask] = pdf[mask] / (Sb[mask] * sigb[mask] * sqrt_t)
    z = np.zeros_like(Sb)
    with np.errstate(divide="ignore", invalid="ignore"):
        z[mask] = g[mask] * (d1[mask] * d2[mask] - 1.0) / sigb[mask]
    g[~mask] = 0.0
    z[~mask] = 0.0
    g = np.nan_to_num(g, nan=0.0, posinf=0.0, neginf=0.0)
    z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
    return g, z


def compute_zomma_bundle(
    portfolio_df: pd.DataFrame,
    spot: float,
    contract_size: int = 100,
    vol_crush_pct: float = 0.20,
    spot_range_pct: float = 0.18,
    n_spot: int = 48,
    n_iv: int = 36,
    max_legs: int = 160,
) -> dict[str, Any]:
    """
    portfolio_df columns: strike, option_type, iv, quantity, expiry, r
    IV in decimal. quantity = OI weight per leg.
    """
    if portfolio_df is None or portfolio_df.empty or spot <= 0:
        return {"ok": False, "error": "empty_portfolio"}

    df = portfolio_df.copy()
    df["quantity"] = df["quantity"].astype(float).clip(lower=0.0)
    if df["quantity"].sum() <= 0:
        return {"ok": False, "error": "zero_quantity"}

    if len(df) > max_legs:
        df = df.nlargest(max_legs, "quantity").reset_index(drop=True)

    strikes = df["strike"].to_numpy(dtype=np.float64)
    qty = df["quantity"].to_numpy(dtype=np.float64) * float(contract_size)
    iv0 = df["iv"].to_numpy(dtype=np.float64)
    T = float(df["expiry"].iloc[0])
    r = float(df["r"].iloc[0])

    w_atm = float(np.average(iv0, weights=np.maximum(qty, 1e-9)))
    w_atm = float(np.clip(w_atm, 0.06, 1.2))

    iv_lo = max(0.04, w_atm * 0.35)
    iv_hi = min(0.95, max(w_atm * 1.9, w_atm + 0.35))
    iv_axis = np.linspace(iv_lo, iv_hi, n_iv, dtype=np.float64)

    s_lo = spot * (1.0 - spot_range_pct)
    s_hi = spot * (1.0 + spot_range_pct)
    spot_axis = np.linspace(s_lo, s_hi, n_spot, dtype=np.float64)

    # (n_iv, n_spot, n_leg)
    S = spot_axis[np.newaxis, :, np.newaxis]
    Sig = iv_axis[:, np.newaxis, np.newaxis]
    K = strikes[np.newaxis, np.newaxis, :]

    g_grid, z_grid = bs_gamma_zomma_vec(S, K, T, r, Sig)
    w = qty[np.newaxis, np.newaxis, :]
    notional_z = (z_grid * w).sum(axis=2)

    # Baseline per-leg at (spot, own iv) for buckets + top strikes
    S0 = np.full_like(iv0, spot)
    g0, z0 = bs_gamma_zomma_vec(S0, strikes, T, r, iv0)
    nz0 = z0 * qty

    crush_iv = iv0 * (1.0 - vol_crush_pct)
    g1, _z1 = bs_gamma_zomma_vec(S0, strikes, T, r, crush_iv)
    ng0 = g0 * qty
    ng1 = g1 * qty

    atm_mask = z0 < 0
    otm_mask = z0 > 0

    def _sum(mask: np.ndarray[Any, np.dtype[Any]], arr: np.ndarray[Any, np.dtype[Any]]) -> float:
        return float(arr[mask].sum()) if np.any(mask) else 0.0

    gamma_crush = {
        "atm_zomma_neg": {
            "gamma_before": _sum(atm_mask, ng0),
            "gamma_after": _sum(atm_mask, ng1),
        },
        "otm_zomma_pos": {
            "gamma_before": _sum(otm_mask, ng0),
            "gamma_after": _sum(otm_mask, ng1),
        },
    }

    post_crush_iv = float(w_atm * (1.0 - vol_crush_pct))

    ot = df["option_type"].astype(str).str.upper()
    labels = np.where(ot.str.startswith("C"), "call", "put")
    top_idx = np.argsort(-np.abs(nz0))[:10]
    top_strikes: list[dict[str, Any]] = []
    for i in top_idx:
        top_strikes.append(
            {
                "strike": float(strikes[i]),
                "option_type": str(labels[i]),
                "notional_zomma": float(nz0[i]),
            }
        )

    z_mat = notional_z.tolist()
    return {
        "ok": True,
        "current_iv": float(w_atm),
        "post_crush_iv": post_crush_iv,
        "vol_crush_pct": float(vol_crush_pct),
        "spot_axis": spot_axis.tolist(),
        "iv_axis": iv_axis.tolist(),
        "heatmap_z": z_mat,
        "gamma_vol_crush": gamma_crush,
        "top_strikes": top_strikes,
    }
