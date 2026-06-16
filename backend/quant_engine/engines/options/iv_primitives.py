"""Primitivas de Volatilidad e Indicadores de Régimen — Sector Opciones/GEX.

Proporciona funciones vectorizadas para el cálculo de volatilidad histórica (HV),
regímenes de IV (Rank/Percentile), Volatility Risk Premium (VRP) y análisis
de estructura temporal y Skew de la cadena de opciones.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.optimize import brentq  # type: ignore[import-untyped]

from .bsm import BlackScholesPricer, OptionType
from .derivatives import SVIParameters, VolatilitySurfaceMath

FloatArray = npt.NDArray[np.float64]


def _norm_cdf_array(x: FloatArray) -> FloatArray:
    erf = np.vectorize(math.erf)
    return np.asarray(0.5 * (1.0 + erf(x / math.sqrt(2.0))), dtype=np.float64)


# ─────────────────────────────────────────────────────────────────────────────
# §1  VOLATILIDAD HISTÓRICA (HV)
# ─────────────────────────────────────────────────────────────────────────────


def historical_volatility(log_returns: FloatArray, window: int) -> float:
    """Calcula la Volatilidad Histórica anualizada (Close-to-Close)."""
    if len(log_returns) < window:
        return np.nan
    return float(np.std(log_returns[-window:], ddof=1) * math.sqrt(252))


def rolling_historical_volatility(log_returns: FloatArray, window: int) -> FloatArray:
    """HV Rolling anualizada optimizada vía stride_tricks."""
    n = len(log_returns)
    if n < window:
        return np.array([], dtype=np.float64)
    n_windows = n - window + 1
    shape = (n_windows, window)
    strides = (log_returns.strides[0], log_returns.strides[0])
    windows = np.lib.stride_tricks.as_strided(log_returns, shape=shape, strides=strides)
    stds = np.std(windows, axis=1, ddof=1)
    return np.asarray(stds * math.sqrt(252), dtype=np.float64)


# ─────────────────────────────────────────────────────────────────────────────
# §2  IV RANK e IV PERCENTILE
# ─────────────────────────────────────────────────────────────────────────────


def iv_rank(iv: float, iv_min: float, iv_max: float) -> float:
    """Posición relativa de la IV actual en su rango histórico [0, 1]."""
    if not math.isfinite(iv) or not math.isfinite(iv_min) or not math.isfinite(iv_max):
        return np.nan
    spread = iv_max - iv_min
    if abs(spread) < 1e-12:
        return np.nan
    return float(np.clip((iv - iv_min) / spread, 0.0, 1.0))


def iv_percentile(iv: float, historical_ivs: FloatArray) -> float:
    """Fracción de días históricos con IV inferior a la actual [0, 1]."""
    if not math.isfinite(iv) or len(historical_ivs) == 0:
        return np.nan
    valid = historical_ivs[np.isfinite(historical_ivs)]
    if len(valid) == 0:
        return np.nan
    return float(np.mean(valid < iv))


# ─────────────────────────────────────────────────────────────────────────────
# §3  VOLATILITY RISK PREMIUM (VRP)
# ─────────────────────────────────────────────────────────────────────────────


def vrp_log_ratio(iv: float, hv: float) -> float:
    """Prima de Riesgo de Volatilidad: ln(IV / HV)."""
    if not math.isfinite(iv) or not math.isfinite(hv):
        return np.nan
    if iv <= 0.0 or hv <= 0.0:
        return np.nan
    return float(math.log(iv / hv))


# ─────────────────────────────────────────────────────────────────────────────
# §4  BSM PRICE (Utility Vectorized)
# ─────────────────────────────────────────────────────────────────────────────


def bsm_price(
    S: FloatArray | float,
    K: FloatArray | float,
    T: float,
    r: float,
    sigma: FloatArray | float,
    is_call: bool = True,
) -> FloatArray:
    """Cálculo vectorizado rápido del precio teórico BSM."""
    S = np.asarray(S, dtype=np.float64)
    K = np.asarray(K, dtype=np.float64)
    sigma = np.asarray(sigma, dtype=np.float64)

    if T <= 0:
        intrinsic = np.maximum(S - K, 0.0) if is_call else np.maximum(K - S, 0.0)
        return np.asarray(intrinsic, dtype=np.float64)

    sqrt_T = math.sqrt(T)
    valid = (sigma > 0) & (K > 0) & (S > 0)
    price = np.full_like(sigma, np.nan, dtype=np.float64)

    if not np.any(valid):
        return price

    S_v, K_v, sigma_v = np.broadcast_arrays(S, K, sigma)
    S_v, K_v, sigma_v = S_v[valid], K_v[valid], sigma_v[valid]

    d1 = (np.log(S_v / K_v) + (r + 0.5 * sigma_v**2) * T) / (sigma_v * sqrt_T)
    d2 = d1 - sigma_v * sqrt_T

    disc = math.exp(-r * T)
    if is_call:
        price[valid] = S_v * _norm_cdf_array(d1) - K_v * disc * _norm_cdf_array(d2)
    else:
        price[valid] = K_v * disc * _norm_cdf_array(-d2) - S_v * _norm_cdf_array(-d1)

    return np.asarray(price, dtype=np.float64)


# ─────────────────────────────────────────────────────────────────────────────
# §5  SKEW METRICS
# ─────────────────────────────────────────────────────────────────────────────


def compute_skew_metrics(
    strikes: FloatArray,
    ivs: FloatArray,
    spot: float,
    window_otm: float = 0.08,
) -> dict[str, float]:
    """Cálculo de métricas de Skew para una expiración (25-Delta Proxy)."""
    result = {
        "atm_iv": np.nan,
        "skew_25d": np.nan,
        "risk_reversal": np.nan,
        "butterfly": np.nan,
        "skew_slope": np.nan,
    }

    valid = np.isfinite(ivs) & (ivs > 0)
    if np.sum(valid) < 3:
        return result

    s_v, iv_v = strikes[valid], ivs[valid]

    atm_idx = int(np.argmin(np.abs(s_v - spot)))
    atm_iv = float(iv_v[atm_idx])
    result["atm_iv"] = atm_iv

    if len(s_v) >= 2:
        strikes_norm = (s_v - spot) / spot
        slope, _ = np.polyfit(strikes_norm, iv_v, deg=1)
        result["skew_slope"] = float(slope)

    k_put, k_call = spot * (1.0 - window_otm), spot * (1.0 + window_otm)
    p_iv = float(np.interp(k_put, s_v, iv_v)) if s_v[0] <= k_put <= s_v[-1] else np.nan
    c_iv = float(np.interp(k_call, s_v, iv_v)) if s_v[0] <= k_call <= s_v[-1] else np.nan

    if math.isfinite(p_iv) and math.isfinite(c_iv):
        skew = p_iv - c_iv
        result.update(
            {
                "skew_25d": float(skew),
                "risk_reversal": float(skew),
                "butterfly": float((p_iv + c_iv) / 2.0 - atm_iv),
            }
        )

    return result


def _bracket_brentq(f: Callable[[float], float], spot: float) -> float | None:
    """Find first root of ``f(K)=0`` on a wide strike grid (spot-scaled)."""
    lo = max(spot * 0.02, 1e-4)
    hi = spot * 15.0
    grid = np.geomspace(lo, hi, num=180)
    prev_k: float | None = None
    prev_e: float | None = None
    for K in grid:
        kf = float(K)
        e = f(kf)
        if not math.isfinite(e):
            prev_k, prev_e = kf, e
            continue
        if prev_k is not None and prev_e is not None and math.isfinite(prev_e):
            if prev_e == 0:
                return prev_k
            if e == 0:
                return kf
            if prev_e * e < 0:
                try:
                    return float(brentq(f, prev_k, kf, xtol=1e-7, maxiter=120))
                except ValueError:
                    pass
        prev_k, prev_e = kf, e
    return None


def _strike_call_delta_svi(
    params: SVIParameters,
    spot: float,
    r: float,
    target_delta: float,
    iv_at: Callable[[float], float],
) -> float | None:
    T = float(params.tte)
    if spot <= 0 or T <= 0:
        return None

    def err(K: float) -> float:
        if K <= 0:
            return 1e9
        sig = iv_at(K)
        if not math.isfinite(sig) or sig <= 1e-6:
            return 1e9
        d = BlackScholesPricer.delta(spot, K, T, r, sig, OptionType.CALL)
        return float(d - target_delta)

    return _bracket_brentq(err, spot)


def _strike_put_delta_svi(
    params: SVIParameters,
    spot: float,
    r: float,
    target_delta: float,
    iv_at: Callable[[float], float],
) -> float | None:
    T = float(params.tte)
    if spot <= 0 or T <= 0:
        return None

    def err(K: float) -> float:
        if K <= 0:
            return 1e9
        sig = iv_at(K)
        if not math.isfinite(sig) or sig <= 1e-6:
            return 1e9
        d = BlackScholesPricer.delta(spot, K, T, r, sig, OptionType.PUT)
        return float(d - target_delta)

    return _bracket_brentq(err, spot)


def compute_skew_metrics_institutional_svi(
    svi_params: SVIParameters,
    spot: float,
    r: float,
) -> dict[str, float]:
    """
    Skew institucional vía SVI + BSM: IV(K) del slice calibrado; strikes por Δ vía bisección/brentq.

    RR = IV(25Δ call) − IV(25Δ put); Fly = (IV_call + IV_put)/2 − IV_ATM con IV_ATM = IV(K=F).
    """
    result: dict[str, float] = {
        "atm_iv": np.nan,
        "skew_25d": np.nan,
        "risk_reversal": np.nan,
        "butterfly": np.nan,
        "risk_reversal_10": np.nan,
        "butterfly_10": np.nan,
        "skew_slope": np.nan,
    }
    T = float(svi_params.tte)
    F = float(svi_params.forward)
    if spot <= 0 or T <= 0 or F <= 0:
        return result

    def iv_at(K: float) -> float:
        v = VolatilitySurfaceMath.svi_to_vol_slice(np.asarray([K], dtype=np.float64), svi_params)[0]
        return float(v) if np.isfinite(v) else float("nan")

    iv_atm = iv_at(F)
    if not math.isfinite(iv_atm) or iv_atm <= 0:
        return result
    result["atm_iv"] = iv_atm

    def rr_fly(abs_delta: float) -> tuple[float, float]:
        Kc = _strike_call_delta_svi(svi_params, spot, r, abs_delta, iv_at)
        Kp = _strike_put_delta_svi(svi_params, spot, r, -abs_delta, iv_at)
        if Kc is None or Kp is None:
            return float("nan"), float("nan")
        iv_c = iv_at(Kc)
        iv_p = iv_at(Kp)
        if not all(math.isfinite(x) and x > 0 for x in (iv_c, iv_p)):
            return float("nan"), float("nan")
        rr = iv_c - iv_p
        fly = (iv_c + iv_p) / 2.0 - iv_atm
        return rr, fly

    rr25, fly25 = rr_fly(0.25)
    rr10, fly10 = rr_fly(0.10)
    result["skew_25d"] = rr25
    result["risk_reversal"] = rr25
    result["butterfly"] = fly25
    result["risk_reversal_10"] = rr10
    result["butterfly_10"] = fly10
    return result


# ─────────────────────────────────────────────────────────────────────────────
# §6  TERM STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────


def compute_term_structure(atm_ivs: FloatArray, dtes: FloatArray) -> dict[str, Any]:
    """Análisis de la Estructura Temporal de la IV ATM vs DTE (Regresión OLS)."""
    _CONTANGO_MIN = 1e-5
    valid = np.isfinite(atm_ivs) & np.isfinite(dtes) & (dtes > 0)
    s_v, d_v = atm_ivs[valid], dtes[valid]

    res = {
        "slope": np.nan,
        "intercept": np.nan,
        "r_squared": np.nan,
        "front_iv": np.nan,
        "back_iv": np.nan,
        "kink_dte": np.nan,
        "contango": False,
        "backwardation": False,
    }

    n = len(s_v)
    if n < 2:
        if n == 1:
            res["front_iv"] = res["back_iv"] = float(s_v[0])
        return res

    coeffs = np.polyfit(d_v, s_v, deg=1)
    slope, intercept = float(coeffs[0]), float(coeffs[1])

    y_hat = np.polyval(coeffs, d_v)
    ss_tot = np.sum((s_v - np.mean(s_v)) ** 2)
    r_sq = 1.0 - np.sum((s_v - y_hat) ** 2) / ss_tot if ss_tot > 1e-12 else 0.0

    ord_idx = np.argsort(d_v)
    res.update(
        {
            "slope": slope,
            "intercept": intercept,
            "r_squared": r_sq,
            "front_iv": float(s_v[ord_idx[0]]),
            "back_iv": float(s_v[ord_idx[-1]]),
            "contango": slope > _CONTANGO_MIN,
            "backwardation": slope < -_CONTANGO_MIN,
        }
    )

    if n >= 3:
        d_ord, iv_ord = d_v[ord_idx], s_v[ord_idx]
        d2 = np.diff(iv_ord, n=2)
        res["kink_dte"] = float(d_ord[np.argmax(np.abs(d2)) + 1])

    return res


# ─────────────────────────────────────────────────────────────────────────────
# §7  ATM IV FROM CHAIN
# ─────────────────────────────────────────────────────────────────────────────


def atm_iv_from_chain(
    strikes: FloatArray, call_ivs: FloatArray, put_ivs: FloatArray, spot: float
) -> float:
    """Calcula la IV ATM promediando Call y Put en el strike más cercano al spot."""
    if len(strikes) == 0:
        return np.nan
    idx = int(np.argmin(np.abs(strikes - spot)))
    c, p = call_ivs[idx], put_ivs[idx]

    if math.isfinite(c) and math.isfinite(p):
        return float((c + p) / 2.0)
    if math.isfinite(c):
        return float(c)
    if math.isfinite(p):
        return float(p)

    all_iv = np.concatenate([call_ivs[np.isfinite(call_ivs)], put_ivs[np.isfinite(put_ivs)]])
    return float(np.nanmedian(all_iv)) if len(all_iv) > 0 else np.nan


# ─────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: OPCIONES
# Archivo      : iv_primitives.py
# Sub-capa     : Engine (Volatility Primitives)
# Eliminado    : Referencias QuantumBeta V1 / Header legacy / as_strided debug notes.
# Preservado   : HV, rolling HV, IV Rank/Percentile, VRP, Skew/Term Structure logic.
# Pendientes   : Ninguno.
# ─────────────────────────────────────────────────────────────
