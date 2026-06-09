"""
backend/engine/metrics/gamma_flip.py
Sector: Options / Gamma Flip Engine
[ARCH-1, PD-4]

Theoretical basis:
    Dealer Gamma Exposure and Gamma Flip Point analysis.
    Purely stateless, synchronous, offline, and pandas/matplotlib-free.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.optimize import brentq  # type: ignore[import-not-found, import-untyped]

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.gamma_flip")

type FloatArray = npt.NDArray[np.float64]

warnings.filterwarnings("ignore")


# ── Greeks Helper Functions ─────────────────────────────────────────────────────


def bs_gamma(
    spot: float,
    strike: FloatArray,
    tte: float,
    rate: float,
    sigma: float,
) -> FloatArray:
    """Calculates Black-Scholes Gamma vectorially for a spot and array of strikes."""
    if tte <= 0.0 or sigma <= 0.0 or spot <= 0.0:
        return np.zeros_like(strike, dtype=np.float64)

    # Filter out non-positive strikes to avoid division/log issues
    valid = strike > 0.0
    gamma = np.zeros_like(strike, dtype=np.float64)
    if not np.any(valid):
        return gamma

    k_val = strike[valid]
    d1 = (np.log(spot / k_val) + (rate + 0.5 * sigma**2) * tte) / (
        sigma * np.sqrt(tte)
    )
    pdf = np.exp(-0.5 * d1**2) / np.sqrt(2.0 * np.pi)
    res = pdf / (spot * sigma * np.sqrt(tte))
    gamma[valid] = res
    return gamma


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class VolatilityRegimeReport(BaseModel):
    """Regime and risk status under Gamma Exposure analysis."""

    model_config = ConfigDict(frozen=True)

    regime: str  # AT_FLIP, GAMMA_POSITIVE, GAMMA_NEGATIVE
    current_gamma: float
    flip_point: float | None
    distance_pct: float | None
    interpretation: str


class PutSensitivityReport(BaseModel):
    """Sensitivity analysis report for put option open interest shocks."""

    model_config = ConfigDict(frozen=True)

    shock_pct_applied: float
    flip_original: float | None
    flip_shocked: float | None
    delta_absolute: float | None
    delta_percent: float | None
    interpretation: str


class GammaFlipReport(BaseModel):
    """Comprehensive dealer gamma exposure and zero-crossing report."""

    model_config = ConfigDict(frozen=True)

    spot_price: float
    flip_point: float | None
    current_gamma: float
    volatility_regime: VolatilityRegimeReport
    sensitivity: PutSensitivityReport
    price_range: list[float]
    gamma_profile: list[float]


# ── Net Gamma Engine Helpers ─────────────────────────────────────────────────────


def _get_net_gamma(
    price: float,
    strikes: FloatArray,
    is_call: FloatArray,
    open_interest: FloatArray,
    tte: float,
    rate: float,
    sigma: float,
    contract_size: int,
) -> float:
    """Calculates Net Gamma of the options chain at a hypothetical spot price."""
    gammas = bs_gamma(price, strikes, tte, rate, sigma)
    direction = np.where(is_call == 1.0, 1.0, -1.0)
    return float(np.sum(gammas * open_interest * direction) * contract_size)


# ── Gamma Flip Engine ────────────────────────────────────────────────────────────


class GammaFlipEngine:
    """Stateless computation engine for dealer gamma flip analysis."""

    def __init__(self, contract_size: int = 100) -> None:
        self.contract_size = contract_size

    def analyze_gamma_flip(
        self,
        chain_data: FloatArray,
        spot_price: float,
        tte: float,
        rate: float,
        sigma: float,
        range_pct: float = 0.15,
        n_points: int = 500,
        shock_pct: float = 0.10,
    ) -> Result[GammaFlipReport]:
        """Calculates the dealer gamma profile, zero-crossing, regime, and shock sensitivity.

        Parameters
        ----------
        chain_data : 2D NumPy array of shape (N, 3) where columns represent:
                     [strike, is_call (1.0 or 0.0), open_interest]
        spot_price : Spot price of the underlying
        tte        : Time to expiration in years
        rate       : Risk-free interest rate
        sigma      : Underlying implied volatility
        range_pct  : Scanned spot price range fraction
        n_points   : Number of points on the net gamma profile curve
        shock_pct  : Shock fraction for Put OI sensitivity analysis

        Returns
        -------
        Result[GammaFlipReport]
        """
        if chain_data is None:
            return Result.failure(reason="chain_data must not be None")
        if chain_data.ndim != 2 or chain_data.shape[1] < 3:
            return Result.failure(
                reason=(
                    f"chain_data must be a 2D array with at least 3 columns. "
                    f"Got shape {chain_data.shape if chain_data is not None else 'None'}"
                )
            )
        if spot_price <= 0.0:
            return Result.failure(
                reason=f"spot price must be greater than zero. Got {spot_price}"
            )
        if tte <= 0.0:
            return Result.failure(
                reason=f"time to expiry must be greater than zero. Got {tte}"
            )
        if sigma <= 0.0:
            return Result.failure(
                reason=f"volatility sigma must be greater than zero. Got {sigma}"
            )

        try:
            strikes = chain_data[:, 0]
            is_call = chain_data[:, 1]
            open_interest = chain_data[:, 2]

            price_range = np.linspace(
                spot_price * (1.0 - range_pct),
                spot_price * (1.0 + range_pct),
                n_points,
            )

            # 1. Generate gamma profile curve
            gamma_profile = np.array(
                [
                    _get_net_gamma(
                        p,
                        strikes,
                        is_call,
                        open_interest,
                        tte,
                        rate,
                        sigma,
                        self.contract_size,
                    )
                    for p in price_range
                ],
                dtype=np.float64,
            )

            # 2. Find Flip Point (Zero-Crossing)
            sign_changes = np.where(np.diff(np.sign(gamma_profile)))[0]
            flip_point: float | None = None

            if len(sign_changes) > 0:
                idx = sign_changes[0]
                p_lo, p_hi = price_range[idx], price_range[idx + 1]

                try:
                    flip_point = float(
                        brentq(
                            lambda p: _get_net_gamma(
                                p,
                                strikes,
                                is_call,
                                open_interest,
                                tte,
                                rate,
                                sigma,
                                self.contract_size,
                            ),
                            p_lo,
                            p_hi,
                            xtol=1e-6,
                            maxiter=200,
                        )
                    )
                except ValueError:
                    g_lo, g_hi = gamma_profile[idx], gamma_profile[idx + 1]
                    flip_point = float(
                        p_lo - g_lo * (p_hi - p_lo) / (g_hi - g_lo)
                    )

            # 3. Volatility regime classification
            current_gamma = _get_net_gamma(
                spot_price,
                strikes,
                is_call,
                open_interest,
                tte,
                rate,
                sigma,
                self.contract_size,
            )
            tolerance = spot_price * 0.002

            if flip_point is not None and abs(spot_price - flip_point) < tolerance:
                regime = "AT_FLIP"
                interpretation = (
                    "🔴 ALERTA CRÍTICA: El precio está en el Gamma Flip Point. "
                    "Movimientos explosivos probables en cualquier dirección."
                )
            elif current_gamma > 0.0:
                regime = "GAMMA_POSITIVE"
                interpretation = (
                    "🟢 ZONA DE BAJA VOLATILIDAD: MM actúa como amortiguador. "
                    "El hedging del dealer compra caídas y vende alzas → precio en rango."
                )
            else:
                regime = "GAMMA_NEGATIVE"
                interpretation = (
                    "🔴 ZONA DE ALTA VOLATILIDAD: MM actúa como amplificador. "
                    "El hedging del dealer vende en caídas y compra en alzas → feedback loop."
                )

            distance_pct = (
                float((spot_price - flip_point) / flip_point * 100.0)
                if flip_point is not None
                else None
            )

            vol_regime_report = VolatilityRegimeReport(
                regime=regime,
                current_gamma=current_gamma,
                flip_point=flip_point,
                distance_pct=distance_pct,
                interpretation=interpretation,
            )

            # 4. Sensitivity Analysis (Shock on Put OI)
            open_interest_shocked = open_interest.copy()
            put_mask = is_call == 0.0
            open_interest_shocked[put_mask] = (
                open_interest[put_mask] * (1.0 + shock_pct)
            )

            gamma_profile_shocked = np.array(
                [
                    _get_net_gamma(
                        p,
                        strikes,
                        is_call,
                        open_interest_shocked,
                        tte,
                        rate,
                        sigma,
                        self.contract_size,
                    )
                    for p in price_range
                ],
                dtype=np.float64,
            )

            sign_changes_shocked = np.where(
                np.diff(np.sign(gamma_profile_shocked))
            )[0]
            flip_shocked: float | None = None

            if len(sign_changes_shocked) > 0:
                idx_s = sign_changes_shocked[0]
                ps_lo, ps_hi = price_range[idx_s], price_range[idx_s + 1]

                try:
                    flip_shocked = float(
                        brentq(
                            lambda p: _get_net_gamma(
                                p,
                                strikes,
                                is_call,
                                open_interest_shocked,
                                tte,
                                rate,
                                sigma,
                                self.contract_size,
                            ),
                            ps_lo,
                            ps_hi,
                            xtol=1e-6,
                            maxiter=200,
                        )
                    )
                except ValueError:
                    gs_lo, gs_hi = (
                        gamma_profile_shocked[idx_s],
                        gamma_profile_shocked[idx_s + 1],
                    )
                    flip_shocked = float(
                        ps_lo - gs_lo * (ps_hi - ps_lo) / (gs_hi - gs_lo)
                    )

            delta_abs: float | None = None
            delta_pct: float | None = None
            if flip_shocked is not None and flip_point is not None:
                delta_abs = float(flip_shocked - flip_point)
                if flip_point != 0.0:
                    delta_pct = float(delta_abs / flip_point * 100.0)

            if delta_abs is not None and delta_pct is not None:
                direction = "hacia arriba" if delta_abs > 0.0 else "hacia abajo"
                interp = (
                    f"Un aumento del {shock_pct * 100.0:.0f}% en el OI de Puts "
                    f"desplaza el Flip Point {direction} en {abs(delta_abs):.2f} pts "
                    f"({abs(delta_pct):.3f}%), aumentando la fragilidad "
                    "estructural del mercado."
                )
            elif delta_abs is not None:
                direction = "hacia arriba" if delta_abs > 0.0 else "hacia abajo"
                interp = (
                    f"Un aumento del {shock_pct * 100.0:.0f}% en el OI de Puts "
                    f"desplaza el Flip Point {direction} en {abs(delta_abs):.2f} pts."
                )
            else:
                interp = "No se pudo calcular el desplazamiento del Flip Point."

            sensitivity_report = PutSensitivityReport(
                shock_pct_applied=shock_pct * 100.0,
                flip_original=flip_point,
                flip_shocked=flip_shocked,
                delta_absolute=delta_abs,
                delta_percent=delta_pct,
                interpretation=interp,
            )

            return Result.success(
                GammaFlipReport(
                    spot_price=float(spot_price),
                    flip_point=flip_point,
                    current_gamma=current_gamma,
                    volatility_regime=vol_regime_report,
                    sensitivity=sensitivity_report,
                    price_range=price_range.tolist(),
                    gamma_profile=gamma_profile.tolist(),
                )
            )

        except Exception as e:
            logger.error(f"Gamma flip analysis failed: {e}")
            return Result.failure(reason=f"Gamma flip analysis failed: {e}")
