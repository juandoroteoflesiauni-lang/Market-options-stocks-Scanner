"""
backend/layer_3_specialists/ia_probabilistico/engines/probabilistic_engine.py
════════════════════════════════════════════════════════════════════════════════
Probabilistic Math Engine — advanced statistical and stochastic modeling.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import TypeAlias

import numpy as np
import numpy.typing as npt
import pandas as pd  # type: ignore[import-untyped]
from scipy import stats  # type: ignore[import-untyped]

from ..domain.probabilistic_models import AdaptiveState, KellySizing, TailRisk

logger = logging.getLogger(__name__)

FloatArray: TypeAlias = npt.NDArray[np.float64]

# ─────────────────────────────────────────────────────────────────────────────
# 1. TAIL KERNEL: Extreme Value Theory (EVT)
# ─────────────────────────────────────────────────────────────────────────────


def fit_gpd(returns: FloatArray, quantile: float = 0.95) -> TailRisk:
    """
    Fits a Generalized Pareto Distribution (GPD) using Peaks Over Threshold (POT).

    Returns:
        TailRisk: {shape, scale, threshold, vaR_99, cVaR_99}
    """
    try:
        # We analyze negative returns (losses)
        losses = -returns[returns < 0]
        if len(losses) < 20:
            return TailRisk(shape=0.0, scale=0.0, threshold=0.0, var_99=0.0, cvar_99=0.0)

        threshold = np.percentile(losses, quantile * 100)
        exceedances = losses[losses > threshold] - threshold

        if len(exceedances) < 5:
            var_99 = float(np.percentile(losses, 99))
            cvar_99 = float(np.mean(losses[losses > np.percentile(losses, 99)]))
            return TailRisk(
                shape=0.0, scale=0.0, threshold=float(threshold), var_99=var_99, cvar_99=cvar_99
            )

        # GPD parameters: shape (xi), scale (sigma)
        # scipy.stats.genpareto.fit uses (shape, loc, scale)
        # xi = shape, sigma = scale. We fix loc=0.
        shape, loc, scale = stats.genpareto.fit(exceedances, floc=0)

        # Tail calculations (VaR and CVaR at 99%)
        n = len(losses)
        n_u = len(exceedances)
        p = 0.99

        # VaR_p = u + (sigma / xi) * [ ( (n/n_u)*(1-p) )^-xi - 1 ]
        if abs(shape) < 1e-4:  # Limit as xi -> 0 (Exponential)
            var_99 = threshold + scale * np.log((n / n_u) * (1 - p))
        else:
            var_99 = threshold + (scale / shape) * (pow(((n / n_u) * (1 - p)), -shape) - 1)

        # CVaR_p = (VaR_p + sigma - xi*u) / (1 - xi)
        cvar_99 = (
            (var_99 + scale - shape * threshold) / (1 - shape) if shape < 1.0 else var_99 * 1.5
        )

        return TailRisk(
            shape=float(shape),
            scale=float(scale),
            threshold=float(threshold),
            var_99=float(var_99),
            cvar_99=float(cvar_99),
        )
    except Exception as e:
        logger.error(f"EVT GPD fit failed: {e}")
        return TailRisk(shape=0.0, scale=0.0, threshold=0.0, var_99=0.0, cvar_99=0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. JUMP KERNEL: Merton Jump-Diffusion (MJD)
# ─────────────────────────────────────────────────────────────────────────────


def estimate_mjd_params(returns: FloatArray) -> dict[str, float]:
    """
    Estimates Merton Jump-Diffusion parameters using log-returns.
    """
    try:
        if len(returns) < 30:
            return {"jump_intensity": 0.0, "mu_j": 0.0, "sigma_j": 0.0, "jump_prob": 0.0}

        mu = np.mean(returns)
        var = np.var(returns)
        skew = stats.skew(returns)
        kurt = stats.kurtosis(returns)  # excess kurtosis

        jump_intensity = max(0.0, kurt / 3.0)
        mu_j = skew * np.sqrt(var)
        sigma_j = np.sqrt(max(0.001, var * kurt))
        jump_prob = 1.0 - np.exp(-jump_intensity)

        return {
            "jump_intensity": float(jump_intensity),
            "mu_j": float(mu_j),
            "sigma_j": float(sigma_j),
            "jump_prob": float(jump_prob),
        }
    except Exception as e:
        logger.error(f"MJD estimation failed: {e}")
        return {"jump_intensity": 0.0, "mu_j": 0.0, "sigma_j": 0.0, "jump_prob": 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# 3. VOL KERNEL: Heston Model & VIX-Crush
# ─────────────────────────────────────────────────────────────────────────────


def calibrate_heston_vov(returns: FloatArray, iv_series: FloatArray) -> float:
    """
    Estimates Vol-of-Vol (vov) parameter from Heston dynamics.
    """
    try:
        if len(iv_series) < 20:
            return 0.0

        v = iv_series**2
        dv = np.diff(v)
        dt = 1 / 252.0

        v_prev = v[:-1]
        raw_vov = np.std(dv / (np.sqrt(v_prev) + 1e-6)) / np.sqrt(dt)

        return float(np.clip(raw_vov, 0.0, 5.0))
    except Exception as e:
        logger.error(f"Heston vov calibration failed: {e}")
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 4. STATE KERNEL: Particle Filter (SMC)
# ─────────────────────────────────────────────────────────────────────────────


class ParticleFilter:
    """
    Sequential Monte Carlo (Particle Filter) for Latent State Estimation.
    Used for regime detection (Ordered vs Chaotic).
    """

    def __init__(self, n_particles: int = 1000):
        self.n_particles = n_particles
        self.particles = np.random.uniform(0, 1, (n_particles, 2))
        self.weights = np.ones(n_particles) / n_particles

    def update(self, price_change: float, volume_change: float) -> None:
        """Transition and Weight Update."""
        self.particles += np.random.normal(0, 0.05, (self.n_particles, 2))
        self.particles = np.clip(self.particles, 0, 1)

        alignment = abs(price_change * volume_change)
        likelihood = np.exp(-((self.particles[:, 0] - alignment) ** 2) / 0.1)

        self.weights *= likelihood + 1e-9
        self.weights /= np.sum(self.weights) + 1e-9

        if 1.0 / np.sum(self.weights**2) < self.n_particles / 2.0:
            indices = np.random.choice(
                range(self.n_particles), size=self.n_particles, p=self.weights
            )
            self.particles = self.particles[indices]
            self.weights = np.ones(self.n_particles) / self.n_particles

    def get_state(self) -> AdaptiveState:
        mean_state = np.average(self.particles, weights=self.weights, axis=0)
        return AdaptiveState(pr_ordered=float(mean_state[0]), trend_strength=float(mean_state[1]))


def run_particle_filter(ohlcv: pd.DataFrame) -> AdaptiveState:
    """Runs a SMC Particle Filter over historical OHLCV to estimate current regime."""
    try:
        pf = ParticleFilter(n_particles=500)
        returns = ohlcv["close"].pct_change().fillna(0).values
        vols = ohlcv["volume"].pct_change().fillna(0).values

        window = min(len(ohlcv), 60)
        for i in range(len(ohlcv) - window, len(ohlcv)):
            pf.update(returns[i], vols[i])

        return pf.get_state()
    except Exception as e:
        logger.error(f"Particle Filter run failed: {e}")
        return AdaptiveState(pr_ordered=0.5, trend_strength=0.5)


# ─────────────────────────────────────────────────────────────────────────────
# 5. RISK LAYER: Expected Trade Value (ETV)
# ─────────────────────────────────────────────────────────────────────────────


def compute_etv(win_prob: float, payoff: float, jump_prob: float, tail_cvar: float) -> float:
    """
    Expected Trade Value calibrated for Probabilistic Gating.
    """
    loss_prob = 1.0 - win_prob
    expected_gain = win_prob * payoff
    expected_loss = loss_prob * 1.0

    tail_penalty = jump_prob * tail_cvar * 10.0

    etv = expected_gain - expected_loss - tail_penalty
    return float(etv)


# ─────────────────────────────────────────────────────────────────────────────
# 6. PREDICTIVE LAYER: Trajectory Projection (Fan Charts)
# ─────────────────────────────────────────────────────────────────────────────


def project_trajectories(
    current_price: float,
    returns: FloatArray,
    mjd_params: dict[str, float],
    vov: float,
    sigma: float | None = None,
    horizon_days: int = 30,
    n_paths: int = 1000,
) -> FloatArray:
    """
    Monte Carlo simulation using Merton Jump-Diffusion (MJD)
    and Heston-like stochastic volatility.
    """
    try:
        dt = 1.0 / 252.0

        # Ornstein-Uhlenbeck (Mean Reversion) parameters
        mu_target = np.mean(returns) * 252.0
        # Use recent short-term drift as starting drift
        mu_current = np.mean(returns[-30:]) * 252.0 if len(returns) >= 30 else mu_target
        theta = 5.0  # Speed of mean reversion

        if sigma is None:
            sigma = np.std(returns) * np.sqrt(252.0)

        lam = mjd_params.get("jump_intensity", 0.0)
        mu_j = mjd_params.get("mu_j", 0.0)
        sig_j = mjd_params.get("sigma_j", 0.01)

        paths = np.zeros((n_paths, horizon_days + 1))
        paths[:, 0] = current_price

        v = np.full(n_paths, sigma**2)

        for t in range(1, horizon_days + 1):
            z_diffusion = np.random.normal(0, 1, n_paths)

            n_jumps = np.random.poisson(lam * dt, n_paths)
            j_component = np.zeros(n_paths)
            for i in range(n_paths):
                if n_jumps[i] > 0:
                    j_component[i] = np.sum(np.random.normal(mu_j, sig_j, n_jumps[i]))

            v = np.maximum(1e-6, v + vov * np.sqrt(v) * np.random.normal(0, np.sqrt(dt), n_paths))

            # Mean-reverting drift
            mu_current = mu_current + theta * (mu_target - mu_current) * dt

            log_returns = (mu_current - 0.5 * v) * dt + np.sqrt(v * dt) * z_diffusion + j_component
            paths[:, t] = paths[:, t - 1] * np.exp(log_returns)

        return paths
    except Exception as e:
        logger.error(f"Trajectory projection failed: {e}")
        return np.full((n_paths, horizon_days + 1), current_price)


def estimate_payoff_ratio(returns: FloatArray) -> float:
    """
    Estimates the historical payoff ratio (average win / average loss).
    Used as the 'b' parameter in Kelly Criterion.
    """
    try:
        wins = returns[returns > 0]
        losses: FloatArray = np.abs(returns[returns < 0])

        if len(wins) == 0 or len(losses) == 0:
            return 1.0

        avg_win = np.mean(wins)
        avg_loss = np.mean(losses)

        # Avoid division by zero
        if avg_loss < 1e-9:
            return 1.5

        ratio = float(avg_win / avg_loss)
        # Institutional Floor: We only size for setups with at least 1.5 target potential
        return float(np.clip(ratio, 1.5, 5.0))
    except Exception:
        return 1.5


def calculate_kelly_sizing(win_prob: float, payoff_ratio: float) -> KellySizing:
    """Calculates Kelly criterion fractions."""
    # Kelly % = (p*b - q) / b where p=win_prob, b=payoff_ratio, q=loss_prob
    loss_prob = 1.0 - win_prob
    full_kelly = (win_prob * payoff_ratio - loss_prob) / payoff_ratio if payoff_ratio > 0 else 0
    full_kelly = max(0, full_kelly)

    return KellySizing(
        full_kelly=full_kelly,
        half_kelly=full_kelly * 0.5,
        quarter_kelly=full_kelly * 0.25,
        expected_value=win_prob * payoff_ratio - loss_prob,
    )


def particle_filter_volatility(
    returns: FloatArray,
    n_particles: int = 1000,
    v_mean: float = 0.04,
    kappa: float = 2.0,
    vov: float = 0.3,
) -> float:
    """
    Sequential Importance Resampling (SIR) Particle Filter.
    Estimates the latent volatility (V_t) given the returns history.
    """
    dt = 1 / 252.0
    # Particles represent possible variance states
    particles = np.random.gamma(2.0, v_mean / 2.0, n_particles)
    weights = np.ones(n_particles) / n_particles

    for r in returns:
        # 1. Prediction (Transition)
        # Heston-like variance dynamics: dv = kappa(v_mean - v)dt + vov*sqrt(v)dw
        particles = np.maximum(
            1e-6,
            particles
            + kappa * (v_mean - particles) * dt
            + vov * np.sqrt(np.maximum(0, particles * dt)) * np.random.normal(0, 1, n_particles),
        )

        # 2. Update (Likelihood)
        # Log-likelihood of return r given variance particle: N(0, particles)
        log_likelihood = -0.5 * np.log(2 * np.pi * particles) - 0.5 * (r**2 / particles)
        likelihood = np.exp(log_likelihood - np.max(log_likelihood))  # Stability trick

        weights *= likelihood
        weights += 1e-300  # Prevent division by zero
        weights /= np.sum(weights)

        # 3. Resampling (if effective sample size is low)
        ess = 1.0 / np.sum(weights**2)
        if ess < n_particles / 2:
            indices = np.random.choice(np.arange(n_particles), size=n_particles, p=weights)
            particles = particles[indices]
            weights = np.ones(n_particles) / n_particles

    return float(np.average(particles, weights=weights))


def apply_macro_anchoring(
    vix: float, us10y: float, current_win_prob: float, current_tail_risk: float
) -> tuple[float, float]:
    """
    Adjusts win probability and tail risk based on Macro Anchors.

    Args:
        vix: Volatility Index (VIX) level.
        us10y: US 10-Year Treasury Yield (%).
        current_win_prob: Probability estimated by the local engine.
        current_tail_risk: VaR/CVaR estimated by the local engine.

    Returns:
        tuple: (adjusted_win_prob, adjusted_tail_risk)
    """
    # 1. VIX Impact (Fear Gauge)
    # Neutral VIX is considered ~15-18.
    # High VIX (>22) increases tail risk and lowers directional confidence.
    vix_factor = np.clip((vix - 15.0) / 10.0, -0.2, 0.5)

    # 2. Yield Impact (Risk-Free Rate / Discounting)
    # High yields (>4.5%) increase the hurdle rate for risk assets.
    yield_factor = np.clip((us10y - 3.5) / 2.0, 0.0, 0.3)

    # Adjustments
    adj_win_prob = current_win_prob * (1.0 - (vix_factor * 0.1) - (yield_factor * 0.05))
    adj_tail_risk = current_tail_risk * (1.0 + vix_factor + yield_factor * 0.5)

    return float(adj_win_prob), float(adj_tail_risk)


# ────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: IA / PROBABILÍSTICO
# Archivo        : probabilistic_engine.py
# Sub-capa       : Engine (Mathematical logic)
# Framework ML   : scipy | numpy
# Eliminado      : Referencias legacy a quantumbeta/math header.
# Preservado     : EVT (GPD), MJD, Heston vov, Particle Filter, Kelly Sizing.
# Integrado      : Ornstein-Uhlenbeck drift en Monte Carlo para series estacionarias/divisas.
# Pendientes     : Integración con base de datos para lookbacks históricos.
# ────────────────────────────────────────────────────────────────
