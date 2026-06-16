"""Módulo de probabilidad de Gamma Flip.

Calcula la probabilidad de que el precio spot toque el nivel de Zero Gamma (ZGL)
antes del vencimiento (DTE), asumiendo un Movimiento Browniano Geométrico (GBM).
"""

from __future__ import annotations

import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def estimate_gamma_flip_probability(
    spot: float, zgl: float, iv: float, dte_days: float, r: float = 0.04
) -> float:
    """
    Estima la probabilidad de que el precio del subyacente toque el Zero Gamma Level
    (ZGL) antes del vencimiento, usando la probabilidad de primera llegada
    (first passage time probability) para un GBM.
    """
    if spot <= 0 or zgl <= 0 or dte_days <= 0 or iv <= 0:
        return 0.0

    T = dte_days / 365.0
    # Evitar problemas numéricos si spot y zgl son muy cercanos
    if math.isclose(spot, zgl, rel_tol=1e-5):
        return 1.0

    a = math.log(zgl / spot)
    mu = r - 0.5 * iv**2
    sigma_sqrt_T = iv * math.sqrt(T)

    if sigma_sqrt_T < 1e-8:
        return 0.0

    if a > 0:
        # Barrera por encima (ZGL > Spot)
        d1 = (-a + mu * T) / sigma_sqrt_T
        d2 = (-a - mu * T) / sigma_sqrt_T
    else:
        # Barrera por debajo (ZGL < Spot)
        d1 = (a - mu * T) / sigma_sqrt_T
        d2 = (a + mu * T) / sigma_sqrt_T

    term1 = _norm_cdf(d1)

    exponent = (2.0 * mu * a) / (iv**2)
    # Prevenir overflow
    if exponent > 700:
        term2_factor = float("inf")
    elif exponent < -700:
        term2_factor = 0.0
    else:
        term2_factor = math.exp(exponent)

    term2 = term2_factor * _norm_cdf(d2)

    prob = float(term1 + term2)

    # Manejar posibles inestabilidades numéricas
    if math.isnan(prob) or math.isinf(prob):
        return 0.0

    return max(0.0, min(prob, 1.0))
