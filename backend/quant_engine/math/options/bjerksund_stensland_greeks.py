"""Finite-difference Greeks for Bjerksund-Stensland American pricer. # [TH]"""

from __future__ import annotations

from typing import Literal

from backend.quant_engine.math.options.bjerksund_stensland import BjerksundStenslandPricer

_D_S_REL = 0.01
_D_SIGMA = 0.01
_D_T_DAYS = 1.0 / 365.0
_D_R = 0.0001
_DAYS_YEAR = 365.0


def _price(
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    option_type: Literal["CALL", "PUT"],
) -> float:
    return BjerksundStenslandPricer.price(S, K, T, r, q, sigma, option_type)


def american_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    option_type: Literal["CALL", "PUT"],
) -> dict[str, float]:
    """Bump-and-revalue Greeks for American options."""
    if T <= 1e-9 or sigma <= 1e-9 or S <= 0:
        return {
            "delta": 0.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "rho": 0.0,
            "theoretical_price": max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0),
        }

    d_s = max(S * _D_S_REL, 1e-4)
    p0 = _price(S, K, T, r, q, sigma, option_type)
    p_up = _price(S + d_s, K, T, r, q, sigma, option_type)
    p_dn = _price(S - d_s, K, T, r, q, sigma, option_type)
    delta = (p_up - p_dn) / (2.0 * d_s)
    gamma = max((p_up - 2.0 * p0 + p_dn) / (d_s * d_s), 0.0)

    t_bump = max(T - _D_T_DAYS, _D_T_DAYS)
    p_t = _price(S, K, t_bump, r, q, sigma, option_type)
    theta = (p_t - p0) / _D_T_DAYS

    p_vol = _price(S, K, T, r, q, sigma + _D_SIGMA, option_type)
    vega = max((p_vol - p0) / _D_SIGMA / 100.0, 0.0)

    p_r_up = _price(S, K, T, r + _D_R, q, sigma, option_type)
    rho = (p_r_up - p0) / _D_R / 100.0

    return {
        "delta": (
            max(min(delta, 1.0), -1.0) if option_type == "CALL" else max(min(delta, 0.0), -1.0)
        ),
        "gamma": gamma,
        "theta": theta / _DAYS_YEAR,
        "vega": vega,
        "rho": rho,
        "theoretical_price": max(p0, 0.0),
    }


__all__ = ["american_greeks"]
