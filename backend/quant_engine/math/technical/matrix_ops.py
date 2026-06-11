"""
backend/engine/metrics/matrix_ops.py
Sector 1: Operaciones Base y Matrices (Probabilistic Math Engine)
[ARCH-1, PD-4]
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
import numpy.typing as npt
import pandas as pd
from pydantic import BaseModel, ConfigDict
from scipy import stats

from backend.models.result import Result

FloatArray: TypeAlias = npt.NDArray[np.float64]

# ─────────────────────────────────────────────────────────────────────────────
# TEMPORAL PYDANTIC MODELS (Asumimos que luego migrarán a backend.engine.models)
# ─────────────────────────────────────────────────────────────────────────────


class AdaptiveState(BaseModel):
    model_config = ConfigDict(frozen=True)
    pr_ordered: float
    trend_strength: float


class JumpRisk(BaseModel):
    model_config = ConfigDict(frozen=True)
    jump_intensity: float
    mu_j: float
    sigma_j: float
    jump_prob: float


class KellySizing(BaseModel):
    model_config = ConfigDict(frozen=True)
    full_kelly: float
    half_kelly: float
    quarter_kelly: float
    expected_value: float


class TailRisk(BaseModel):
    model_config = ConfigDict(frozen=True)
    shape: float
    scale: float
    threshold: float
    var_99: float
    cvar_99: float


# ─────────────────────────────────────────────────────────────────────────────
# 1. CORE MATH & KERNELS
# ─────────────────────────────────────────────────────────────────────────────


def safe_divide(numerator: float, denominator: float) -> Result[float]:
    """División segura, retorna failure si el divisor es 0."""
    if denominator == 0.0:
        return Result.failure(reason="Division by zero")
    return Result.success(numerator / denominator)


def fit_gpd(returns: FloatArray, quantile: float = 0.95) -> Result[TailRisk]:
    """Fits a Generalized Pareto Distribution (GPD) using Peaks Over Threshold (POT)."""
    try:
        losses = -returns[returns < 0]
        if len(losses) < 20:
            return Result.failure(reason="Not enough negative returns (losses) to fit GPD (<20)")

        threshold = np.percentile(losses, quantile * 100)
        exceedances = losses[losses > threshold] - threshold

        if len(exceedances) < 5:
            var_99 = float(np.percentile(losses, 99))
            cvar_99 = float(np.mean(losses[losses > np.percentile(losses, 99)]))
            return Result.success(
                TailRisk(
                    shape=0.0, scale=0.0, threshold=float(threshold), var_99=var_99, cvar_99=cvar_99
                )
            )

        shape, loc, scale = stats.genpareto.fit(exceedances, floc=0)

        n = len(losses)
        n_u = len(exceedances)
        p = 0.99

        if abs(shape) < 1e-4:
            var_99 = threshold + scale * np.log((n / n_u) * (1 - p))
        else:
            var_99 = threshold + (scale / shape) * (pow(((n / n_u) * (1 - p)), -shape) - 1)

        cvar_99 = (
            (var_99 + scale - shape * threshold) / (1 - shape) if shape < 1.0 else var_99 * 1.5
        )

        return Result.success(
            TailRisk(
                shape=float(shape),
                scale=float(scale),
                threshold=float(threshold),
                var_99=float(var_99),
                cvar_99=float(cvar_99),
            )
        )
    except Exception as e:
        return Result.failure(reason=f"EVT GPD fit failed: {e}")


def estimate_mjd_params(returns: FloatArray) -> Result[dict[str, float]]:
    """Estimates Merton Jump-Diffusion parameters using log-returns."""
    try:
        if len(returns) < 30:
            return Result.failure(reason="Insufficient data points for MJD estimation (<30)")

        mu = np.mean(returns)
        var = np.var(returns)
        skew = stats.skew(returns)
        kurt = stats.kurtosis(returns)

        jump_intensity = max(0.0, kurt / 3.0)
        mu_j = skew * np.sqrt(var)
        sigma_j = np.sqrt(max(0.001, var * kurt))
        jump_prob = 1.0 - np.exp(-jump_intensity)

        return Result.success(
            {
                "jump_intensity": float(jump_intensity),
                "mu_j": float(mu_j),
                "sigma_j": float(sigma_j),
                "jump_prob": float(jump_prob),
            }
        )
    except Exception as e:
        return Result.failure(reason=f"MJD estimation failed: {e}")


def calibrate_heston_vov(returns: FloatArray, iv_series: FloatArray) -> Result[float]:
    """Estimates Vol-of-Vol (vov) parameter from Heston dynamics."""
    try:
        if len(iv_series) < 20:
            return Result.failure(reason="Insufficient data points for Heston vov (<20)")

        v = iv_series**2
        dv = np.diff(v)
        dt = 1 / 252.0

        v_prev = v[:-1]
        raw_vov = np.std(dv / (np.sqrt(v_prev) + 1e-6)) / np.sqrt(dt)

        return Result.success(float(np.clip(raw_vov, 0.0, 5.0)))
    except Exception as e:
        return Result.failure(reason=f"Heston vov calibration failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. STATE ESTIMATION & FILTERING
# ─────────────────────────────────────────────────────────────────────────────


class ParticleFilter:
    """Sequential Monte Carlo (Particle Filter) for Latent State Estimation."""

    def __init__(self, n_particles: int = 1000):
        self.n_particles = n_particles
        self.particles = np.random.uniform(0, 1, (n_particles, 2))
        self.weights = np.ones(n_particles) / n_particles

    def update(self, price_change: float, volume_change: float) -> None:
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


def run_particle_filter(ohlcv: pd.DataFrame) -> Result[AdaptiveState]:
    """Runs a SMC Particle Filter over historical OHLCV to estimate current regime."""
    try:
        if len(ohlcv) < 2:
            return Result.failure(reason="Insufficient OHLCV data (<2 rows)")

        pf = ParticleFilter(n_particles=500)
        returns = ohlcv["close"].pct_change().fillna(0).values
        vols = ohlcv["volume"].pct_change().fillna(0).values

        window = min(len(ohlcv), 60)
        for i in range(len(ohlcv) - window, len(ohlcv)):
            pf.update(returns[i], vols[i])

        return Result.success(pf.get_state())
    except Exception as e:
        return Result.failure(reason=f"Particle Filter run failed: {e}")


def particle_filter_volatility(
    returns: FloatArray,
    n_particles: int = 1000,
    v_mean: float = 0.04,
    kappa: float = 2.0,
    vov: float = 0.3,
) -> Result[float]:
    """Estimates latent volatility using SIR Particle Filter."""
    try:
        if len(returns) == 0:
            return Result.failure(reason="Returns array is empty")

        dt = 1 / 252.0
        particles = np.random.gamma(2.0, v_mean / 2.0, n_particles)
        weights = np.ones(n_particles) / n_particles

        for r in returns:
            particles = np.maximum(
                1e-6,
                particles
                + kappa * (v_mean - particles) * dt
                + vov
                * np.sqrt(np.maximum(0, particles * dt))
                * np.random.normal(0, 1, n_particles),
            )

            log_likelihood = -0.5 * np.log(2 * np.pi * particles) - 0.5 * (r**2 / particles)
            likelihood = np.exp(log_likelihood - np.max(log_likelihood))

            weights *= likelihood
            weights += 1e-300
            weights /= np.sum(weights)

            ess = 1.0 / np.sum(weights**2)
            if ess < n_particles / 2:
                indices = np.random.choice(np.arange(n_particles), size=n_particles, p=weights)
                particles = particles[indices]
                weights = np.ones(n_particles) / n_particles

        return Result.success(float(np.average(particles, weights=weights)))
    except Exception as e:
        return Result.failure(reason=f"PF volatility estimation failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. RISK & PORTFOLIO LOGIC
# ─────────────────────────────────────────────────────────────────────────────


def compute_etv(
    win_prob: float, payoff: float, jump_prob: float, tail_cvar: float
) -> Result[float]:
    """Expected Trade Value calibrated for Probabilistic Gating."""
    try:
        loss_prob = 1.0 - win_prob
        expected_gain = win_prob * payoff
        expected_loss = loss_prob * 1.0

        tail_penalty = jump_prob * tail_cvar * 10.0

        etv = expected_gain - expected_loss - tail_penalty
        return Result.success(float(etv))
    except Exception as e:
        return Result.failure(reason=f"ETV computation failed: {e}")


def project_trajectories(
    current_price: float,
    returns: FloatArray,
    mjd_params: dict[str, float],
    vov: float,
    sigma: float | None = None,
    horizon_days: int = 30,
    n_paths: int = 1000,
) -> Result[FloatArray]:
    """Monte Carlo simulation using Merton Jump-Diffusion and Heston volatility."""
    try:
        if len(returns) == 0:
            return Result.failure(reason="Returns array is empty")

        dt = 1.0 / 252.0
        mu_target = np.mean(returns) * 252.0
        mu_current = np.mean(returns[-30:]) * 252.0 if len(returns) >= 30 else mu_target
        theta = 5.0

        if sigma is None:
            sigma = float(np.std(returns) * np.sqrt(252.0))

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

            mu_current = mu_current + theta * (mu_target - mu_current) * dt

            log_returns = (mu_current - 0.5 * v) * dt + np.sqrt(v * dt) * z_diffusion + j_component
            paths[:, t] = paths[:, t - 1] * np.exp(log_returns)

        return Result.success(paths)
    except Exception as e:
        return Result.failure(reason=f"Trajectory projection failed: {e}")


def estimate_payoff_ratio(returns: FloatArray) -> Result[float]:
    """Estimates the historical payoff ratio (average win / average loss)."""
    try:
        wins = returns[returns > 0]
        losses = np.abs(returns[returns < 0])

        if len(wins) == 0 or len(losses) == 0:
            return Result.success(1.0)

        avg_win = np.mean(wins)
        avg_loss = np.mean(losses)

        if avg_loss < 1e-9:
            return Result.success(1.5)

        ratio = float(avg_win / avg_loss)
        return Result.success(float(np.clip(ratio, 1.5, 5.0)))
    except Exception as e:
        return Result.failure(reason=f"Payoff ratio estimation failed: {e}")


def calculate_kelly_sizing(win_prob: float, payoff_ratio: float) -> Result[KellySizing]:
    """Calculates Kelly criterion fractions."""
    try:
        loss_prob = 1.0 - win_prob
        full_kelly = (
            (win_prob * payoff_ratio - loss_prob) / payoff_ratio if payoff_ratio > 0 else 0.0
        )
        full_kelly = max(0.0, float(full_kelly))

        return Result.success(
            KellySizing(
                full_kelly=full_kelly,
                half_kelly=full_kelly * 0.5,
                quarter_kelly=full_kelly * 0.25,
                expected_value=win_prob * payoff_ratio - loss_prob,
            )
        )
    except Exception as e:
        return Result.failure(reason=f"Kelly sizing calculation failed: {e}")


def apply_macro_anchoring(
    vix: float, us10y: float, current_win_prob: float, current_tail_risk: float
) -> Result[tuple[float, float]]:
    """Adjusts win probability and tail risk based on Macro Anchors."""
    try:
        vix_factor = np.clip((vix - 15.0) / 10.0, -0.2, 0.5)
        yield_factor = np.clip((us10y - 3.5) / 2.0, 0.0, 0.3)

        adj_win_prob = current_win_prob * (1.0 - (vix_factor * 0.1) - (yield_factor * 0.05))
        adj_tail_risk = current_tail_risk * (1.0 + vix_factor + yield_factor * 0.5)

        return Result.success((float(adj_win_prob), float(adj_tail_risk)))
    except Exception as e:
        return Result.failure(reason=f"Macro anchoring failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. OPTIONS & GREEKS MATH
# ─────────────────────────────────────────────────────────────────────────────


class CMMath:
    """Stateless mathematical kernels for dealer GEX / DAGEX and tail-aware Kelly."""

    @staticmethod
    def gex_institutional(
        gamma: FloatArray,
        oi: FloatArray,
        spot: float,
        multiplier: float = 100.0,
        is_call: bool | FloatArray = True,
    ) -> Result[FloatArray]:
        """Institutional GEX (S²): Gamma * OI * mult * S² * sign(call/put)."""
        try:
            sign = np.where(is_call, 1.0, -1.0)
            res = np.asarray(gamma * oi * multiplier * (spot**2) * sign, dtype=np.float64)
            return Result.success(res)
        except Exception as e:
            return Result.failure(reason=f"Institutional GEX calculation failed: {e}")

    @staticmethod
    def dagex(
        gamma: FloatArray,
        delta: FloatArray,
        oi: FloatArray,
        spot: float,
        multiplier: float = 100.0,
        is_call: bool | FloatArray = True,
    ) -> Result[FloatArray]:
        """Delta-adjusted gamma exposure."""
        try:
            sign = np.where(is_call, 1.0, -1.0)
            res = np.asarray(
                gamma * np.abs(delta) * oi * multiplier * spot * sign, dtype=np.float64
            )
            return Result.success(res)
        except Exception as e:
            return Result.failure(reason=f"DAGEX calculation failed: {e}")

    @staticmethod
    def proximitiy_weight(tte_years: FloatArray) -> Result[FloatArray]:
        """Expiry proximity weight w = exp(-TTE * 52)."""
        try:
            res = np.exp(-tte_years * 52.0)
            return Result.success(res)
        except Exception as e:
            return Result.failure(reason=f"Proximity weight calculation failed: {e}")

    @staticmethod
    def vrp_log_ratio(iv: FloatArray | float, hv: FloatArray | float) -> Result[FloatArray | float]:
        """Log VRP ln(IV/HV) with numerical floors."""
        try:
            iv_safe = np.maximum(iv, 1e-6)
            hv_safe = np.maximum(hv, 1e-6)
            out = np.log(iv_safe / hv_safe)
            if isinstance(out, np.ndarray):
                return Result.success(np.asarray(out, dtype=np.float64))
            return Result.success(float(out))
        except Exception as e:
            return Result.failure(reason=f"VRP log ratio calculation failed: {e}")

    @staticmethod
    def kelly_fat_tail(
        mu: float,
        sigma: float,
        kurtosis: float,
        fraction: float = 0.5,
    ) -> Result[float]:
        """Kelly fraction damped for excess kurtosis."""
        try:
            if sigma <= 1e-9:
                return Result.failure(reason="Sigma too low for Kelly calculation")
            raw_kelly = mu / (sigma**2)
            tail_adj = 1.0 / (1.0 + max(0.0, kurtosis) / 6.0)
            return Result.success(float(np.clip(raw_kelly * tail_adj * fraction, 0.0, 1.0)))
        except Exception as e:
            return Result.failure(reason=f"Kelly fat tail calculation failed: {e}")

    @staticmethod
    def markov_projection(
        transition_matrix: FloatArray, current_state_idx: int, n_steps: int
    ) -> Result[FloatArray]:
        """Chapman–Kolmogorov: distribution after n_steps."""
        try:
            t_n = np.linalg.matrix_power(transition_matrix, n_steps)
            v0 = np.zeros(transition_matrix.shape[0])
            v0[current_state_idx] = 1.0
            return Result.success(np.asarray(v0 @ t_n, dtype=np.float64))
        except Exception as e:
            return Result.failure(reason=f"Markov projection failed: {e}")


def compute_vanna_vol_drift(vanna_exposure: float, iv_change: float) -> Result[float]:
    """Vol-drift contribution from vanna exposure."""
    try:
        return Result.success(float(vanna_exposure * iv_change))
    except Exception as e:
        return Result.failure(reason=f"Vanna vol drift calculation failed: {e}")


def compute_charm_price_bias(charm_exposure: float, time_decay: float) -> Result[float]:
    """Price-bias contribution from charm exposure."""
    try:
        return Result.success(float(charm_exposure * time_decay))
    except Exception as e:
        return Result.failure(reason=f"Charm price bias calculation failed: {e}")


def calculate_probabilistic_gex_gating(
    current_gex: float,
    vanna_flow: float,
    regime_confidence: float,
    threshold: float = 0.5,
) -> Result[bool]:
    """Heuristic stability gate: positive GEX/vanna support + regime confidence."""
    try:
        gex_aligned = current_gex > 0
        vanna_aligned = vanna_flow > 0
        stability_score = (
            0.4 * float(gex_aligned) + 0.4 * float(vanna_aligned) + 0.2 * regime_confidence
        )
        return Result.success(stability_score >= threshold)
    except Exception as e:
        return Result.failure(reason=f"Probabilistic GEX gating calculation failed: {e}")
