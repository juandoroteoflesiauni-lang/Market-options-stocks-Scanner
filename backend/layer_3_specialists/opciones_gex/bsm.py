"""Núcleo Matemático Black-Scholes-Merton (BSM) — Sector Opciones/GEX.

Implementa el motor de pricing de opciones y cálculo de Griegas de primer,
segundo y tercer orden. Soporta cálculos escalares (Python puro) y
vectorizados (NumPy) para el análisis de cadenas completas.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import TypeAlias

import numpy as np
import numpy.typing as npt
from scipy.optimize import brentq, newton  # type: ignore[import-untyped]


class OptionType(str, Enum):
    """Tipo de opción (Call/Put)."""

    CALL = "CALL"
    PUT = "PUT"


# ─────────────────────────────────────────────────────────────────────────────
# §0  CONSTANTES DE CONTROL
# ─────────────────────────────────────────────────────────────────────────────

_T_FLOOR = 1e-9  # Protección para 0 DTE
_SIGMA_FLOOR = 1e-4  # Floor interno de IV
_DAYS_YEAR = 365.0
_SQRT_TWO = math.sqrt(2.0)
_TWO_PI_SQRT = math.sqrt(2.0 * math.pi)
_DEFAULT_R = 0.05
_IV_SOLVER_LO = 1e-3
_IV_SOLVER_HI = 5.0

FloatArray: TypeAlias = npt.NDArray[np.float64]


def _norm_cdf_scalar(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT_TWO))


def _as_float_array(value: npt.ArrayLike) -> FloatArray:
    return np.asarray(value, dtype=np.float64)


# ─────────────────────────────────────────────────────────────────────────────
# §1  KERNEL ESCALAR (Alta Velocidad para Opciones Individuales)
# ─────────────────────────────────────────────────────────────────────────────


def _d1_scalar(S: float, K: float, T: float, r: float, sigma: float) -> float:
    denom = sigma * math.sqrt(T)
    return (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / denom


def _phi(x: float) -> float:
    """Función de densidad de probabilidad Normal Estándar."""
    return math.exp(-0.5 * x * x) / _TWO_PI_SQRT


def _call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(S - K, 0.0)
    d1 = _d1_scalar(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return max(S * _norm_cdf_scalar(d1) - K * math.exp(-r * T) * _norm_cdf_scalar(d2), 0.0)


def _put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(K - S, 0.0)
    d1 = _d1_scalar(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return max(K * math.exp(-r * T) * _norm_cdf_scalar(-d2) - S * _norm_cdf_scalar(-d1), 0.0)


def _delta(S: float, K: float, T: float, r: float, sigma: float, opt: OptionType) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1_scalar(S, K, T, r, sigma)
    raw = _norm_cdf_scalar(d1)
    return raw if opt == OptionType.CALL else raw - 1.0


def _gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    denom = S * sigma * math.sqrt(T)
    return max(_phi(_d1_scalar(S, K, T, r, sigma)) / denom, 0.0)


def _vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Normalizado por 1 punto porcentual de IV."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    return max(S * _phi(_d1_scalar(S, K, T, r, sigma)) * math.sqrt(T) / 100.0, 0.0)


def _theta(S: float, K: float, T: float, r: float, sigma: float, opt: OptionType) -> float:
    """Decaimiento temporal diario (Theta)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = _d1_scalar(S, K, T, r, sigma)
    d2 = d1 - sigma * sqrt_T
    phi_d1 = _phi(d1)
    disc = math.exp(-r * T)
    decay = -S * phi_d1 * sigma / (2.0 * sqrt_T)
    if opt == OptionType.CALL:
        carry = -r * K * disc * _norm_cdf_scalar(d2)
    else:
        carry = r * K * disc * _norm_cdf_scalar(-d2)
    return (decay + carry) / _DAYS_YEAR


def _rho(S: float, K: float, T: float, r: float, sigma: float, opt: OptionType) -> float:
    """Sensibilidad a tasa de interés por 1 pp."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1_scalar(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    disc = math.exp(-r * T)
    if opt == OptionType.CALL:
        return K * T * disc * _norm_cdf_scalar(d2) / 100.0
    return -K * T * disc * _norm_cdf_scalar(-d2) / 100.0


def _charm(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    T_s = max(T, _T_FLOOR)
    s_s = max(sigma, _SIGMA_FLOOR)
    sqrt_T = math.sqrt(T_s)
    d1 = _d1_scalar(S, K, T_s, r, s_s)
    d2 = d1 - s_s * sqrt_T
    numer = 2.0 * r * T_s - d2 * s_s * sqrt_T
    denom = 2.0 * T_s * s_s * sqrt_T
    return -_phi(d1) * numer / (denom + 1e-12) / _DAYS_YEAR


# ─────────────────────────────────────────────────────────────────────────────
# §2  KERNEL VECTORIZADO (Eficiencia NumPy para Cadenas Completas)
# ─────────────────────────────────────────────────────────────────────────────


def _d1_vec(S: float, K: FloatArray, T: FloatArray, r: float, sigma: FloatArray) -> FloatArray:
    return _as_float_array((np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T)))


def _gamma_vec_kernel(
    S: float,
    K: FloatArray,
    T: FloatArray,
    sigma: FloatArray,
    r: float,
) -> FloatArray:
    T_s = np.maximum(T, _T_FLOOR)
    s_s = np.maximum(sigma, _SIGMA_FLOOR)
    sqrt_T = np.sqrt(T_s)
    d1 = _d1_vec(S, K, T_s, r, s_s)
    phi_d1 = np.exp(-0.5 * d1**2) / _TWO_PI_SQRT
    return _as_float_array(np.maximum(phi_d1 / (S * s_s * sqrt_T), 0.0))


def _vega_vec_kernel(
    S: float,
    K: FloatArray,
    T: FloatArray,
    sigma: FloatArray,
    r: float,
) -> FloatArray:
    T_s = np.maximum(T, _T_FLOOR)
    s_s = np.maximum(sigma, _SIGMA_FLOOR)
    d1 = _d1_vec(S, K, T_s, r, s_s)
    phi_d1 = np.exp(-0.5 * d1**2) / _TWO_PI_SQRT
    return _as_float_array(np.maximum(S * phi_d1 * np.sqrt(T_s) / 100.0, 0.0))


def _vanna_vec_kernel(
    S: float,
    K: FloatArray,
    T: FloatArray,
    sigma: FloatArray,
    r: float,
) -> FloatArray:
    T_s = np.maximum(T, _T_FLOOR)
    s_s = np.maximum(sigma, _SIGMA_FLOOR)
    sqrt_T = np.sqrt(T_s)
    d1 = _d1_vec(S, K, T_s, r, s_s)
    d2 = d1 - s_s * sqrt_T
    phi_d1 = np.exp(-0.5 * d1**2) / _TWO_PI_SQRT
    return _as_float_array(-phi_d1 * d2 / s_s)


def _charm_vec_kernel(
    S: float,
    K: FloatArray,
    T: FloatArray,
    r: float,
    sigma: FloatArray,
) -> FloatArray:
    T_s = np.maximum(T, _T_FLOOR)
    s_s = np.maximum(sigma, _SIGMA_FLOOR)
    sqrt_T = np.sqrt(T_s)
    d1 = _d1_vec(S, K, T_s, r, s_s)
    d2 = d1 - s_s * sqrt_T
    phi_d1 = np.exp(-0.5 * d1**2) / _TWO_PI_SQRT
    numer = 2.0 * r * T_s - d2 * s_s * sqrt_T
    denom = 2.0 * T_s * s_s * sqrt_T
    return _as_float_array(-phi_d1 * numer / (denom + 1e-12) / _DAYS_YEAR)


# ─────────────────────────────────────────────────────────────────────────────
# §3  IV SOLVER
# ─────────────────────────────────────────────────────────────────────────────


def _iv_solver(
    S: float,
    K: float,
    T: float,
    mkt_p: float,
    opt: OptionType,
    r: float,
    x0: float,
) -> float:
    def obj(s: float) -> float:
        return (
            _call_price(S, K, T, r, s) if opt == OptionType.CALL else _put_price(S, K, T, r, s)
        ) - mkt_p

    def d_obj(s: float) -> float:
        return _vega(S, K, T, r, s) * 100.0

    try:
        iv = float(newton(obj, x0, fprime=d_obj, maxiter=100, tol=1e-8))
        if _IV_SOLVER_LO <= iv <= _IV_SOLVER_HI:
            return round(iv, 8)
    except Exception:
        pass
    try:
        iv = float(brentq(obj, _IV_SOLVER_LO, _IV_SOLVER_HI, maxiter=200, xtol=1e-8))
        return round(iv, 8)
    except Exception:
        return math.nan


# ─────────────────────────────────────────────────────────────────────────────
# §4  CORE PRICER FACADE
# ─────────────────────────────────────────────────────────────────────────────


class BlackScholesPricer:
    """Motor Stateless de Pricing BSM y Griegas."""

    T_FLOOR = 1 / 365
    SIGMA_FLOOR = 1e-4
    CONTRACT_SIZE = 100

    @staticmethod
    def price(
        S: float, K: float, T: float, r: float, sigma: float, opt: OptionType = OptionType.CALL
    ) -> float:
        T_s, s_s = max(T, _T_FLOOR), max(sigma, _SIGMA_FLOOR)
        return (
            _call_price(S, K, T_s, r, s_s)
            if opt == OptionType.CALL
            else _put_price(S, K, T_s, r, s_s)
        )

    @staticmethod
    def delta(
        S: float, K: float, T: float, r: float, sigma: float, opt: OptionType = OptionType.CALL
    ) -> float:
        T_s, s_s = max(T, _T_FLOOR), max(sigma, _SIGMA_FLOOR)
        return _delta(S, K, T_s, r, s_s, opt)

    @staticmethod
    def gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
        T_s, s_s = max(T, _T_FLOOR), max(sigma, _SIGMA_FLOOR)
        return _gamma(S, K, T_s, r, s_s)

    @staticmethod
    def vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
        T_s, s_s = max(T, _T_FLOOR), max(sigma, _SIGMA_FLOOR)
        return _vega(S, K, T_s, r, s_s)

    @staticmethod
    def theta(
        S: float, K: float, T: float, r: float, sigma: float, opt: OptionType = OptionType.CALL
    ) -> float:
        T_s, s_s = max(T, _T_FLOOR), max(sigma, _SIGMA_FLOOR)
        return _theta(S, K, T_s, r, s_s, opt)

    @staticmethod
    def rho(
        S: float, K: float, T: float, r: float, sigma: float, opt: OptionType = OptionType.CALL
    ) -> float:
        T_s, s_s = max(T, _T_FLOOR), max(sigma, _SIGMA_FLOOR)
        return _rho(S, K, T_s, r, s_s, opt)

    @staticmethod
    def rho_argentina(
        S: float,
        K: float,
        T: float,
        r_caucion: float,
        sigma: float,
        opt: OptionType = OptionType.CALL,
    ) -> float:
        """Rho especializado para entornos de alta tasa (Argentina) usando Caución Bursátil."""
        phi = 1.0 if opt == OptionType.CALL else -1.0
        T_s, s_s = max(T, _T_FLOOR), max(sigma, _SIGMA_FLOOR)
        d1 = (math.log(S / K) + (r_caucion + 0.5 * s_s**2) * T_s) / (s_s * math.sqrt(T_s))
        d2 = d1 - s_s * math.sqrt(T_s)
        disc = math.exp(-r_caucion * T_s)
        Nd2 = _norm_cdf_scalar(phi * d2)
        return round(phi * K * T_s * disc * Nd2, 6)

    @staticmethod
    def greeks(
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        opt: OptionType = OptionType.CALL,
        second_order: bool = True,
    ) -> dict[str, float]:
        T_s, s_s = max(T, _T_FLOOR), max(sigma, _SIGMA_FLOOR)
        d1 = _d1_scalar(S, K, T_s, r, s_s)
        res = {
            "theoretical_price": round(BlackScholesPricer.price(S, K, T_s, r, s_s, opt), 6),
            "delta": round(_delta(S, K, T_s, r, s_s, opt), 8),
            "gamma": round(_gamma(S, K, T_s, r, s_s), 8),
            "vega": round(_vega(S, K, T_s, r, s_s), 6),
            "theta": round(_theta(S, K, T_s, r, s_s, opt), 6),
            "rho": round(_rho(S, K, T_s, r, s_s, opt), 6),
            "d1": round(d1, 8),
            "d2": round(d1 - s_s * math.sqrt(T_s), 8),
        }
        if second_order:
            res["vanna"] = round(-_phi(d1) * (d1 - s_s * math.sqrt(T_s)) / s_s, 8)
            res["charm"] = round(_charm(S, K, T_s, r, s_s), 8)
        return res

    @staticmethod
    def implied_vol(
        mkt_p: float,
        S: float,
        K: float,
        T: float,
        r: float = _DEFAULT_R,
        opt: OptionType = OptionType.CALL,
        x0: float = 0.25,
    ) -> float:
        return _iv_solver(S, K, max(T, _T_FLOOR), mkt_p, opt, r, x0)

    # Vector Batch API
    @staticmethod
    def gamma_vec(
        S: float,
        K: npt.ArrayLike,
        T: npt.ArrayLike,
        r: float,
        sigma: npt.ArrayLike,
    ) -> FloatArray:
        return _gamma_vec_kernel(
            S, _as_float_array(K), _as_float_array(T), _as_float_array(sigma), r
        )

    @staticmethod
    def vega_vec(
        S: float,
        K: npt.ArrayLike,
        T: npt.ArrayLike,
        r: float,
        sigma: npt.ArrayLike,
    ) -> FloatArray:
        return _vega_vec_kernel(
            S, _as_float_array(K), _as_float_array(T), _as_float_array(sigma), r
        )

    @staticmethod
    def vanna_vec(
        S: float,
        K: npt.ArrayLike,
        T: npt.ArrayLike,
        r: float,
        sigma: npt.ArrayLike,
    ) -> FloatArray:
        return _vanna_vec_kernel(
            S, _as_float_array(K), _as_float_array(T), _as_float_array(sigma), r
        )

    @staticmethod
    def charm_vec(
        S: float,
        K: npt.ArrayLike,
        T: npt.ArrayLike,
        r: float,
        sigma: npt.ArrayLike,
    ) -> FloatArray:
        return _charm_vec_kernel(
            S, _as_float_array(K), _as_float_array(T), r, _as_float_array(sigma)
        )

    @staticmethod
    def speed_vec(
        S: float,
        K: npt.ArrayLike,
        T: npt.ArrayLike,
        r: float,
        sigma: npt.ArrayLike,
    ) -> FloatArray:
        """Approximate dGamma/dSpot with a central finite difference."""
        step = max(abs(float(S)) * 1e-4, 1e-3)
        up = BlackScholesPricer.gamma_vec(float(S) + step, K, T, r, sigma)
        down = BlackScholesPricer.gamma_vec(max(float(S) - step, 1e-6), K, T, r, sigma)
        return _as_float_array((up - down) / (2.0 * step))

    @staticmethod
    def zomma_vec(
        S: float,
        K: npt.ArrayLike,
        T: npt.ArrayLike,
        r: float,
        sigma: npt.ArrayLike,
    ) -> FloatArray:
        """Approximate dGamma/dVol with element-wise central finite differences."""
        sig = _as_float_array(sigma)
        step = np.maximum(np.abs(sig) * 1e-4, 1e-4)
        up = BlackScholesPricer.gamma_vec(S, K, T, r, sig + step)
        down = BlackScholesPricer.gamma_vec(S, K, T, r, np.maximum(sig - step, _SIGMA_FLOOR))
        return _as_float_array((up - down) / (2.0 * step))

    @staticmethod
    def color_vec(
        S: float,
        K: npt.ArrayLike,
        T: npt.ArrayLike,
        r: float,
        sigma: npt.ArrayLike,
    ) -> FloatArray:
        """Approximate dGamma/dTime with central finite differences."""
        t = _as_float_array(T)
        step = np.maximum(np.abs(t) * 1e-4, 1.0 / 36500.0)
        up = BlackScholesPricer.gamma_vec(S, K, t + step, r, sigma)
        down = BlackScholesPricer.gamma_vec(S, K, np.maximum(t - step, _T_FLOOR), r, sigma)
        return _as_float_array((up - down) / (2.0 * step))

    @staticmethod
    def ultima_vec(
        S: float,
        K: npt.ArrayLike,
        T: npt.ArrayLike,
        r: float,
        sigma: npt.ArrayLike,
    ) -> FloatArray:
        """Approximate second derivative of Vega with respect to volatility."""
        sig = _as_float_array(sigma)
        step = np.maximum(np.abs(sig) * 1e-4, 1e-4)
        up = BlackScholesPricer.vega_vec(S, K, T, r, sig + step)
        mid = BlackScholesPricer.vega_vec(S, K, T, r, sig)
        down = BlackScholesPricer.vega_vec(S, K, T, r, np.maximum(sig - step, _SIGMA_FLOOR))
        return _as_float_array((up - 2.0 * mid + down) / np.maximum(step * step, 1e-12))


# ─────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: OPCIONES
# Archivo      : bsm.py
# Sub-capa     : Engine (Math Core)
# Eliminado    : Referencias QuantumBeta V1 / Header legacy / Brent Fallback redundant comments.
# Preservado   : Fórmulas de pricing, Griegas 1er y 2do orden, Vectorización NumPy, IV Solver.
# Pendientes   : Integración de Griegas de 3er orden en la fachada pública BlackScholesPricer si es necesario.
# ─────────────────────────────────────────────────────────────
