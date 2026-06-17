"""Bjerksund-Stensland (2002) American option pricer — scalar float math. # [PD-2][TH]"""

from __future__ import annotations

import math
from typing import Literal

from backend.quant_engine.math.options.bsm import BlackScholesPricer, OptionType

_T_FLOOR = 1e-9
_SIGMA_FLOOR = 1e-4
_SQRT_TWO = math.sqrt(2.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT_TWO))


def _european_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return BlackScholesPricer.price(S, K, T, r, sigma, OptionType.CALL)


def _european_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return BlackScholesPricer.price(S, K, T, r, sigma, OptionType.PUT)


def _phi(
    S: float,
    T: float,
    gamma: float,
    H: float,
    I: float,
    r: float,
    b: float,
    sigma: float,
) -> float:
    """Helper phi from Bjerksund-Stensland (1993/2002)."""
    if T <= _T_FLOOR or sigma <= _SIGMA_FLOOR or S <= 0 or I <= 0 or H <= 0:
        return 0.0
    sigma_sqrt_t = sigma * math.sqrt(T)
    lambda_val = -r + gamma * b + 0.5 * gamma * (gamma - 1.0) * sigma * sigma
    d = -(math.log(S / H) + (b + (gamma - 0.5) * sigma * sigma) * T) / sigma_sqrt_t
    kappa = 2.0 * b / (sigma * sigma) + (2.0 * gamma - 1.0)
    log_i_s = math.log(I / S)
    return (
        math.exp(lambda_val * T)
        * S**gamma
        * (_norm_cdf(d) - (I / S) ** kappa * _norm_cdf(d - 2.0 * log_i_s / sigma_sqrt_t))
    )


def _european_call_with_div(
    S: float, K: float, T: float, r: float, q: float, sigma: float
) -> float:
    """European call with continuous dividend yield."""
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _american_call_bs2002(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """American call with continuous dividend yield q (Bjerksund-Stensland 1993)."""
    if q <= 1e-12:
        return _european_call(S, K, T, r, sigma)
    b = r - q
    if b >= r:
        return _european_call_with_div(S, K, T, r, q, sigma)
    beta = (0.5 - b / (sigma * sigma)) + math.sqrt(
        (b / (sigma * sigma) - 0.5) ** 2 + 2.0 * r / (sigma * sigma)
    )
    b_inf = (beta / (beta - 1.0)) * K
    b0 = max(K, (r / (r - q)) * K) if abs(r - q) > 1e-12 else K
    ht = -(b * T + 2.0 * sigma * math.sqrt(T)) * (b0 / (b_inf - b0))
    trigger = b0 + (b_inf - b0) * (1.0 - math.exp(ht))

    if trigger <= S:
        return max(S - K, 0.0)

    alpha = (trigger - K) * trigger ** (-beta)
    price = (
        alpha * S**beta
        - alpha * _phi(S, T, beta, trigger, trigger, r, b, sigma)
        + _phi(S, T, 1.0, trigger, trigger, r, b, sigma)
        - _phi(S, T, 1.0, K, trigger, r, b, sigma)
        - K * _phi(S, T, 0.0, trigger, trigger, r, b, sigma)
        + K * _phi(S, T, 0.0, K, trigger, r, b, sigma)
    )
    return max(price, S - K, 0.0)


class BjerksundStenslandPricer:
    """American option pricer (Bjerksund-Stensland 2002 two-step approximation)."""

    @staticmethod
    def price(
        S: float,
        K: float,
        T: float,
        r: float,
        q: float,
        sigma: float,
        option_type: Literal["CALL", "PUT"],
    ) -> float:
        """Price American option; put via put-call transformation."""
        if any(math.isnan(x) for x in (S, K, T, r, q, sigma)):
            return float("nan")
        if S <= 0 or K <= 0:
            return 0.0
        if T <= _T_FLOOR:
            intrinsic = max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0)
            return intrinsic
        if sigma <= _SIGMA_FLOOR:
            intrinsic = max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0)
            return intrinsic

        if option_type == "CALL":
            return _american_call_bs2002(S, K, T, r, q, sigma)

        # Put-call transformation: P(S,K) = C(K,S) with r<->q
        return _american_call_bs2002(K, S, T, q, r, sigma)


__all__ = ["BjerksundStenslandPricer"]
