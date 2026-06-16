from __future__ import annotations
"""Motor de Derivados y Superficies de Volatilidad — Sector Opciones/GEX.

Implementa la parametrización SVI (Gatheral), extracción de densidades de
riesgo-neutral (Breeden-Litzenberger) y el motor de exposición de dealers
(GEX, VEX, CEX) para análisis institucional.
"""


import math
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.optimize import minimize


from .bsm import BlackScholesPricer

FloatArray = npt.NDArray[np.float64]


def _as_float_array(value: npt.ArrayLike) -> FloatArray:
    return np.asarray(value, dtype=np.float64)


def _norm_cdf_array(x: FloatArray) -> FloatArray:
    erf = np.vectorize(math.erf)
    return np.asarray(0.5 * (1.0 + erf(x / math.sqrt(2.0))), dtype=np.float64)


# ─────────────────────────────────────────────────────────────────────────────
# §1  MODELOS DE SUPERFICIE
# ─────────────────────────────────────────────────────────────────────────────


class SVIParameters(BaseModel):
    """
    Parametrización SVI (Gatheral) para una expiración.
    w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma^2))
    k = ln(K/F) es la log-moneyness.
    """

    model_config = ConfigDict(frozen=True)

    tte: float = Field(..., gt=0.0)
    a: float
    b: float = Field(..., ge=0.0)
    rho: float = Field(..., ge=-0.9999, le=0.9999)
    m: float
    sigma: float = Field(..., gt=0.0)
    forward: float = Field(..., gt=0.0)

    @model_validator(mode="after")
    def butterfly_no_arb(self) -> SVIParameters:
        """Consistencia para evitar arbitraje de mariposa (wing floor >= 0)."""
        wing_floor = self.a + self.b * self.sigma * math.sqrt(1.0 - self.rho**2)
        if wing_floor < -1e-9:
            raise ValueError(f"SVI butterfly no-arb violated: {wing_floor:.6f} < 0")
        return self


class ImpliedPDFResult(BaseModel):
    """Densidad de Probabilidad Implícita (Breeden-Litzenberger)."""

    model_config = ConfigDict(frozen=True)

    strikes: list[float]
    density: list[float]
    cdf: list[float]
    forward: float
    tte: float
    risk_neutral_mean: float
    risk_neutral_std: float
    skewness: float
    excess_kurtosis: float
    left_tail_prob: float
    right_tail_prob: float
    tail_regime: Literal["LEFT_FAT", "RIGHT_FAT", "SYMMETRIC", "FAT_BOTH"]


# ─────────────────────────────────────────────────────────────────────────────
# §2  GEOMETRÍA DE LA SUPERFICIE (SVI)
# ─────────────────────────────────────────────────────────────────────────────


class VolatilitySurfaceMath:
    """Motor de cálculo de superficies de volatilidad."""

    @staticmethod
    def svi_total_var(k: FloatArray, params: SVIParameters) -> FloatArray:
        """Calcula la varianza total w(k) usando el modelo SVI."""
        km = k - params.m
        return _as_float_array(
            params.a + params.b * (params.rho * km + np.sqrt(km**2 + params.sigma**2))
        )

    @staticmethod
    def svi_to_vol_slice(strikes: FloatArray, params: SVIParameters) -> FloatArray:
        """Convierte un grid de strikes a volatilidades implícitas BSM vía SVI."""
        k = np.log(strikes / params.forward)
        w = VolatilitySurfaceMath.svi_total_var(k, params)
        w = np.maximum(w, 1e-10)
        return _as_float_array(np.sqrt(w / params.tte))

    @staticmethod
    def svi_calibrate(
        strikes: FloatArray,
        market_vols: FloatArray,
        tte: float,
        forward: float,
        initial_guess: tuple[float, ...] | None = None,
    ) -> SVIParameters:
        """Calibración L-BFGS-B de parámetros SVI minimizando error cuadrático."""
        k = np.log(strikes / forward)
        w_mkt = market_vols**2 * tte

        if initial_guess is None:
            atm_w = float(np.interp(0.0, k, w_mkt))
            initial_guess = (atm_w * 0.5, 0.1, -0.3, 0.0, 0.2)

        def objective(x: FloatArray) -> float:
            a, b, rho, m, sigma = x
            if b < 0 or abs(rho) >= 1 or sigma <= 0:
                return 1e12
            km = k - m
            w_model = a + b * (rho * km + np.sqrt(km**2 + sigma**2))
            neg_mask = w_model < 0
            penalty = 1e6 * float(np.sum(w_model[neg_mask] ** 2)) if neg_mask.any() else 0.0
            return float(np.sum((w_model - w_mkt) ** 2)) + penalty

        bounds = [(None, None), (1e-8, None), (-0.9999, 0.9999), (None, None), (1e-4, None)]
        res = minimize(objective, x0=initial_guess, method="L-BFGS-B", bounds=bounds)
        a, b, rho, m, sigma = res.x

        return SVIParameters(
            tte=tte,
            a=float(a),
            b=float(b),
            rho=float(rho),
            m=float(m),
            sigma=max(float(sigma), 1e-5),
            forward=float(forward),
        )

    @staticmethod
    def bl_pdf(params: SVIParameters, r: float = 0.04, n_points: int = 500) -> ImpliedPDFResult:
        """Extrae la PDF de riesgo-neutral vía Breeden-Litzenberger."""

        def _trapz1d(y: FloatArray, x: FloatArray | None = None) -> float:
            """NumPy 2 depreca ``trapz``; usar ``trapezoid`` cuando exista."""
            fn = getattr(np, "trapezoid", None)
            if fn is not None:
                return float(fn(y, x=x))
            return float(np.trapz(y, x))


        F, T = params.forward, params.tte
        disc = math.exp(-r * T)

        K_grid = _as_float_array(np.linspace(F * 0.5, F * 1.5, n_points))
        vols = VolatilitySurfaceMath.svi_to_vol_slice(K_grid, params)

        def bsm_call(S_arr: FloatArray, sig_arr: FloatArray) -> FloatArray:
            d1 = (np.log(F / S_arr) + 0.5 * sig_arr**2 * T) / (sig_arr * math.sqrt(T))
            d2 = d1 - sig_arr * math.sqrt(T)
            return _as_float_array(disc * (F * _norm_cdf_array(d1) - S_arr * _norm_cdf_array(d2)))

        C = bsm_call(K_grid, vols)
        d2C_dK2 = np.gradient(np.gradient(C, K_grid), K_grid)
        density = np.maximum(math.exp(r * T) * d2C_dK2, 0.0)

        total = _trapz1d(density, K_grid)
        if total > 1e-10:
            density /= total

        cdf = _as_float_array(np.clip(np.cumsum(density) * (K_grid[1] - K_grid[0]), 0.0, 1.0))
        mean_q = _trapz1d(K_grid * density, K_grid)
        std_q = math.sqrt(max(_trapz1d((K_grid - mean_q) ** 2 * density, K_grid), 0.0))

        skew_q = kurt_q = 0.0
        if std_q > 1e-10:
            skew_q = _trapz1d(((K_grid - mean_q) / std_q) ** 3 * density, K_grid)
            kurt_q = _trapz1d(((K_grid - mean_q) / std_q) ** 4 * density, K_grid) - 3.0

        left_tail = _trapz1d(
            _as_float_array(density[K_grid < 0.9 * F]), _as_float_array(K_grid[K_grid < 0.9 * F])
        )
        right_tail = _trapz1d(
            _as_float_array(density[K_grid > 1.1 * F]), _as_float_array(K_grid[K_grid > 1.1 * F])
        )

        reg: Literal["LEFT_FAT", "RIGHT_FAT", "SYMMETRIC", "FAT_BOTH"] = "SYMMETRIC"
        if left_tail > 0.05 and right_tail > 0.05:
            reg = "FAT_BOTH"
        elif left_tail > 0.05:
            reg = "LEFT_FAT"
        elif right_tail > 0.05:
            reg = "RIGHT_FAT"

        return ImpliedPDFResult(
            strikes=K_grid.tolist(),
            density=density.tolist(),
            cdf=cdf.tolist(),
            forward=F,
            tte=T,
            risk_neutral_mean=mean_q,
            risk_neutral_std=std_q,
            skewness=skew_q,
            excess_kurtosis=kurt_q,
            left_tail_prob=left_tail,
            right_tail_prob=right_tail,
            tail_regime=reg,
        )


# ─────────────────────────────────────────────────────────────────────────────
# §3  MOTOR DE EXPOSICIÓN (GEX / VEX / CEX)
# ─────────────────────────────────────────────────────────────────────────────

# Convención alineada con documentación pública SpotGamma / VannaCharm:
#   GEX_K ≈ Γ_K × OI_K × 100 × S² × 0.01   (notional de hedging por movimiento ~1 % del spot)
#   VEX   = Σ_K (Vanna_K × OI_K × 100) en calls menos puts (sin factor S adicional)
#   CEX   = Σ_K (Charm_K × OI_K × 100)   (misma escala por contrato)
_GEX_ONE_PCT_MOVE = 0.01


class GEXMath:
    """Motor de cálculo de exposición de Dealers."""

    @staticmethod
    def net_gex(
        strikes: FloatArray,
        call_oi: FloatArray,
        put_oi: FloatArray,
        call_iv: FloatArray,
        put_iv: FloatArray,
        S: float,
        T: float,
        r: float = 0.04,
        multiplier: float = 100.0,
    ) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Calcula GEX neto y por strike (Perspectiva Dealer: Call+, Put-).

        Dollar gamma por convención SpotGamma-style: Γ × OI × contrato × S² × 0.01.
        """
        s = float(S)
        s2 = s * s
        scale = multiplier * s2 * _GEX_ONE_PCT_MOVE
        call_gex = BlackScholesPricer.gamma_vec(S, strikes, T, r, call_iv) * call_oi * scale
        put_gex = -BlackScholesPricer.gamma_vec(S, strikes, T, r, put_iv) * put_oi * scale
        return call_gex + put_gex, call_gex, put_gex

    @staticmethod
    def zero_gamma_level(strikes: FloatArray, net_gex: FloatArray) -> float:
        """Identifica el nivel de precio donde GEX cambia de signo (Interpolación Lineal)."""
        if len(strikes) == 0:
            return float("nan")
        order = np.argsort(strikes)
        k_s = strikes[order]
        g_s = net_gex[order]
        for i in range(len(g_s) - 1):
            if g_s[i] * g_s[i + 1] < 0:
                slope = g_s[i + 1] - g_s[i]
                return float(k_s[i] + (-g_s[i] / slope) * (k_s[i + 1] - k_s[i]))
        return float(k_s[np.argmin(np.abs(g_s))])

    @staticmethod
    def vanna_cex_exposure(
        strikes: FloatArray,
        call_oi: FloatArray,
        put_oi: FloatArray,
        call_iv: FloatArray,
        put_iv: FloatArray,
        S: float,
        T: float,
        multiplier: float = 100.0,
        r: float = 0.04,
    ) -> tuple[float, float]:
        """Calcula VEX (Vanna Exposure) y CEX (Charm Exposure) agregados."""
        v_c = BlackScholesPricer.vanna_vec(S, strikes, T, r, call_iv) * call_oi * multiplier
        v_p = BlackScholesPricer.vanna_vec(S, strikes, T, r, put_iv) * put_oi * multiplier
        vex = float(np.sum(v_c - v_p))

        c_c = BlackScholesPricer.charm_vec(S, strikes, T, r, call_iv) * call_oi * multiplier
        c_p = BlackScholesPricer.charm_vec(S, strikes, T, r, put_iv) * put_oi * multiplier
        cex = float(np.sum(c_c - c_p))

        return vex, cex

    @staticmethod
    def squeeze_probability(
        net_gex_total: float, vanna_exposure: float, gamma_flip_p: float, spot_to_zgl_pct: float
    ) -> float:
        """Heurística de probabilidad de Squeeze basada en GEX y Vanna condicional."""
        gex_score = 1.0 if net_gex_total < 0 else 0.0
        prox_score = max(0.0, 1.0 - abs(spot_to_zgl_pct) * 20.0)
        vanna_score = 1.0 if (net_gex_total < 0 and vanna_exposure > 0) else 0.0

        score = 0.4 * gex_score + 0.3 * prox_score + 0.2 * vanna_score + 0.1 * gamma_flip_p
        return float(np.clip(score, 0.0, 1.0))
