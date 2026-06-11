"""
backend/layer_3_specialists/ia_probabilistico/engines/portfolio_optimizer.py
════════════════════════════════════════════════════════════════════════════════
Portfolio Optimizer — implementing Black-Litterman with Probabilistic Views.
════════════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import Any

import numpy as np
from scipy.optimize import minimize  # type: ignore[import-not-found, import-untyped]

logger = logging.getLogger(__name__)


class BlackLittermanOptimizer:
    """
    Combines market equilibrium (Prior) with QuantumAnalyzer Predictive Views
    to generate optimal portfolio weights.
    """

    def __init__(self, risk_aversion: float = 2.5, tau: float = 0.05):
        self.delta = risk_aversion  # Risk aversion coefficient
        self.tau = tau  # Scaling factor for prior

    def optimize(
        self,
        tickers: list[str],
        cov_matrix: np.ndarray[Any, np.dtype[Any]],
        prior_returns: np.ndarray[Any, np.dtype[Any]],
        views: np.ndarray[Any, np.dtype[Any]],
        confidences: np.ndarray[Any, np.dtype[Any]],
    ) -> dict[str, Any]:
        """
        Runs the Black-Litterman optimization.

        Args:
            tickers: List of symbols.
            cov_matrix: NxN covariance matrix of returns.
            prior_returns: N vector of equilibrium returns (CAPM).
            views: N vector of expected returns from Predictive Engine.
            confidences: N vector of confidences (0 to 1).
        """
        n = len(tickers)
        try:
            # 1. P matrix (Identity if 1 view per asset)
            P = np.eye(n)

            # 2. Q vector (Views)
            Q = views

            # 3. Omega matrix (Uncertainty of views)
            # Higher confidence -> lower omega
            omega = np.diag([max(1e-6, (1.0 - c) * 0.1) for c in confidences])

            # 4. Black-Litterman formula for posterior expected returns (mu_bl)
            tau_sigma_inv = np.linalg.inv(self.tau * cov_matrix)
            p_omega_inv_p = P.T @ np.linalg.inv(omega) @ P

            posterior_cov = np.linalg.inv(tau_sigma_inv + p_omega_inv_p)
            mu_bl = posterior_cov @ (tau_sigma_inv @ prior_returns + P.T @ np.linalg.inv(omega) @ Q)

            # 5. Mean-Variance Optimization using mu_bl
            def obj(weights: np.ndarray[Any, np.dtype[Any]]) -> float:
                port_ret = weights.T @ mu_bl
                port_vol = np.sqrt(weights.T @ cov_matrix @ weights)
                # Maximize Utility: Ret - (delta/2)*Var
                return float(-(port_ret - (self.delta / 2.0) * (port_vol**2)))

            constraints = [{"type": "eq", "fun": lambda x: np.sum(x) - 1.0}]  # Fully invested
            bounds = [(0.0, 0.4) for _ in range(n)]  # Max 40% per asset for diversification

            initial_weights = np.ones(n) / n
            res = minimize(
                obj, initial_weights, method="SLSQP", bounds=bounds, constraints=constraints
            )

            weights = res.x if res.success else initial_weights

            return {
                "tickers": tickers,
                "weights": {t: float(w) for t, w in zip(tickers, weights, strict=False)},
                "expected_return": float(weights.T @ mu_bl),
                "expected_volatility": float(np.sqrt(weights.T @ cov_matrix @ weights)),
                "success": bool(res.success),
            }

        except Exception as e:
            logger.error(f"Black-Litterman optimization failed: {e}")
            return {"error": str(e), "success": False}


def calculate_covariance(
    returns_dict: dict[str, np.ndarray[Any, np.dtype[Any]]]
) -> tuple[list[str], np.ndarray[Any, np.dtype[Any]]]:
    """Calculates the covariance matrix for a group of returns."""
    tickers = list(returns_dict.keys())
    # Align lengths
    min_len = min(len(r) for r in returns_dict.values())
    data = np.array([r[-min_len:] for r in returns_dict.values()])
    cov = np.cov(data) * 252.0  # Annualized
    return tickers, cov
