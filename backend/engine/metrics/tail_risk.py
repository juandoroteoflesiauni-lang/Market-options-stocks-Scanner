"""
backend/engine/metrics/tail_risk.py
Sector: Options / Tail Risk Engine
[ARCH-1, PD-4]

Theoretical basis:
    Tail risk analysis from volatility smile geometry using cubic splines.
    Purely stateless, synchronous, offline, and pandas-free.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.interpolate import CubicSpline  # type: ignore[import-not-found, import-untyped]
from scipy.stats import percentileofscore  # type: ignore[import-not-found, import-untyped]

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.tail_risk")

type FloatArray = npt.NDArray[np.float64]

# Soft import — engine remains functional if RND engine is absent
try:
    from backend.engine.metrics.risk_neutral_density import (
        get_risk_neutral_density as _get_rnd,
    )
    _RND_AVAILABLE = True
except ImportError:
    try:
        from backend.layer_3_specialists.ia_probabilistico.engines import (
            risk_neutral_density_engine as _rnde,
        )

        _get_rnd = _rnde.get_risk_neutral_density
        _RND_AVAILABLE = True
    except ImportError:
        _RND_AVAILABLE = False

# Reference 25Δ butterfly distribution for equity surfaces (prior for percentiles)
_REF_CONVEXITIES = np.array(
    [
        0.002,
        0.003,
        0.004,
        0.005,
        0.006,
        0.007,
        0.008,
        0.009,
        0.010,
        0.012,
        0.014,
        0.016,
        0.018,
        0.020,
        0.022,
        0.025,
        0.028,
        0.032,
        0.038,
        0.045,
    ],
    dtype=np.float64,
)


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class SmileMetrics(BaseModel):
    """Metrics describing the shape of the volatility smile."""

    model_config = ConfigDict(frozen=True)

    skew_25d: float
    convexity_25d: float
    iv_put_25d: float
    iv_call_25d: float
    iv_atm: float
    min_iv_strike: float
    smile_skewness_pct: float
    as_of_iso: str


class TailRiskAlert(BaseModel):
    """Alert level and interpretation of tail risk metrics."""

    model_config = ConfigDict(frozen=True)

    level: str
    convexity_percentile: float
    skew_regime: str
    message: str
    metrics: SmileMetrics


class RiskReversalReport(BaseModel):
    """Report detailing risk reversal parameters and interpretation."""

    model_config = ConfigDict(frozen=True)

    direction: str
    signal_strength: str
    skew_vol_pts: float
    iv_put_25d_pct: float
    iv_call_25d_pct: float
    iv_atm_pct: float
    convexity_vol_pts: float
    min_iv_strike: float
    smile_skewness_pct: float
    interpretation: str


class ObservedPoint(BaseModel):
    """A raw observed option data point for visualization."""

    model_config = ConfigDict(frozen=True)

    strike: float
    iv_pct: float
    is_call: float
    delta: float


class SplinePoint(BaseModel):
    """A point on the interpolated volatility smile line."""

    model_config = ConfigDict(frozen=True)

    strike: float
    iv_pct: float


class CurvaturePoint(BaseModel):
    """A point showing the curvature (second derivative) of the smile."""

    model_config = ConfigDict(frozen=True)

    strike: float
    curvature: float


class TailRiskReport(BaseModel):
    """Comprehensive stateless tail risk analysis report."""

    model_config = ConfigDict(frozen=True)

    spot: float
    metrics: SmileMetrics
    alert: TailRiskAlert
    risk_reversal: RiskReversalReport
    q_skewness: float | None
    q_kurtosis: float | None
    implied_skew_signal: float
    tail_asymmetry: float | None
    bimodal_alert: bool
    directional_signal: float
    observed: list[ObservedPoint]
    smile_spline: list[SplinePoint]
    curvature: list[CurvaturePoint]


# ── Extraction & Soft Dependency Helpers ─────────────────────────────────────────


def _extract_iv_by_delta(
    options_chain: FloatArray, is_call_val: float, target_delta: float
) -> float:
    """Extracts IV for the option nearest to target_delta of a specific type (CALL/PUT)."""
    mask = options_chain[:, 2] == is_call_val
    if not np.any(mask):
        return float("nan")

    subset = options_chain[mask]
    delta_abs = np.abs(subset[:, 3])
    diff = np.abs(delta_abs - target_delta)
    min_idx = np.argmin(diff)
    return float(subset[min_idx, 1])


def _extract_atm_iv(options_chain: FloatArray, spot: float) -> float:
    """Extracts ATM IV by averaging CALL and PUT IVs closest to spot strike."""
    ivs_to_average = []
    for is_call_val in (1.0, 0.0):
        mask = (options_chain[:, 2] == is_call_val) & (~np.isnan(options_chain[:, 1]))
        if np.any(mask):
            subset = options_chain[mask]
            dist_spot = np.abs(subset[:, 0] - spot)
            min_idx = np.argmin(dist_spot)
            ivs_to_average.append(subset[min_idx, 1])
    if ivs_to_average:
        return float(np.nanmean(ivs_to_average))
    return float("nan")


def _extract_implied_moments(
    options_chain: FloatArray,
    spot: float,
    rate: float,
    tte: float,
) -> dict[str, Any] | None:
    """Calls soft-dependency RND engine and returns relevant moments."""
    if not _RND_AVAILABLE:
        return None
    try:
        # Extract strike (col 0) and call_price (col 5)
        # Filter out rows where either strike or call_price is NaN
        valid_mask = ~np.isnan(options_chain[:, 0]) & ~np.isnan(options_chain[:, 5])
        valid_data = options_chain[valid_mask]
        if len(valid_data) == 0:
            return None

        # Sort by strike
        sort_idx = np.argsort(valid_data[:, 0])
        sorted_data = valid_data[sort_idx]

        # Deduplicate strike (keeping the first occurrence)
        unique_strikes, unique_indices = np.unique(sorted_data[:, 0], return_index=True)
        unique_call_prices = sorted_data[unique_indices, 5]

        # Construct 2D array of [strike, call_price]
        rnd_input = np.column_stack((unique_strikes, unique_call_prices))

        result = _get_rnd(rnd_input, spot, rate, tte)
        if result is None or "error_msg" in result:
            return None
        return {
            "q_skewness": result.get("q_skewness"),
            "q_kurtosis": result.get("q_kurtosis"),
            "modal_price": result.get("modal_price"),
            "is_bimodal": result.get("is_bimodal", False),
        }
    except Exception:
        return None


def _analyze_risk_reversal(metrics: SmileMetrics) -> RiskReversalReport:
    """Calculates risk reversal details and interpretation."""
    skew = metrics.skew_25d
    skew_pct = skew * 100.0
    abs_skew = abs(skew_pct)
    if abs_skew < 2.0:
        signal_strength = "DEBIL"
    elif abs_skew < 5.0:
        signal_strength = "MODERADA"
    elif abs_skew < 10.0:
        signal_strength = "FUERTE"
    else:
        signal_strength = "EXTREMA"
    direction = "BAJISTA" if skew > 0 else "ALCISTA"
    interpretation = {
        "BAJISTA": (
            f"Puts 25Δ ~{abs_skew:.1f} vol pts por encima de calls 25Δ. "
            "Demanda de protección bajista / hedging institucional."
        ),
        "ALCISTA": (
            f"Calls 25Δ ~{abs_skew:.1f} vol pts por encima de puts 25Δ. "
            "Demanda alcista o cobertura de shorts."
        ),
    }
    return RiskReversalReport(
        direction=direction,
        signal_strength=signal_strength,
        skew_vol_pts=round(skew_pct, 4),
        iv_put_25d_pct=round(metrics.iv_put_25d * 100.0, 2),
        iv_call_25d_pct=round(metrics.iv_call_25d * 100.0, 2),
        iv_atm_pct=round(metrics.iv_atm * 100.0, 2),
        convexity_vol_pts=round(metrics.convexity_25d * 100.0, 4),
        min_iv_strike=round(metrics.min_iv_strike, 4),
        smile_skewness_pct=round(metrics.smile_skewness_pct * 100.0, 2),
        interpretation=interpretation[direction],
    )


# ── Tail Risk Engine ─────────────────────────────────────────────────────────────


class TailRiskEngine:
    """Stateless computation engine for tail risk analysis."""

    SKEW_NEUTRAL_BAND = (-0.02, 0.02)
    SKEW_BEARISH_WARN = 0.05
    SKEW_CRASH_HEDGE = 0.10
    CATASTROPHE_PERCENTILE = 90.0
    ELEVATED_PERCENTILE = 70.0

    def __init__(self, spline_smoothing: bool = True) -> None:
        self.spline_smoothing = spline_smoothing

    def _find_smile_minimum(
        self, cs: CubicSpline, k_min: float, k_max: float, n_points: int = 500
    ) -> float:
        """Finds the strike that minimizes the implied volatility smile."""
        k_grid = np.linspace(k_min, k_max, n_points)
        iv_grid = cs(k_grid)
        return float(k_grid[np.argmin(iv_grid)])

    def assess_tail_risk(self, metrics: SmileMetrics) -> TailRiskAlert:
        """Determines alert level and risk regime based on convexity percentile and skew."""
        conv_pct = float(percentileofscore(_REF_CONVEXITIES, metrics.convexity_25d, kind="weak"))

        s = metrics.skew_25d
        if s >= self.SKEW_CRASH_HEDGE:
            skew_regime = "CRASH_HEDGE"
        elif s >= self.SKEW_BEARISH_WARN:
            skew_regime = "BEARISH_REVERSAL"
        elif s <= self.SKEW_NEUTRAL_BAND[0]:
            skew_regime = "BULLISH_REVERSAL"
        else:
            skew_regime = "NEUTRAL"

        if conv_pct >= self.CATASTROPHE_PERCENTILE:
            level = "CATASTROPHE_IMMINENT"
            message = (
                f"ALERTA MÁXIMA: convexidad en percentil ~{conv_pct:.0f} vs "
                f"referencia histórica típica. Skew 25Δ: {metrics.skew_25d * 100:.2f} vol pts. "
                f"Régimen: {skew_regime}. "
                "Considerar reducción de riesgo direccional y vega defensiva."
            )
        elif conv_pct >= self.ELEVATED_PERCENTILE or skew_regime == "CRASH_HEDGE":
            level = "ELEVATED"
            message = (
                f"Riesgo elevado: convexidad ~percentil {conv_pct:.0f}. "
                f"Skew 25Δ: {metrics.skew_25d * 100:.2f} vol pts ({skew_regime}). "
                f"Monitorear flujos y gamma."
            )
        else:
            level = "NORMAL"
            message = (
                f"Régimen estable vs referencia: convexidad ~percentil {conv_pct:.0f}. "
                f"Skew 25Δ: {metrics.skew_25d * 100:.2f} vol pts ({skew_regime})."
            )

        return TailRiskAlert(
            level=level,
            convexity_percentile=conv_pct,
            skew_regime=skew_regime,
            message=message,
            metrics=metrics,
        )

    def analyze_tail_risk(
        self,
        options_chain: FloatArray,
        spot: float,
        rate: float,
        tte: float,
        as_of_iso: str | None = None,
    ) -> Result[TailRiskReport]:
        """Calculates tail risk indicators, interpolates smile skew, and returns a unified report.

        Parameters
        ----------
        options_chain : 2D NumPy array with columns representing:
                        [strike, iv, is_call, delta, spot_price, call_price, put_price]
        spot          : Current spot price
        rate          : Annualised risk-free rate
        tte           : Time to expiry in years
        as_of_iso     : ISO timestamp override

        Returns
        -------
        Result[TailRiskReport]
        """
        if options_chain is None:
            return Result.failure(reason="options_chain must not be None")
        if options_chain.ndim != 2 or options_chain.shape[1] < 7:
            return Result.failure(
                reason=(
                    f"options_chain must be a 2D array with at least 7 columns. "
                    f"Got shape {options_chain.shape if options_chain is not None else 'None'}"
                )
            )
        if spot <= 0.0:
            return Result.failure(reason=f"spot price must be greater than zero. Got {spot}")

        try:
            # 1. Validate number of unique strikes in options_chain early
            valid_mask = ~np.isnan(options_chain[:, 0]) & ~np.isnan(options_chain[:, 1])
            valid_data = options_chain[valid_mask]
            if len(valid_data) == 0:
                return Result.failure(
                    reason="No valid non-NaN strike/iv pairs found in options_chain"
                )

            # Sort by strike
            sort_idx = np.argsort(valid_data[:, 0])
            sorted_data = valid_data[sort_idx]

            # Get unique strikes and their first occurrence IVs
            unique_strikes, unique_indices = np.unique(sorted_data[:, 0], return_index=True)
            unique_ivs = sorted_data[unique_indices, 1]

            if len(unique_strikes) < 4:
                return Result.failure(
                    reason=(
                        f"At least 4 unique strikes are required for spline interpolation. "
                        f"Got {len(unique_strikes)}"
                    )
                )

            # 2. Extract 25d put/call IVs and ATM IV
            iv_put_25d = _extract_iv_by_delta(options_chain, 0.0, 0.25)
            iv_call_25d = _extract_iv_by_delta(options_chain, 1.0, 0.25)
            iv_atm = _extract_atm_iv(options_chain, spot)

            if np.isnan(iv_put_25d) or np.isnan(iv_call_25d) or np.isnan(iv_atm):
                return Result.failure(
                    reason="Could not extract 25D put, 25D call, or ATM IV from options_chain"
                )

            # 3. Compute basic metrics
            skew_25d = iv_put_25d - iv_call_25d
            convexity_25d = (iv_put_25d + iv_call_25d) / 2.0 - iv_atm

            cs = CubicSpline(unique_strikes, unique_ivs, bc_type="not-a-knot")
            min_iv_strike = self._find_smile_minimum(
                cs, float(unique_strikes.min()), float(unique_strikes.max())
            )
            smile_skewness_pct = (min_iv_strike - spot) / spot if spot > 0.0 else 0.0

            ts = as_of_iso or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            metrics = SmileMetrics(
                skew_25d=skew_25d,
                convexity_25d=convexity_25d,
                iv_put_25d=iv_put_25d,
                iv_call_25d=iv_call_25d,
                iv_atm=iv_atm,
                min_iv_strike=min_iv_strike,
                smile_skewness_pct=smile_skewness_pct,
                as_of_iso=ts,
            )

            alert = self.assess_tail_risk(metrics)
            rr = _analyze_risk_reversal(metrics)

            # 4. Implied skew signal baseline
            baseline_skew_signal = float(np.clip(-skew_25d / 0.10, -1.0, 1.0))

            q_skewness: float | None = None
            q_kurtosis: float | None = None
            implied_skew_signal: float = baseline_skew_signal
            bimodal_alert: bool = False

            # Check if call prices are present and not all NaN in column 5
            if not np.all(np.isnan(options_chain[:, 5])):
                moments = _extract_implied_moments(options_chain, spot, rate, tte)
                if moments is not None:
                    q_skewness = moments["q_skewness"]
                    q_kurtosis = moments["q_kurtosis"]
                    bimodal_alert = bool(moments["is_bimodal"])
                    if q_skewness is not None:
                        rnd_signal = float(np.clip(q_skewness / 2.0, -1.0, 1.0))
                        implied_skew_signal = float(
                            np.clip(0.6 * rnd_signal + 0.4 * baseline_skew_signal, -1.0, 1.0)
                        )

            # 5. Tail asymmetry: OTM put premium / OTM call premium
            tail_asymmetry: float | None = None
            try:
                call_mask = (
                    (options_chain[:, 2] == 1.0)
                    & (options_chain[:, 0] > spot)
                    & (~np.isnan(options_chain[:, 5]))
                )
                put_mask = (
                    (options_chain[:, 2] == 0.0)
                    & (options_chain[:, 0] < spot)
                    & (~np.isnan(options_chain[:, 6]))
                )

                otm_calls = options_chain[call_mask, 5]
                otm_puts = options_chain[put_mask, 6]

                put_prem = float(np.sum(otm_puts))
                call_prem = float(np.sum(otm_calls))
                if call_prem > 0.0:
                    tail_asymmetry = round(put_prem / call_prem, 4)
            except Exception:
                pass

            # 6. Directional signal
            conv_norm = float(np.clip(-alert.convexity_percentile / 100.0, -1.0, 0.0))
            skew_norm = float(np.clip(-skew_25d / 0.10, -1.0, 1.0))
            legacy_component = float(np.clip(0.5 * conv_norm + 0.5 * skew_norm, -1.0, 1.0))
            directional_signal = float(
                np.clip(0.6 * legacy_component + 0.4 * implied_skew_signal, -1.0, 1.0)
            )

            # 7. Generate spline coordinates
            k0, k1 = float(unique_strikes.min()), float(unique_strikes.max())
            k_interp = np.linspace(k0, k1, 140)
            iv_line = cs(k_interp)
            curv = cs(k_interp, 2)

            smile_spline = [
                SplinePoint(strike=float(k), iv_pct=float(iv) * 100.0)
                for k, iv in zip(k_interp, iv_line, strict=True)
            ]

            curvature = [
                CurvaturePoint(strike=float(k), curvature=float(c) * 100.0)
                for k, c in zip(k_interp, curv, strict=True)
            ]

            observed = []
            for i in range(options_chain.shape[0]):
                strike = float(options_chain[i, 0])
                iv = float(options_chain[i, 1])
                is_call = float(options_chain[i, 2])
                delta = float(options_chain[i, 3])

                observed_delta = delta if not np.isnan(delta) else 0.0
                observed_iv_pct = 0.0 if np.isnan(iv) else iv * 100.0

                observed.append(
                    ObservedPoint(
                        strike=strike,
                        iv_pct=observed_iv_pct,
                        is_call=is_call,
                        delta=observed_delta,
                    )
                )

            return Result.success(
                TailRiskReport(
                    spot=float(spot),
                    metrics=metrics,
                    alert=alert,
                    risk_reversal=rr,
                    q_skewness=round(q_skewness, 6) if q_skewness is not None else None,
                    q_kurtosis=round(q_kurtosis, 6) if q_kurtosis is not None else None,
                    implied_skew_signal=round(implied_skew_signal, 4),
                    tail_asymmetry=tail_asymmetry,
                    bimodal_alert=bimodal_alert,
                    directional_signal=round(directional_signal, 4),
                    observed=observed,
                    smile_spline=smile_spline,
                    curvature=curvature,
                )
            )

        except Exception as e:
            logger.error(f"Tail risk analysis failed: {e}")
            return Result.failure(reason=f"Tail risk analysis failed: {e}")
