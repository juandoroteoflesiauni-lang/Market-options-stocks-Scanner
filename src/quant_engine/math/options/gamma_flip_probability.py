"""Cálculo analítico de la probabilidad de Gamma Flip bajo Movimiento Browniano Geométrico."""

from __future__ import annotations

import math


def estimate_gamma_flip_probability(
    spot: float,
    zgl: float,
    iv: float,
    dte_days: float,
    r: float = 0.05,
) -> float:
    """Estima la probabilidad de que el precio del activo (spot) cruce el Zero Gamma Level (zgl)

    antes del vencimiento (dte_days) bajo la hipótesis de Movimiento Browniano Geométrico (GBM).

    Aplica la fórmula analítica de primer tiempo de paso (First Passage Time) para una barrera
    única constante B = zgl, dadas la tasa libre de riesgo `r` y la volatilidad implícita `iv`.
    """
    if spot <= 0.0 or zgl <= 0.0 or iv <= 0.0:
        return 0.0

    T = max(dte_days / 365.0, 1e-9)

    # Si ya está en o ha cruzado el nivel flip
    if abs(spot - zgl) < 1e-12:
        return 1.0

    def norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / 1.4142135623730951))

    # Log-ratio de distancia a la barrera
    # Si spot > zgl, buscamos la probabilidad de caer hasta zgl (mínimo)
    # Si spot < zgl, buscamos la probabilidad de subir hasta zgl (máximo)
    ln_B_S = math.log(zgl / spot)
    sigma = iv
    sigma_sqrt_T = sigma * math.sqrt(T)

    # Parámetros del drift
    mu = r - 0.5 * sigma**2

    # Exponente de escala para el término reflectivo
    # scale = (B/S_0) ** (2*mu / sigma^2)
    # Para evitar overflow o división por cero si sigma es muy baja
    denom_scale = sigma**2
    if denom_scale < 1e-10:
        return 1.0 if (spot >= zgl if zgl <= spot else spot <= zgl) else 0.0

    power = (2.0 * mu) / denom_scale
    scale = math.exp(power * ln_B_S)

    if zgl < spot:
        # Barrera inferior (precio cae a ZGL)
        d1 = (ln_B_S - mu * T) / sigma_sqrt_T
        d2 = (ln_B_S + mu * T) / sigma_sqrt_T
        prob = norm_cdf(d1) + scale * norm_cdf(d2)
    else:
        # Barrera superior (precio sube a ZGL)
        d1 = (-ln_B_S + mu * T) / sigma_sqrt_T
        d2 = (-ln_B_S - mu * T) / sigma_sqrt_T
        prob = norm_cdf(d1) + scale * norm_cdf(d2)

    return float(max(min(prob, 1.0), 0.0))
