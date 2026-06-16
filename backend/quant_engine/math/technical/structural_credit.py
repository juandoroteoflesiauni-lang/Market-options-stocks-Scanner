"""
Merton Structural Credit Model & DTS Primitives.

Features:
  - Asset Value & Volatility inference from Equity.
  - Distance to Default (DD) & Probability of Default (PD).
  - Implied Credit Spreads.
  - Duration Times Spread (DTS) Exposure.

Source: Merton (1974), FactSet MAC-III Fixed Income methodology.
"""

from __future__ import annotations

import math

from scipy.optimize import fsolve  # type: ignore[import-untyped]
from scipy.stats import norm  # type: ignore[import-untyped]


def merton_asset_inference(
    equity_value: float,
    equity_vol: float,
    debt_face: float,
    r: float,
    t: float = 1.0,
) -> tuple[float, float]:
    """
    Infer Asset Value (V) and Asset Volatility (sigma_V) from Equity components.

    Uses numerical root-finding for the Merton (1974) equations:
      1) E = V*N(d1) - D*exp(-rt)*N(d2)
      2) sigma_E = (V/E) * N(d1) * sigma_V

    Returns: (V, sigma_V)
    """
    if debt_face <= 0:
        # No debt -> Asset = Equity
        return max(equity_value, 1e-12), max(equity_vol, 1e-12)

    if equity_value <= 0 or not math.isfinite(equity_value):
        # Non-positive equity: Merton surface ill-defined — return conservative stub.
        return max(float(debt_face) * 1.01, 1.0), max(float(equity_vol), 0.05)

    if equity_vol <= 0 or not math.isfinite(equity_vol):
        return float(equity_value + debt_face), 0.05

    def equations(p: tuple[float, float]) -> tuple[float, float]:
        v, sigma_v = p
        if v <= 0 or sigma_v <= 0:
            return 1e10, 1e10  # Penalty

        d1 = (math.log(v / debt_face) + (r + 0.5 * sigma_v**2) * t) / (sigma_v * math.sqrt(t))
        d2 = d1 - sigma_v * math.sqrt(t)

        # Black-Scholes Call (Equity Value)
        e_theor = v * norm.cdf(d1) - debt_face * math.exp(-r * t) * norm.cdf(d2)
        # Delta mapping (Equity Vol)
        denom = max(abs(equity_value), 1e-12)
        vol_theor = (v / denom) * norm.cdf(d1) * sigma_v

        return (e_theor - equity_value, vol_theor - equity_vol)

    # Initial guess: V = E + D, sigma_V = sigma_E * (E / (E+D))
    v_guess = max(equity_value + debt_face, debt_face * 1.001)
    sv_guess = max(equity_vol * (equity_value / max(v_guess, 1e-12)), 1e-6)

    try:
        sol = fsolve(equations, [v_guess, sv_guess], xtol=1e-6)
        v_out, sv_out = float(sol[0]), float(sol[1])
        if (
            not math.isfinite(v_out)
            or not math.isfinite(sv_out)
            or v_out <= debt_face
            or sv_out <= 0
        ):
            return v_guess, sv_guess
        return v_out, sv_out
    except Exception:
        # Fallback to initial guess if solver fails
        return v_guess, sv_guess


def distance_to_default(
    asset_value: float,
    asset_vol: float,
    debt_face: float,
    r: float,
    t: float = 1.0,
) -> float:
    """
    Compute Distance to Default (DD).

    DD = [ln(V/D) + (r - sigma_V^2/2)T] / [sigma_V * sqrt(T)]
    """
    if asset_vol <= 0 or asset_value <= 0 or debt_face <= 0:
        return 0.0

    num = math.log(asset_value / debt_face) + (r - 0.5 * asset_vol**2) * t
    den = asset_vol * math.sqrt(t)
    return num / den


def probability_of_default(dd: float) -> float:
    """
    Probability of Default (PD) = N(-DD).
    """
    return float(norm.cdf(-dd))


def implied_credit_spread(
    asset_value: float,
    equity_value: float,
    debt_face: float,
    r: float,
    t: float = 1.0,
) -> float:
    """
    Compute implied credit spread in decimal (e.g. 0.02 = 200 bps).

    S = - (1/T) * ln(F/D) - r
    where F = AssetValue - EquityValue (Market Value of Debt)
    """
    if t <= 0 or debt_face <= 0:
        return 0.0

    market_debt = max(0.001, asset_value - equity_value)
    spread = -(1.0 / t) * math.log(market_debt / debt_face) - r
    return max(0.0, spread)


def dts_exposure(oas_bps: float, d_oas: float) -> float:
    """
    Duration Times Spread (DTS).

    DTS = D_OAS * OAS
    """
    return oas_bps * d_oas


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: structural_credit.py
# Eliminado: referencia de ruta del sistema anterior en encabezado
# Preservado: fórmulas Merton (inferencia V/sigmaV, DD, PD, spread implícito) y DTS
# Pendientes: ninguno
# ─────────────────────────────────────────────────
