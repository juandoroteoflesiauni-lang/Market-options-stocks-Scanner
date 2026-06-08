"""
backend/engine/metrics/portfolio_optimizer.py
Sector: Options / Portfolio Optimizer Engine
[ARCH-1, PD-4]

Theoretical basis:
    Combines market equilibrium (Prior) with Predictive Views (Black-Litterman model)
    to generate optimal portfolio weights using Mean-Variance Optimization.
    Purely stateless, synchronous, and offline.
"""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.optimize import minimize  # type: ignore[import-not-found, import-untyped]

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.portfolio_optimizer")

type FloatArray = npt.NDArray[np.float64]


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class PortfolioOptimizationReport(BaseModel):
    """Optimal factor weights output report."""

    model_config = ConfigDict(frozen=True)

    tickers: list[str]
    weights: dict[str, float]
    expected_return: float
    expected_volatility: float


# ── Covariance Matrix Free Function ──────────────────────────────────────────────


def calculate_covariance(
    returns_matrix: FloatArray, tickers: list[str]
) -> Result[tuple[list[str], FloatArray]]:
    """Calculates the annualized covariance matrix for a group of aligned returns."""
    if returns_matrix is None or tickers is None:
        return Result.failure(reason="returns_matrix and tickers must not be None")

    if returns_matrix.ndim != 2:
        return Result.failure(reason="returns_matrix must be a 2D array")

    n = len(tickers)
    if returns_matrix.shape[0] != n:
        return Result.failure(
            reason=(
                f"returns_matrix first dimension ({returns_matrix.shape[0]}) "
                f"must match number of tickers ({n})"
            )
        )

    if returns_matrix.shape[1] < 2:
        return Result.failure(reason="returns_matrix must have at least 2 periods of history")

    try:
        cov = np.cov(returns_matrix) * 252.0

        # Enforce 2D matrix structure for single asset outputs
        if isinstance(cov, float) or cov.ndim == 0:
            cov = np.array([[float(cov)]], dtype=np.float64)
        elif cov.ndim == 1:
            cov = cov.reshape(1, 1)

        return Result.success((tickers, cov))
    except Exception as e:
        logger.error(f"Covariance matrix calculation failed: {e}")
        return Result.failure(reason=f"Covariance matrix calculation failed: {e}")


# ── Black-Litterman Optimizer ───────────────────────────────────────────────────


class BlackLittermanOptimizer:
    """Combines CAPM Prior with views to generate optimal portfolio weights."""

    def __init__(self, risk_aversion: float = 2.5, tau: float = 0.05) -> None:
        self.delta = risk_aversion
        self.tau = tau

    def optimize(
        self,
        tickers: list[str],
        cov_matrix: FloatArray,
        prior_returns: FloatArray,
        views: FloatArray,
        confidences: FloatArray,
    ) -> Result[PortfolioOptimizationReport]:
        """Calculates optimal factor weights using SLSQP constraints optimization."""
        if self.delta <= 0.0:
            return Result.failure(reason="risk_aversion must be greater than zero")
        if self.tau <= 0.0:
            return Result.failure(reason="tau must be greater than zero")
        if not tickers:
            return Result.failure(reason="tickers list cannot be empty")

        n = len(tickers)
        if cov_matrix.ndim != 2 or cov_matrix.shape != (n, n):
            return Result.failure(reason=f"cov_matrix must be a 2D array of shape ({n}, {n})")
        if prior_returns.ndim != 1 or prior_returns.shape != (n,):
            return Result.failure(reason=f"prior_returns must be a 1D array of shape ({n},)")
        if views.ndim != 1 or views.shape != (n,):
            return Result.failure(reason=f"views must be a 1D array of shape ({n},)")
        if confidences.ndim != 1 or confidences.shape != (n,):
            return Result.failure(reason=f"confidences must be a 1D array of shape ({n},)")

        if np.any((confidences < 0.0) | (confidences > 1.0)):
            return Result.failure(reason="confidences must be between 0.0 and 1.0")

        try:
            # 1. P matrix (Identity matrix representation)
            p_matrix = np.eye(n)

            # 2. Q vector (Views)
            q_vector = views

            # 3. Omega matrix (Uncertainty of views)
            omega = np.diag([max(1e-6, (1.0 - c) * 0.1) for c in confidences])

            # 4. Black-Litterman expected returns calculation (mu_bl)
            tau_sigma_inv = np.linalg.inv(self.tau * cov_matrix)
            p_omega_inv_p = p_matrix.T @ np.linalg.inv(omega) @ p_matrix

            posterior_cov = np.linalg.inv(tau_sigma_inv + p_omega_inv_p)
            mu_bl = posterior_cov @ (
                tau_sigma_inv @ prior_returns + p_matrix.T @ np.linalg.inv(omega) @ q_vector
            )

            # 5. Mean-Variance Optimization objective function
            def obj(weights: FloatArray) -> float:
                port_ret = weights.T @ mu_bl
                port_vol = np.sqrt(weights.T @ cov_matrix @ weights)
                return float(-(port_ret - (self.delta / 2.0) * (port_vol**2)))

            constraints = [{"type": "eq", "fun": lambda x: np.sum(x) - 1.0}]
            bounds = [(0.0, 0.4) for _ in range(n)]

            initial_weights = np.ones(n) / n
            res = minimize(
                obj,
                initial_weights,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
            )

            weights = res.x if res.success else initial_weights

            return Result.success(
                PortfolioOptimizationReport(
                    tickers=tickers,
                    weights={t: float(w) for t, w in zip(tickers, weights, strict=True)},
                    expected_return=float(weights.T @ mu_bl),
                    expected_volatility=float(np.sqrt(weights.T @ cov_matrix @ weights)),
                )
            )
        except Exception as e:
            logger.error(f"Black-Litterman optimization failed: {e}")
            return Result.failure(reason=f"Black-Litterman optimization failed: {e}")
