from __future__ import annotations
"""
backend/engine/metrics/vol_term_structure.py
Sector: Options / Volatility Term Structure Engine
[ARCH-1, PD-4]

Theoretical basis:
    Vasquez (2015) – "Equity Volatility Term Structures and the
    Cross-Section of Option Returns"
    → Documents that the SLOPE of the IV curve predicts future straddle returns.
    Flat curves or inversions indicate structural shifts and risk spikes.
"""


import logging

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.interpolate import CubicSpline


from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.vol_term_structure")

type FloatArray = npt.NDArray[np.float64]


class VolTermStructureReport(BaseModel):
    """
    Immutable report container for Volatility Term Structure analysis.
    """

    model_config = ConfigDict(frozen=True)

    short_tenor: int
    long_tenor: int
    iv_short: float
    iv_long: float
    ratio: float
    slope: float
    curvature: float
    regime: str
    inversion_alert: bool
    slope_zscore: float | None
    flat_warning: bool
    slope_bps: float
    summary_msg: str
    interpolated_ivs: dict[int, float]


class VolatilityTermStructureEngine:
    """
    Stateless engine for Volatility Term Structure analysis using pure NumPy and SciPy.
    """

    def __init__(
        self,
        standard_tenors: list[int] | None = None,
        short_tenor: int = 30,
        long_tenor: int = 90,
        zscore_window: int = 30,
        min_periods: int = 5,
    ) -> None:
        self.standard_tenors = standard_tenors or [7, 30, 60, 90, 180, 360]
        self.short_tenor = short_tenor
        self.long_tenor = long_tenor
        self.zscore_window = zscore_window
        self.min_periods = min_periods

        if self.short_tenor not in self.standard_tenors:
            logger.warning(
                "short_tenor %d not in standard_tenors. It will be interpolated.", self.short_tenor
            )
        if self.long_tenor not in self.standard_tenors:
            logger.warning(
                "long_tenor %d not in standard_tenors. It will be interpolated.", self.long_tenor
            )

    def analyze(
        self,
        atm_curve: FloatArray,
        historical_slopes: FloatArray | None = None,
    ) -> Result[VolTermStructureReport]:
        """
        Analyzes the volatility term structure for a single snapshot of ATM curve.

        Parameters
        ----------
        atm_curve : FloatArray
            2D NumPy array with shape (N, 2) where columns are:
            0 = days_to_expiry (DTE)
            1 = iv_atm (implied volatility at-the-money, decimal format)
        historical_slopes : FloatArray, optional
            1D NumPy array with historical slopes to compute Z-Score without state.

        Returns
        -------
        Result[VolTermStructureReport]
            The analysis report wrapped in a Result monad.
        """
        try:
            # 1. Validation
            if not isinstance(atm_curve, np.ndarray):
                return Result.failure(reason="atm_curve must be a numpy ndarray")

            if atm_curve.ndim != 2 or atm_curve.shape[1] != 2:
                return Result.failure(
                    reason=(
                        f"atm_curve must be a 2D array of shape (N, 2), "
                        f"got shape {atm_curve.shape}"
                    )
                )

            if len(atm_curve) == 0:
                return Result.failure(reason="atm_curve is empty")

            if np.any(np.isnan(atm_curve)):
                return Result.failure(reason="atm_curve contains NaN values")

            # Clean data: filter IVs between 3% (0.03) and 200% (2.0) and DTE >= 0
            # Vasquez (2015) filter: 3% - 200%
            mask = (atm_curve[:, 1] >= 0.03) & (atm_curve[:, 1] <= 2.00) & (atm_curve[:, 0] >= 0.0)
            cleaned_curve = atm_curve[mask]

            if len(cleaned_curve) == 0:
                return Result.failure(
                    reason="No valid points in atm_curve after applying filters (0.03 <= IV <= 2.00, DTE >= 0)"
                )

            # Aggregate duplicates (mean IV per unique DTE) to avoid SciPy CubicSpline errors
            unique_dtes, inverse_indices = np.unique(cleaned_curve[:, 0], return_inverse=True)
            unique_ivs = np.bincount(inverse_indices, weights=cleaned_curve[:, 1]) / np.bincount(
                inverse_indices
            )

            # Sort by DTE (np.unique already returns sorted unique values, so unique_dtes is sorted)
            # 2. Interpolation
            # cubic spline requires at least 4 unique points, fallback to linear interpolation
            if len(unique_dtes) >= 4:
                # CubicSpline with 'not-a-knot' boundary conditions
                cs = CubicSpline(unique_dtes, unique_ivs, bc_type="not-a-knot", extrapolate=True)
                interp_fn = cs
            else:
                # fallback to linear interpolation with constant extrapolation
                interp_fn = lambda x: np.interp(x, unique_dtes, unique_ivs)

            # 3. Calculate interpolated IV for standard tenors & target tenors
            interpolated_ivs: dict[int, float] = {}
            for tenor in self.standard_tenors:
                val = float(interp_fn(tenor))
                val = max(val, 0.01)  # cota inferior 1%
                interpolated_ivs[tenor] = round(val, 6)

            # Interpolate short and long tenors specifically if they are not in the standard list,
            # or just retrieve them.
            iv_short = float(interp_fn(self.short_tenor))
            iv_short = round(max(iv_short, 0.01), 6)
            iv_long = float(interp_fn(self.long_tenor))
            iv_long = round(max(iv_long, 0.01), 6)

            # 4. Compute metrics
            ratio = iv_short / iv_long
            slope = (iv_long - iv_short) / (self.long_tenor - self.short_tenor)

            # Find mid tenor to compute curvature (convexity)
            available = [t for t in self.standard_tenors if self.short_tenor < t < self.long_tenor]
            mid_tenor = available[len(available) // 2] if available else self.short_tenor
            iv_mid = float(interp_fn(mid_tenor))
            iv_mid = round(max(iv_mid, 0.01), 6)
            curvature = iv_mid - (iv_short + iv_long) / 2.0

            # 5. Compute Z-Score without storing state
            slope_zscore = None
            if historical_slopes is not None:
                if np.any(np.isnan(historical_slopes)):
                    return Result.failure(reason="historical_slopes contains NaN values")

                combined = np.append(historical_slopes, slope)
                if len(combined) > self.zscore_window:
                    combined = combined[-self.zscore_window :]

                if len(combined) >= self.min_periods:
                    finite_mask = np.isfinite(combined)
                    if np.sum(finite_mask) >= self.min_periods:
                        mean = float(np.mean(combined[finite_mask]))
                        std = float(np.std(combined[finite_mask], ddof=1))
                        if std == 0.0:
                            slope_zscore = 0.0
                        else:
                            slope_zscore = round((slope - mean) / std, 3)

            # 6. Regime classification
            # Umbral de flat: pendiente menor a 0.5 puntos base por día (0.0003)
            flat_threshold = 0.0003
            flat_warning = bool(abs(slope) < flat_threshold)
            inversion_alert = bool(ratio > 1.0)

            if inversion_alert:
                regime = "⚠️  PANIC / BACKWARDATION"
            elif flat_warning:
                regime = "⚡ FLAT / PRE-EXPANSION"
            else:
                regime = "✅ NORMAL / CONTANGO"

            # 7. Summary message
            slope_bps = slope * 10_000.0
            if inversion_alert:
                msg = (
                    f"🚨 ALERTA CRÍTICA — VOLATILITY INVERSION\n"
                    f"   IV corto ({self.short_tenor}d): {iv_short:.2%}  >  "
                    f"IV largo ({self.long_tenor}d): {iv_long:.2%}\n"
                    f"   Ratio: {ratio:.3f}  |  Slope: {slope_bps:.2f} bps/día\n"
                    f"   → Mercado en modo PÁNICO. Considerar estrategias "
                    f"     de short vega en plazos largos."
                )
            elif flat_warning:
                msg = (
                    f"⚡ ALERTA — CURVA PLANA (Precursor de Expansión)\n"
                    f"   Slope: {slope_bps:.2f} bps/día  (umbral: "
                    f"{flat_threshold*10000.0:.1f} bps/día)\n"
                    f"   → Históricamente el aplanamiento de la curva precede\n"
                    f"     expansiones de volatilidad en 2-4 semanas.\n"
                    f"   → Considerar compra de volatilidad (long straddle / vega)."
                )
            else:
                msg = (
                    f"✅ MERCADO NORMAL — CONTANGO\n"
                    f"   Slope: {slope_bps:.2f} bps/día  |  "
                    f"Ratio: {ratio:.3f}\n"
                    f"   → Estructura temporal saludable. "
                    f"     Vendedores de volatilidad en ventaja."
                )

            if slope_zscore is not None:
                if abs(slope_zscore) > 2.0:
                    msg += (
                        f"\n   ⚠️  Z-Score slope: {slope_zscore:.2f}σ — "
                        f"ANOMALÍA ESTADÍSTICA DETECTADA"
                    )
                else:
                    msg += f"\n   Z-Score slope: {slope_zscore:.2f}σ (rango normal)"

            report = VolTermStructureReport(
                short_tenor=self.short_tenor,
                long_tenor=self.long_tenor,
                iv_short=iv_short,
                iv_long=iv_long,
                ratio=round(ratio, 4),
                slope=slope,
                curvature=round(curvature, 6),
                regime=regime,
                inversion_alert=inversion_alert,
                slope_zscore=slope_zscore,
                flat_warning=flat_warning,
                slope_bps=round(slope_bps, 3),
                summary_msg=msg,
                interpolated_ivs=interpolated_ivs,
            )
            return Result.success(report)

        except Exception as e:
            logger.error("VolatilityTermStructureEngine analysis failed: %s", e)
            return Result.failure(reason=f"VolatilityTermStructureEngine analysis failed: {e}")


def analyze_vol_term_structure(
    atm_curve: FloatArray,
    historical_slopes: FloatArray | None = None,
    standard_tenors: list[int] | None = None,
    short_tenor: int = 30,
    long_tenor: int = 90,
    zscore_window: int = 30,
    min_periods: int = 5,
) -> Result[VolTermStructureReport]:
    """Stateless functional entry point for Volatility Term Structure analysis."""
    engine = VolatilityTermStructureEngine(
        standard_tenors=standard_tenors,
        short_tenor=short_tenor,
        long_tenor=long_tenor,
        zscore_window=zscore_window,
        min_periods=min_periods,
    )
    return engine.analyze(atm_curve, historical_slopes)
