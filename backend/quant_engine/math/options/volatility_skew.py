"""
backend/engine/metrics/volatility_skew.py
Sector: Options / Volatility Skew Engine
[ARCH-1, PD-4]

Theoretical basis:
    Volatility smile/skew fitting (Polynomial or SABR) and scenario analysis.
    Purely stateless, synchronous, offline, and pandas-free.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.optimize import curve_fit  # type: ignore[import-not-found, import-untyped]

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.volatility_skew")

type FloatArray = npt.NDArray[np.float64]

warnings.filterwarnings("ignore", category=RuntimeWarning)


# ── Mathematical Smile Helper Functions ─────────────────────────────────────────


def polynomial_smile(x: FloatArray, a: float, b: float, c: float) -> FloatArray:
    """Calculates polynomial smile values: a * x^2 + b * x + c."""
    return a * x**2 + b * x + c


def sabr_approximation(
    strike: FloatArray,
    forward: float,
    alpha: float,
    beta: float,
    rho: float,
    nu: float,
    t: float = 1.0,
) -> FloatArray:
    """Calculates SABR implied volatility approximation."""
    k_arr = np.asarray(strike, dtype=np.float64)
    f_val = float(forward)
    eps = 1e-8
    atm_vol = (
        alpha
        / (f_val ** (1 - beta))
        * (
            1
            + (
                (1 - beta) ** 2 / 24 * alpha**2 / f_val ** (2 * (1 - beta))
                + rho * beta * nu * alpha / (4 * f_val ** (1 - beta))
                + (2 - 3 * rho**2) / 24 * nu**2
            )
            * t
        )
    )
    fk_val = f_val * k_arr
    log_fk = np.log(f_val / (k_arr + eps))
    fk_mid = (fk_val) ** ((1 - beta) / 2)
    z = nu / alpha * fk_mid * log_fk
    x_z = np.log((np.sqrt(1 - 2 * rho * z + z**2) + z - rho) / (1 - rho))
    ratio = np.where(np.abs(x_z) < eps, 1.0, z / (x_z + eps))
    a_val = alpha / (
        fk_mid * (1 + (1 - beta) ** 2 / 24 * log_fk**2 + (1 - beta) ** 4 / 1920 * log_fk**4)
    )
    b_val = (
        1
        + (
            (1 - beta) ** 2 / 24 * alpha**2 / fk_val ** (1 - beta)
            + rho * beta * nu * alpha / (4 * fk_mid)
            + (2 - 3 * rho**2) / 24 * nu**2
        )
        * t
    )
    iv_sabr = a_val * ratio * b_val
    atm_mask = np.abs(log_fk) < 1e-4
    iv_sabr = np.where(atm_mask, atm_vol, iv_sabr)
    return np.clip(iv_sabr, 1e-4, 5.0)


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class SkewMetrics(BaseModel):
    """Metrics describing volatility skew shape and warning levels."""

    model_config = ConfigDict(frozen=True)

    slope_25d: float
    convexity: float
    iv_25d_put: float
    iv_25d_call: float
    iv_atm: float
    iv_10d_put: float
    iv_10d_call: float
    regime: str
    tail_risk_alert: bool
    alert_message: str
    poly_coeffs: list[float]


class MarketPoint(BaseModel):
    """Raw market data point of strike, iv, type, and delta."""

    model_config = ConfigDict(frozen=True)

    strike: float
    iv_pct: float
    option_type: str
    delta: float


class FittedPoint(BaseModel):
    """Fitted smile curve strike and iv value."""

    model_config = ConfigDict(frozen=True)

    strike: float
    iv_fitted_pct: float


class CurvaturePoint(BaseModel):
    """Curvature normalized metric for a specific strike."""

    model_config = ConfigDict(frozen=True)

    strike: float
    curvature_norm: float


class ScenarioReport(BaseModel):
    """Scenario stress test outcome."""

    model_config = ConfigDict(frozen=True)

    shock_pct: float
    stressed_strike: float
    iv_stressed: float
    iv_atm: float
    iv_premium: float
    iv_ratio: float | None


class VolatilitySkewReport(BaseModel):
    """Fitted volatility skew and smile report."""

    model_config = ConfigDict(frozen=True)

    spot: float
    fit_model: str
    metrics: SkewMetrics
    market_points: list[MarketPoint]
    fitted_curve: list[FittedPoint]
    curvature: list[CurvaturePoint]
    scenarios: list[ScenarioReport]


# ── Helper Extraction & Skew Prediction Functions ────────────────────────────────


def build_curve_data(options_chain: FloatArray, spot: float) -> FloatArray:
    """Constructs OTM-composite curve for fitting.

    Returns a 2D array of [strike, iv] sorted by strike and deduplicated.
    """
    strikes = options_chain[:, 0]
    ivs = options_chain[:, 1]
    is_calls = options_chain[:, 2]

    unique_strikes = np.unique(strikes)
    curve_ivs = np.zeros(len(unique_strikes), dtype=np.float64)

    for i, k in enumerate(unique_strikes):
        mask = strikes == k
        matching_ivs = ivs[mask]
        matching_is_calls = is_calls[mask]

        if k < spot * 0.999:
            # Prefer PUT (is_call == 0.0)
            put_mask = matching_is_calls == 0.0
            if np.any(put_mask):
                curve_ivs[i] = matching_ivs[put_mask][0]
            else:
                curve_ivs[i] = matching_ivs[0] if len(matching_ivs) > 0 else np.nan
        elif k > spot * 1.001:
            # Prefer CALL (is_call == 1.0)
            call_mask = matching_is_calls == 1.0
            if np.any(call_mask):
                curve_ivs[i] = matching_ivs[call_mask][0]
            else:
                curve_ivs[i] = matching_ivs[0] if len(matching_ivs) > 0 else np.nan
        else:
            # Average CALL and PUT
            if len(matching_ivs) > 0:
                curve_ivs[i] = np.nanmean(matching_ivs)
            else:
                curve_ivs[i] = np.nan

    valid = ~np.isnan(curve_ivs)
    return np.column_stack((unique_strikes[valid], curve_ivs[valid]))


def predict_iv(
    strikes: FloatArray,
    fit_model: str,
    fitted_params: FloatArray,
    spot: float,
    rate: float,
    tte: float,
) -> FloatArray:
    """Predicts IV on strike grid using fitted parameters."""
    strikes = np.asarray(strikes, dtype=np.float64)
    if fit_model == "polynomial":
        log_m = np.log(strikes / spot)
        a, b, c = fitted_params
        return polynomial_smile(log_m, a, b, c)
    if fit_model == "sabr":
        forward = spot * np.exp(rate * tte)
        alpha, beta, rho, nu = fitted_params
        return sabr_approximation(strikes, forward, alpha, beta, rho, nu, t=tte)
    return np.full_like(strikes, np.nan)


def _extract_iv_by_delta(
    options_chain: FloatArray, is_call_val: float, target_delta: float
) -> float:
    """Extracts IV for the option nearest to target_delta of a specific type (CALL/PUT)."""
    mask = options_chain[:, 2] == is_call_val
    if not np.any(mask):
        return float("nan")

    subset = options_chain[mask]
    diff = np.abs(subset[:, 3] - target_delta)
    min_idx = np.argmin(diff)
    return float(subset[min_idx, 1])


def _get_iv_atm(
    options_chain: FloatArray,
    spot: float,
    fit_model: str,
    fitted_params: FloatArray | None,
    rate: float,
    tte: float,
) -> float:
    """Extracts ATM IV by evaluating the model or finding the closest market strike."""
    if fitted_params is not None:
        strikes = np.array([spot], dtype=np.float64)
        return float(predict_iv(strikes, fit_model, fitted_params, spot, rate, tte)[0])

    dist = np.abs(options_chain[:, 0] - spot)
    min_idx = np.argmin(dist)
    return float(options_chain[min_idx, 1])


def _local_slope(
    strike: float,
    fit_model: str,
    fitted_params: FloatArray,
    spot: float,
    rate: float,
    tte: float,
) -> float:
    """Calculates numeric local slope dIV/dK at a strike."""
    h = strike * 0.001
    strikes = np.array([strike - h, strike + h], dtype=np.float64)
    ivs = predict_iv(strikes, fit_model, fitted_params, spot, rate, tte)
    return float((ivs[1] - ivs[0]) / (2.0 * h))


def _local_curvature(
    strike: float,
    fit_model: str,
    fitted_params: FloatArray,
    spot: float,
    rate: float,
    tte: float,
) -> float:
    """Calculates numeric local curvature d2IV/dK2 at a strike."""
    h = strike * 0.001
    strikes = np.array([strike - h, strike, strike + h], dtype=np.float64)
    ivs = predict_iv(strikes, fit_model, fitted_params, spot, rate, tte)
    return float((ivs[2] - 2.0 * ivs[1] + ivs[0]) / h**2)


def _scenario_analysis(
    shock_pct: float,
    fit_model: str,
    fitted_params: FloatArray,
    spot: float,
    rate: float,
    tte: float,
    iv_atm: float,
) -> ScenarioReport:
    """Simulates spot price stress shock and returns output scenario parameters."""
    stressed_strike = spot * (1.0 + shock_pct)
    strikes = np.array([stressed_strike], dtype=np.float64)
    iv_stressed = float(predict_iv(strikes, fit_model, fitted_params, spot, rate, tte)[0])
    ratio = iv_stressed / iv_atm if iv_atm > 0.0 else float("nan")

    ratio_val = float(ratio)
    iv_ratio = None if np.isnan(ratio_val) else ratio_val

    return ScenarioReport(
        shock_pct=shock_pct,
        stressed_strike=stressed_strike,
        iv_stressed=iv_stressed,
        iv_atm=iv_atm,
        iv_premium=iv_stressed - iv_atm,
        iv_ratio=iv_ratio,
    )


# ── Volatility Skew Engine ───────────────────────────────────────────────────────


class VolatilitySkewEngine:
    """Stateless computation engine for volatility skew analysis."""

    CRASH_RISK_SLOPE_THRESHOLD = 0.08
    CRASH_RISK_CONVEXITY_THRESHOLD = 0.03
    BULLISH_SLOPE_THRESHOLD = -0.02
    TAIL_RISK_MULTIPLIER = 1.20

    def __init__(self, fit_model: str = "polynomial") -> None:
        self.fit_model = fit_model

    def _classify_regime(self, slope_25d: float, convexity: float) -> str:
        """Determines volatility regime based on slope and convexity."""
        crash_slope = slope_25d >= self.CRASH_RISK_SLOPE_THRESHOLD
        crash_conv = convexity >= self.CRASH_RISK_CONVEXITY_THRESHOLD
        bullish = slope_25d <= self.BULLISH_SLOPE_THRESHOLD
        if crash_slope and crash_conv:
            return "Crash Risk"
        if bullish:
            return "Bullish Skew"
        return "Normal Skew"

    def analyze_volatility_skew(
        self,
        options_chain: FloatArray,
        spot: float,
        rate: float,
        tte: float,
        convexity_history: list[float] | None = None,
    ) -> Result[VolatilitySkewReport]:
        """Calculates tail risk skew indicators and returns a volatility skew report.

        Parameters
        ----------
        options_chain : 2D NumPy array of dimensions (N, >=4) containing:
                        [strike, iv, is_call, delta]
        spot          : Spot price of the underlying
        rate          : Risk-free interest rate
        tte           : Time to expiration in years
        convexity_history : Historical convexity scores list for tail risk alert

        Returns
        -------
        Result[VolatilitySkewReport]
        """
        if options_chain is None:
            return Result.failure(reason="options_chain must not be None")
        if options_chain.ndim != 2 or options_chain.shape[1] < 4:
            return Result.failure(
                reason=(
                    f"options_chain must be a 2D array with at least 4 columns. "
                    f"Got shape {options_chain.shape if options_chain is not None else 'None'}"
                )
            )
        if spot <= 0.0:
            return Result.failure(reason=f"spot price must be greater than zero. Got {spot}")
        if tte <= 0.0:
            return Result.failure(reason=f"time to expiry must be greater than zero. Got {tte}")

        try:
            # 1. Construct OTM-composite curve data
            curve_data = build_curve_data(options_chain, spot)
            if len(curve_data) < 5:
                return Result.failure(
                    reason=(
                        f"Insufficient smile points for fitting. "
                        f"Need at least 5 unique strikes, got {len(curve_data)}"
                    )
                )

            curve_strikes = curve_data[:, 0]
            curve_ivs = curve_data[:, 1]

            # 2. Fit smile model (SABR or Polynomial)
            fit_model_used = self.fit_model
            fitted_params = None

            if fit_model_used == "sabr":
                forward = spot * np.exp(rate * tte)

                def sabr_wrapper(
                    k_arr: FloatArray,
                    alpha: float,
                    beta: float,
                    rho: float,
                    nu: float,
                ) -> FloatArray:
                    return sabr_approximation(k_arr, forward, alpha, beta, rho, nu, t=tte)

                try:
                    popt, _ = curve_fit(
                        sabr_wrapper,
                        curve_strikes,
                        curve_ivs,
                        p0=[0.3, 0.5, -0.3, 0.4],
                        bounds=(
                            [0.001, 0.0, -0.999, 0.001],
                            [5.0, 1.0, 0.999, 5.0],
                        ),
                        maxfev=50000,
                    )
                    fitted_params = popt
                except Exception:
                    logger.warning("SABR fit failed to converge. Falling back to polynomial model.")
                    fit_model_used = "polynomial"

            if fit_model_used == "polynomial":
                log_moneyness = np.log(curve_strikes / spot)
                try:
                    popt, _ = curve_fit(
                        polynomial_smile,
                        log_moneyness,
                        curve_ivs,
                        p0=[0.5, -0.1, float(np.mean(curve_ivs))],
                        maxfev=10000,
                    )
                    fitted_params = popt
                except Exception:
                    logger.warning(
                        "Polynomial curve_fit failed to converge. Falling back to polyfit."
                    )
                    p_coeffs = np.polyfit(log_moneyness, curve_ivs, 2)
                    fitted_params = p_coeffs

            if fitted_params is None:
                return Result.failure(
                    reason="Could not obtain fitted parameters for the smile curve"
                )

            # 3. Extract key delta IV values
            iv_25d_put = _extract_iv_by_delta(options_chain, 0.0, -0.25)
            iv_25d_call = _extract_iv_by_delta(options_chain, 1.0, 0.25)
            iv_10d_put = _extract_iv_by_delta(options_chain, 0.0, -0.10)
            iv_10d_call = _extract_iv_by_delta(options_chain, 1.0, 0.10)
            iv_atm = _get_iv_atm(
                options_chain,
                spot,
                fit_model_used,
                fitted_params,
                rate,
                tte,
            )

            # Convert NaNs to 0.0 where applicable (except ATM which must be valid)
            if np.isnan(iv_25d_put) or np.isnan(iv_25d_call) or np.isnan(iv_atm):
                return Result.failure(
                    reason="Could not extract 25D put, 25D call, or ATM IV from options_chain"
                )

            iv_10d_put_clean = 0.0 if np.isnan(iv_10d_put) else iv_10d_put
            iv_10d_call_clean = 0.0 if np.isnan(iv_10d_call) else iv_10d_call

            # 4. Calculate skew metrics
            slope_25d = iv_25d_put - iv_25d_call
            convexity = (iv_25d_put + iv_25d_call) / 2.0 - iv_atm

            # 5. Tail risk alert evaluation
            tail_risk_alert = False
            alert_message = ""
            history = list(convexity_history) if convexity_history is not None else []
            history.append(convexity)

            if len(history) >= 15:
                recent_history = history[-15:]
                mean_conv = float(np.mean(recent_history))
                threshold = mean_conv * self.TAIL_RISK_MULTIPLIER
                if convexity > threshold and mean_conv > 0.0:
                    tail_risk_alert = True
                    pct_above = (convexity / mean_conv - 1.0) * 100.0
                    alert_message = (
                        f"TAIL RISK: convexidad {convexity:.4f} es {pct_above:.1f}% "
                        f"por encima de la media 15d ({mean_conv:.4f})."
                    )
            else:
                alert_message = (
                    f"Historial insuficiente ({len(history)}/15) para alerta de tail risk."
                )

            regime = self._classify_regime(slope_25d, convexity)

            poly_coeffs = (
                fitted_params.tolist() if fit_model_used == "polynomial" else [0.0, 0.0, 0.0]
            )

            # 6. Generate fitted coordinates and curvature
            k_min = float(curve_strikes.min()) * 0.95
            k_max = float(curve_strikes.max()) * 1.05
            strike_grid = np.linspace(k_min, k_max, 160)
            iv_fitted = predict_iv(
                strike_grid,
                fit_model_used,
                fitted_params,
                spot,
                rate,
                tte,
            )

            idx = np.linspace(0, len(strike_grid) - 1, num=min(100, len(strike_grid)), dtype=int)
            curv_k = strike_grid[idx]
            curv_raw = np.array(
                [
                    _local_curvature(
                        k,
                        fit_model_used,
                        fitted_params,
                        spot,
                        rate,
                        tte,
                    )
                    for k in curv_k
                ],
                dtype=np.float64,
            )
            mx = float(np.max(np.abs(curv_raw)) + 1e-9)
            curv_norm = (curv_raw / mx).tolist()

            fitted_curve = [
                FittedPoint(strike=float(k), iv_fitted_pct=float(iv) * 100.0)
                for k, iv in zip(strike_grid.tolist(), iv_fitted.tolist(), strict=True)
            ]

            curvature = [
                CurvaturePoint(strike=float(k), curvature_norm=float(v))
                for k, v in zip(curv_k.tolist(), curv_norm, strict=True)
            ]

            # 7. Stress scenarios
            scenarios = [
                _scenario_analysis(
                    shock,
                    fit_model_used,
                    fitted_params,
                    spot,
                    rate,
                    tte,
                    iv_atm,
                )
                for shock in (-0.05, -0.1, -0.15, -0.2)
            ]

            # 8. Observed market points
            market_points = []
            for i in range(options_chain.shape[0]):
                strike = float(options_chain[i, 0])
                iv = float(options_chain[i, 1])
                is_call_val = float(options_chain[i, 2])
                delta = float(options_chain[i, 3])

                delta_clean = 0.0 if np.isnan(delta) else delta
                iv_clean = 0.0 if np.isnan(iv) else iv

                ot_long = "call" if is_call_val == 1.0 else "put"
                market_points.append(
                    MarketPoint(
                        strike=strike,
                        iv_pct=iv_clean * 100.0,
                        option_type=ot_long,
                        delta=delta_clean,
                    )
                )

            metrics_report = SkewMetrics(
                slope_25d=slope_25d,
                convexity=convexity,
                iv_25d_put=iv_25d_put,
                iv_25d_call=iv_25d_call,
                iv_atm=iv_atm,
                iv_10d_put=iv_10d_put_clean,
                iv_10d_call=iv_10d_call_clean,
                regime=regime,
                tail_risk_alert=tail_risk_alert,
                alert_message=alert_message,
                poly_coeffs=poly_coeffs,
            )

            return Result.success(
                VolatilitySkewReport(
                    spot=float(spot),
                    fit_model=fit_model_used,
                    metrics=metrics_report,
                    market_points=market_points,
                    fitted_curve=fitted_curve,
                    curvature=curvature,
                    scenarios=scenarios,
                )
            )

        except Exception as e:
            logger.error(f"Volatility skew analysis failed: {e}")
            return Result.failure(reason=f"Volatility skew analysis failed: {e}")
