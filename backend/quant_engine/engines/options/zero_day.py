from __future__ import annotations
"""
backend/engine/metrics/zero_day.py
Sector: Options / Zero Day (0DTE) Engine
[ARCH-1, PD-4]

Theoretical basis:
    Zero-day / near-expiry gamma wall, pinning probability, and squeeze-style alerts.
    Vectorized spot profile, cascades, and gravity map analysis.
    Purely stateless, synchronous, offline, and pandas-free.
"""


import logging
import warnings

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.stats import norm


from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.zero_day")

type FloatArray = npt.NDArray[np.float64]

warnings.filterwarnings("ignore", category=RuntimeWarning)


# ── Greeks Vectorized Helper Functions ──────────────────────────────────────────


def bs_gamma_vectorized(
    spot: float,
    strike: FloatArray,
    tte: float,
    rate: float,
    sigma: FloatArray,
) -> FloatArray:
    """Calculates Black-Scholes Gamma vectorially."""
    if tte <= 1e-12:
        return np.zeros_like(strike, dtype=np.float64)

    sig_val = np.maximum(sigma, 1e-12)
    sqrt_t = np.sqrt(tte)

    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(spot / strike) + (rate + 0.5 * sig_val**2) * tte) / (sig_val * sqrt_t)
        pdf_d1 = norm.pdf(d1)
        denom = spot * sig_val * sqrt_t
        return np.where(denom > 1e-12, pdf_d1 / denom, 0.0)


def bs_theta_vectorized(
    spot: float,
    strike: FloatArray,
    tte: float,
    rate: float,
    sigma: FloatArray,
    is_call: FloatArray,
) -> FloatArray:
    """Calculates Black-Scholes Theta vectorially."""
    if tte <= 1e-12:
        return np.zeros_like(strike, dtype=np.float64)

    sig_val = np.maximum(sigma, 1e-12)
    sqrt_t = np.sqrt(tte)

    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(spot / strike) + (rate + 0.5 * sig_val**2) * tte) / (sig_val * sqrt_t)
        d2 = d1 - sig_val * sqrt_t

        pdf_d1 = norm.pdf(d1)
        term1 = -spot * pdf_d1 * sig_val / (2.0 * sqrt_t)

        cdf_d2 = norm.cdf(d2)
        cdf_neg_d2 = norm.cdf(-d2)

        term2 = np.where(
            is_call == 1.0,
            -rate * strike * np.exp(-rate * tte) * cdf_d2,
            rate * strike * np.exp(-rate * tte) * cdf_neg_d2,
        )

    return term1 + term2


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class SqueezeAlert(BaseModel):
    """Zero-day risk alert for potential option squeezes or cascades."""

    model_config = ConfigDict(frozen=True)

    alert_type: str
    severity: str
    strike: float
    message: str
    confidence: float
    metadata: dict[str, float | str | None]


class GravityLevel(BaseModel):
    """Aggregate strike showing attraction or repulsion characteristics."""

    model_config = ConfigDict(frozen=True)

    strike: float
    gex: float
    level_type: str
    strength: float
    oi_concentration: float
    pinning_prob: float


class GexBar(BaseModel):
    """GEX bar coordinate for plotting and delta walls distribution."""

    model_config = ConfigDict(frozen=True)

    strike: float
    gex_bn: float


class PinCurvePoint(BaseModel):
    """Model-calculated pinning probability at a strike coordinate."""

    model_config = ConfigDict(frozen=True)

    strike: float
    pin_prob: float


class ZoneInfoReport(BaseModel):
    """Stabilization or instability price zone boundaries."""

    model_config = ConfigDict(frozen=True)

    x0: float
    x1: float
    kind: str


class ZeroDayReport(BaseModel):
    """Comprehensive zero-day / 0DTE portfolio risk and pinning report."""

    model_config = ConfigDict(frozen=True)

    spot: float
    minutes_to_close: float
    gamma_flip: float
    call_wall: float
    put_wall: float
    total_gex_bn: float
    vanna_pressure_bn: float
    charm_decay_mm: float
    imbalance_ratio: float | None
    pinning_strike: float
    pinning_prob: float
    zone: ZoneInfoReport
    gex_bars: list[GexBar]
    pin_curve: list[PinCurvePoint]
    alerts: list[SqueezeAlert]
    gravity_map: list[GravityLevel]


# ── Zero Day Analysis Function ──────────────────────────────────────────────────


def analyze_zero_day(
    chain_data: FloatArray,
    spot: float,
    r: float,
    minutes_to_close: float,
    initial_oi: FloatArray | None = None,
    spot_multiplier: int = 100,
    rvol_threshold: float = 3.0,
    gamma_rent_threshold: float = 50.0,
) -> Result[ZeroDayReport]:
    """Performs Zero Day (0DTE) option analysis, cascades, walls, gravity, and alerts.

    Parameters
    ----------
    chain_data : 2D NumPy array of shape (N, 10) where columns represent:
                 [strike, is_call (1.0/0.0), bid, ask, last, vol, oi, delta, gamma, iv]
    spot       : Spot price of the underlying asset
    r          : Risk-free rate (interest rate)
    minutes_to_close : Number of minutes remaining until expiry close
    initial_oi : Reference open interest array at session start (optional)
    spot_multiplier : Option contract size multiplier
    rvol_threshold : Standard relative volume threshold for alerts
    gamma_rent_threshold : Risk-unit threshold for Gamma Rent ignition

    Returns
    -------
    Result[ZeroDayReport]
    """
    if chain_data is None:
        return Result.failure(reason="chain_data must not be None")
    if chain_data.ndim != 2 or chain_data.shape[1] < 10:
        return Result.failure(
            reason=(
                f"chain_data must be a 2D array with at least 10 columns. "
                f"Got shape {chain_data.shape if chain_data is not None else 'None'}"
            )
        )
    if spot <= 0.0:
        return Result.failure(reason=f"spot price must be greater than zero. Got {spot}")
    if r < 0.0:
        return Result.failure(reason=f"interest rate must be non-negative. Got {r}")
    if minutes_to_close <= 0.0:
        return Result.failure(
            reason=f"minutes to close must be greater than zero. Got {minutes_to_close}"
        )

    try:
        n = chain_data.shape[0]
        if n == 0:
            return Result.failure(reason="empty_portfolio")

        strikes = chain_data[:, 0]
        is_call = chain_data[:, 1]
        bid = chain_data[:, 2]
        ask = chain_data[:, 3]
        last = chain_data[:, 4]
        volume = chain_data[:, 5]
        open_interest = chain_data[:, 6]
        delta = chain_data[:, 7]
        gamma = chain_data[:, 8]
        iv = chain_data[:, 9]

        if np.sum(open_interest) <= 0.0:
            return Result.failure(reason="zero_oi")

        t_years = max(minutes_to_close / (252.0 * 390.0), 1e-6)

        # Baseline mid price reconstruction
        mid = (bid + ask) / 2.0
        mid = np.where((mid <= 0.0) | np.isnan(mid), last, mid)

        otm_flag = np.where(
            is_call == 1.0,
            strikes > spot,
            strikes < spot,
        )

        # Compute missing gamma values if any
        missing_gamma_mask = (gamma <= 0.0) | np.isnan(gamma)
        if np.any(missing_gamma_mask):
            computed_gamma = bs_gamma_vectorized(spot, strikes, t_years, r, iv)
            gamma = np.where(missing_gamma_mask, computed_gamma, gamma)

        # Theta vectorized calculation (since it is not in the columns)
        theta = bs_theta_vectorized(spot, strikes, t_years, r, iv, is_call)

        vanna = (gamma * spot * 0.01) / (spot * np.clip(iv, 0.01, None))
        charm = -gamma * iv / (2.0 * np.sqrt(t_years) * spot)

        # 1. GEX computation
        spot_sq = spot**2
        gex_raw = gamma * open_interest * spot_multiplier * spot_sq / 1e9
        sign = np.where(is_call == 1.0, 1.0, -1.0)
        gex_ops = gex_raw * sign

        unique_strikes, inverse_indices = np.unique(strikes, return_inverse=True)
        gex_by_strike = np.bincount(inverse_indices, weights=gex_ops)
        oi_by_strike = np.bincount(inverse_indices, weights=open_interest)

        # 2. RVOL computation
        if initial_oi is not None:
            oi_ref = np.maximum(initial_oi, 1.0)
        else:
            oi_ref = np.maximum(open_interest, 1.0)

        rvol = volume / oi_ref
        rvol_flag = rvol > rvol_threshold

        # 3. Gamma Rent
        gex_unit = gamma * spot_multiplier * spot_sq / 1e6
        theta_abs = np.clip(np.abs(theta), 0.001, None)
        gamma_rent = gex_unit / theta_abs
        ignition_flag = gamma_rent > gamma_rent_threshold

        # 4. Vanna and Charm cascades
        vanna_usd = vanna * sign * open_interest * spot_multiplier * spot / 1e9
        vanna_pressure = float(np.sum(vanna_usd))

        charm_flow = charm * sign * open_interest * spot_multiplier * spot / 1e6
        charm_decay = float(np.sum(charm_flow))

        # 5. Pinning Probability Matrix-wise
        oi_norm = oi_by_strike / max(np.sum(oi_by_strike), 1.0)
        gex_neg = np.maximum(-gex_by_strike, 0.0)
        gex_neg_norm = gex_neg / max(np.sum(gex_neg), 1.0)
        proximity = np.exp(-0.5 * ((unique_strikes - spot) / (spot * 0.01)) ** 2)
        prox_norm = proximity / max(np.sum(proximity), 1.0)
        time_factor = np.exp(-minutes_to_close / 60.0)

        raw_score = (0.35 * gex_neg_norm + 0.35 * oi_norm + 0.30 * prox_norm) * time_factor
        mean_score = np.mean(raw_score) if len(raw_score) > 0 else 0.0
        prob = 1.0 / (1.0 + np.exp(-10.0 * (raw_score - mean_score)))

        # 6. Gravity Map
        max_gex = float(np.max(np.abs(gex_by_strike))) if len(gex_by_strike) > 0 else 1e-9
        max_oi = float(np.max(oi_by_strike)) if len(oi_by_strike) > 0 else 1.0

        attractions = []
        repulsions = []
        for i in range(len(unique_strikes)):
            strike_val = float(unique_strikes[i])
            gex_val = float(gex_by_strike[i])
            oi_val = float(oi_by_strike[i])
            pin_prob = float(prob[i])

            strength = abs(gex_val) / max(max_gex, 1e-9)
            oi_conc = oi_val / max(max_oi, 1.0)

            level = GravityLevel(
                strike=strike_val,
                gex=gex_val,
                level_type="ATTRACTION" if gex_val < 0.0 else "REPULSION",
                strength=strength,
                oi_concentration=oi_conc,
                pinning_prob=pin_prob,
            )
            if gex_val < 0.0:
                attractions.append(level)
            else:
                repulsions.append(level)

        attractions.sort(key=lambda x: x.strength, reverse=True)
        repulsions.sort(key=lambda x: x.strength, reverse=True)
        gravity_map = attractions + repulsions

        # 7. Gamma Flip Strike
        gex_cum = np.cumsum(gex_by_strike)
        sign_changes = np.where(np.diff(np.sign(gex_cum)))[0]

        if len(sign_changes) == 0:
            flip_strike = (
                float(unique_strikes[np.argmin(np.abs(gex_cum))])
                if len(unique_strikes) > 0
                else spot
            )
        else:
            candidate_strikes = unique_strikes[sign_changes]
            closest_idx = int(np.argmin(np.abs(candidate_strikes - spot)))
            flip_strike = float(candidate_strikes[closest_idx])
            idx = int(sign_changes[closest_idx])
            if idx + 1 < len(unique_strikes):
                g0, g1 = gex_cum[idx], gex_cum[idx + 1]
                s0, s1 = unique_strikes[idx], unique_strikes[idx + 1]
                if g1 - g0 != 0.0:
                    flip_strike = float(s0 + (0.0 - g0) * (s1 - s0) / (g1 - g0))

        # 8. Call and Put Walls
        call_mask = gex_by_strike > 0.0
        put_mask = gex_by_strike < 0.0

        if np.any(call_mask):
            call_wall = float(unique_strikes[call_mask][np.argmax(gex_by_strike[call_mask])])
        else:
            call_wall = spot

        if np.any(put_mask):
            put_wall = float(unique_strikes[put_mask][np.argmin(gex_by_strike[put_mask])])
        else:
            put_wall = spot

        # 9. Volume Imbalance
        otm_calls_mask = (is_call == 1.0) & otm_flag
        otm_puts_mask = (is_call == 0.0) & otm_flag
        call_vol = float(np.sum(volume[otm_calls_mask]))
        put_vol = float(np.sum(volume[otm_puts_mask]))
        if put_vol < 1.0:
            imbalance = float("inf") if call_vol > 0.0 else 1.0
        else:
            imbalance = call_vol / put_vol

        # 10. Generate Squeeze Alerts
        alerts = []
        for i in range(n):
            if rvol_flag[i]:
                severity = "CRITICAL" if rvol[i] > 6.0 else "HIGH" if rvol[i] > 4.0 else "MEDIUM"
                opt_type_str = "C" if is_call[i] == 1.0 else "P"
                alerts.append(
                    SqueezeAlert(
                        alert_type="GAMMA_SQUEEZE",
                        severity=severity,
                        strike=float(strikes[i]),
                        message=(
                            f"RVOL elevado en {opt_type_str} strike {strikes[i]:.0f}: "
                            f"{rvol[i]:.1f}x OI de referencia."
                        ),
                        confidence=min(0.95, float(rvol[i]) / 10.0),
                        metadata={
                            "rvol": float(rvol[i]),
                            "option_type": opt_type_str,
                        },
                    )
                )

            if ignition_flag[i]:
                opt_type_str = "C" if is_call[i] == 1.0 else "P"
                alerts.append(
                    SqueezeAlert(
                        alert_type="GAMMA_SQUEEZE",
                        severity="HIGH",
                        strike=float(strikes[i]),
                        message=(
                            f"Gamma rent alto en {opt_type_str} strike {strikes[i]:.0f}: "
                            f"ratio {gamma_rent[i]:.1f}."
                        ),
                        confidence=min(0.85, float(gamma_rent[i]) / 200.0),
                        metadata={"gamma_rent": float(gamma_rent[i])},
                    )
                )

        vanna_threshold = 0.5
        if abs(vanna_pressure) > vanna_threshold:
            direction = "ALCISTA" if vanna_pressure > 0.0 else "BAJISTA"
            alerts.append(
                SqueezeAlert(
                    alert_type="VANNA_FLUSH",
                    severity="HIGH",
                    strike=spot,
                    message=(
                        f"Vanna pressure {direction}: {vanna_pressure:.2f} B USD por 1% IV. "
                        "Movimientos de IV pueden forzar rebalanceo de delta."
                    ),
                    confidence=min(0.90, abs(vanna_pressure) / 2.0),
                    metadata={"vanna_pressure_bn": float(vanna_pressure)},
                )
            )

        charm_threshold = 100.0
        if abs(charm_decay) > charm_threshold and minutes_to_close < 120.0:
            direction = "VENDEDOR" if charm_decay < 0.0 else "COMPRADOR"
            alerts.append(
                SqueezeAlert(
                    alert_type="CHARM_CASCADE",
                    severity="CRITICAL" if minutes_to_close < 60.0 else "HIGH",
                    strike=spot,
                    message=(
                        f"Charm cascade {direction}: {charm_decay:.1f} M USD/min; "
                        f"{minutes_to_close:.0f} min aprox. al cierre de la expiración."
                    ),
                    confidence=min(0.92, abs(charm_decay) / 500.0),
                    metadata={
                        "charm_flow_mm": float(charm_decay),
                        "minutes_to_close": minutes_to_close,
                    },
                )
            )

        if imbalance > 2.0 and minutes_to_close < 90.0:
            alerts.append(
                SqueezeAlert(
                    alert_type="GAMMA_SQUEEZE",
                    severity="HIGH",
                    strike=spot,
                    message=f"Desequilibrio volumen OTM calls/puts: ratio {imbalance:.1f}:1.",
                    confidence=min(0.80, imbalance / 5.0),
                    metadata={"imbalance_ratio": float(imbalance)},
                )
            )
        elif imbalance < 0.5 and imbalance > 0.0 and minutes_to_close < 90.0:
            inv = 1.0 / imbalance
            alerts.append(
                SqueezeAlert(
                    alert_type="GAMMA_SQUEEZE",
                    severity="HIGH",
                    strike=spot,
                    message=f"Desequilibrio volumen OTM puts/calls: ratio {inv:.1f}:1.",
                    confidence=min(0.80, inv / 5.0),
                    metadata={"imbalance_ratio": float(imbalance)},
                )
            )

        if len(prob) > 0:
            pinning_strike_idx = np.argmax(prob)
            pin_strike = unique_strikes[pinning_strike_idx]
            pin_prob = float(prob[pinning_strike_idx])
            if pin_prob > 0.60 and minutes_to_close < 90.0:
                alerts.append(
                    SqueezeAlert(
                        alert_type="PINNING",
                        severity="MEDIUM",
                        strike=float(pin_strike),
                        message=(
                            f"Pinning relativamente alto en strike {float(pin_strike):.0f} "
                            f"(prob modelo {pin_prob:.1%})."
                        ),
                        confidence=pin_prob,
                        metadata={"pinning_prob": pin_prob},
                    )
                )

        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        alerts.sort(key=lambda a: severity_order.get(a.severity, 4))
        alerts_out = alerts[:24]

        # 11. Zone Info
        if spot > flip_strike:
            zone = ZoneInfoReport(x0=flip_strike, x1=float(spot), kind="positive_stabilization")
        else:
            zone = ZoneInfoReport(x0=float(spot), x1=flip_strike, kind="negative_instability")

        gex_bars = [
            GexBar(strike=float(s), gex_bn=float(g))
            for s, g in zip(unique_strikes.tolist(), gex_by_strike.tolist(), strict=True)
        ]
        pin_curve = [
            PinCurvePoint(strike=float(s), pin_prob=float(p))
            for s, p in zip(unique_strikes.tolist(), prob.tolist(), strict=True)
        ]

        pinning_strike = float(unique_strikes[np.argmax(prob)]) if len(prob) > 0 else spot
        pinning_prob = float(np.max(prob)) if len(prob) > 0 else 0.0

        return Result.success(
            ZeroDayReport(
                spot=float(spot),
                minutes_to_close=float(minutes_to_close),
                gamma_flip=flip_strike,
                call_wall=call_wall,
                put_wall=put_wall,
                total_gex_bn=float(np.sum(gex_by_strike)),
                vanna_pressure_bn=vanna_pressure,
                charm_decay_mm=charm_decay,
                imbalance_ratio=(None if np.isnan(imbalance) or np.isinf(imbalance) else imbalance),
                pinning_strike=pinning_strike,
                pinning_prob=pinning_prob,
                zone=zone,
                gex_bars=gex_bars,
                pin_curve=pin_curve,
                alerts=alerts_out,
                gravity_map=gravity_map,
            )
        )

    except Exception as e:
        logger.error(f"Zero-day analysis failed: {e}")
        return Result.failure(reason=f"Zero-day analysis failed: {e}")
