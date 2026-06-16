from __future__ import annotations
from typing import Any


import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import Bounds, minimize
from scipy.spatial.distance import pdist

# MIGRATION: Dependencia de dominio interna
from ..domain.optimization_models import (
    CapmAssetMetrics,
    OptimizedPortfolioStats,
    PortfolioOptimizationResult,
)

# ─────────────────────────────────────────────────────────────────────────────
# MIGRATION : Constantes Institucionales (Thresholds de Optimización)
# ─────────────────────────────────────────────────────────────────────────────
OPTIM_PERIODS_PER_YEAR_DEFAULT: Final[int] = 252
OPTIM_FRONTIER_POINTS_DEFAULT: Final[int] = 20
OPTIM_CONDITION_NUMBER_THRESHOLD: Final[float] = 1e12
OPTIM_NEAR_ZERO_EPSILON: Final[float] = 1e-12
OPTIM_WEIGHT_SUM_TOLERANCE: Final[float] = 1e-4
OPTIM_SHORT_BOUND: Final[float] = -1.0
OPTIM_LONG_BOUND: Final[float] = 1.0

_FRONTIER_FTOL: Final[float] = 1e-12
_FRONTIER_MAXITER: Final[int] = 1000


class PortfolioOptimizationEngine:
    """Stateless Markowitz plus CAPM optimization engine."""

    @staticmethod
    def optimize(
        returns_df: pd.DataFrame,
        risk_free_rate: float,
        market_col: str = "SPY",
        periods_per_year: int = OPTIM_PERIODS_PER_YEAR_DEFAULT,
        frontier_points: int = OPTIM_FRONTIER_POINTS_DEFAULT,
    ) -> PortfolioOptimizationResult | None:
        """Runs the full Markowitz + CAPM suite."""
        try:
            warnings_log: list[str] = []
            clean_df = PortfolioOptimizationEngine._validate_and_clean(
                returns_df, market_col, warnings_log
            )
            if clean_df is None:
                return None

            asset_columns = [col for col in clean_df.columns if col != market_col]
            n_assets = len(asset_columns)
            if n_assets == 0:
                return None

            asset_returns = clean_df[asset_columns]
            market_returns = clean_df[market_col]

            # Covariance and Correlation
            cov_matrix_raw = np.atleast_2d(np.cov(asset_returns.values.T))
            cov_annualized = cov_matrix_raw * float(periods_per_year)
            std_vector = np.sqrt(np.clip(np.diag(cov_annualized), 0.0, None))
            denom = np.outer(std_vector, std_vector)
            corr_matrix = np.divide(
                cov_annualized, denom, out=np.zeros_like(cov_annualized), where=denom > 0.0
            )
            np.clip(corr_matrix, -1.0, 1.0, out=corr_matrix)

            covariance_df = pd.DataFrame(cov_annualized, index=asset_columns, columns=asset_columns)
            correlation_df = pd.DataFrame(corr_matrix, index=asset_columns, columns=asset_columns)

            expected_returns = asset_returns.mean().to_numpy(dtype=np.float64) * float(
                periods_per_year
            )

            # Optimization Layers
            inverse_covariance, inversion_valid, inversion_warning = (
                PortfolioOptimizationEngine._safe_invert(cov_annualized, n_assets)
            )
            if inversion_warning:
                warnings_log.append(inversion_warning)

            tangency_weights, tangency_valid = PortfolioOptimizationEngine._tangency_portfolio(
                inverse_covariance, expected_returns, risk_free_rate, n_assets, warnings_log
            )
            min_variance_weights, min_variance_valid = (
                PortfolioOptimizationEngine._min_variance_portfolio(
                    cov_annualized, n_assets, warnings_log
                )
            )

            tangency_stats = PortfolioOptimizationEngine._portfolio_stats(
                tangency_weights, expected_returns, cov_annualized, risk_free_rate, asset_columns
            )
            min_variance_stats = PortfolioOptimizationEngine._portfolio_stats(
                min_variance_weights,
                expected_returns,
                cov_annualized,
                risk_free_rate,
                asset_columns,
            )

            capm_metrics = PortfolioOptimizationEngine._compute_capm(
                asset_returns,
                market_returns,
                risk_free_rate,
                periods_per_year,
                asset_columns,
                warnings_log,
            )
            efficient_frontier = PortfolioOptimizationEngine._compute_efficient_frontier(
                expected_returns,
                cov_annualized,
                risk_free_rate,
                asset_columns,
                frontier_points,
                warnings_log,
            )

            return PortfolioOptimizationResult(
                covariance_matrix=covariance_df,
                correlation_matrix=correlation_df,
                min_variance_portfolio=min_variance_stats,
                tangency_portfolio=tangency_stats,
                capm_metrics=capm_metrics,
                efficient_frontier=efficient_frontier,
                is_valid_optimization=inversion_valid and tangency_valid and min_variance_valid,
                warnings=warnings_log,
            )
        except Exception:
            return None

    @staticmethod
    def calculate_hrp_weights(cov_matrix: pd.DataFrame) -> dict[str, float]:
        """Hierarchical Risk Parity (HRP) Algorithm."""
        try:
            tickers = cov_matrix.columns.tolist()
            V = cov_matrix.values
            D = PortfolioOptimizationEngine._corr_dist(V)
            link = linkage(D, method="single")
            sort_idx = leaves_list(link)
            sorted_tickers = [tickers[i] for i in sort_idx]

            weights = pd.Series(1.0, index=sorted_tickers)
            items = [sorted_tickers]
            while len(items) > 0:
                items = [
                    i[start:end]
                    for i in items
                    for start, end in ((0, len(i) // 2), (len(i) // 2, len(i)))
                    if len(i) > 1
                ]
                for i in range(0, len(items), 2):
                    c_left, c_right = items[i], items[i + 1]
                    alpha = PortfolioOptimizationEngine._get_cluster_var(
                        cov_matrix, c_left, c_right
                    )
                    weights[c_left] *= alpha
                    weights[c_right] *= 1 - alpha

            return weights.to_dict()
        except Exception:
            return {t: 1.0 / len(cov_matrix) for t in cov_matrix.columns}

    @staticmethod
    def apply_black_litterman(
        cov_matrix: np.ndarray[Any, Any],
        market_prior: np.ndarray[Any, Any],
        views: np.ndarray[Any, Any],
        p_matrix: np.ndarray[Any, Any],
        omega: np.ndarray[Any, Any],
        tau: float = 0.05,
    ) -> np.ndarray[Any, Any]:
        """Black-Litterman Master Formula implementation."""
        try:
            inv_tau_sigma = np.linalg.inv(tau * cov_matrix)
            inv_omega = np.linalg.inv(omega)
            term1 = np.linalg.inv(inv_tau_sigma + p_matrix.T @ inv_omega @ p_matrix)
            term2 = inv_tau_sigma @ market_prior + p_matrix.T @ inv_omega @ views
            return term1 @ term2
        except Exception:
            return market_prior

    # ─────────────────────────────────────────────────────────────────────────────
    # INTERNAL CALCULATORS
    # ─────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_and_clean(
        df: pd.DataFrame, market_col: str, log: list[str]
    ) -> pd.DataFrame | None:
        if market_col not in df.columns:
            return None
        clean_df = df.dropna().copy()
        if len(clean_df) < len(clean_df.columns) + 2:
            log.append("Low sample size detected (T < N + 2).")
        return clean_df if len(clean_df) > 0 else None

    @staticmethod
    def _safe_invert(cov: np.ndarray[Any, Any], n: int) -> tuple[np.ndarray[Any, Any], bool, str | None]:
        try:
            inverse = np.linalg.inv(cov)
            cond = np.linalg.cond(cov)
            if cond > OPTIM_CONDITION_NUMBER_THRESHOLD:
                return (
                    np.linalg.pinv(cov),
                    False,
                    f"Ill-conditioned matrix (cond={cond:.2e}); using pinv.",
                )
            return inverse, True, None
        except np.linalg.LinAlgError:
            return np.linalg.pinv(cov), False, "Singular matrix; using pinv."

    @staticmethod
    def _tangency_portfolio(
        inv_cov: np.ndarray[Any, Any], expected_returns: np.ndarray[Any, Any], rf: float, n: int, log: list[str]
    ) -> tuple[np.ndarray[Any, Any], bool]:
        excess = expected_returns - rf
        z = inv_cov @ excess
        sum_z = float(np.sum(z))
        if abs(sum_z) < OPTIM_NEAR_ZERO_EPSILON:
            return np.full(n, 1.0 / n), False
        return z / sum_z, True

    @staticmethod
    def _min_variance_portfolio(cov: np.ndarray[Any, Any], n: int, log: list[str]) -> tuple[np.ndarray[Any, Any], bool]:
        ones = np.ones((n, 1))
        c_star = np.block([[cov, ones], [ones.T, np.zeros((1, 1))]])
        b_star = np.zeros(n + 1)
        b_star[-1] = 1.0
        try:
            x_star = np.linalg.solve(c_star, b_star)
            weights = x_star[:n]
            return weights, abs(float(np.sum(weights)) - 1.0) < OPTIM_WEIGHT_SUM_TOLERANCE
        except np.linalg.LinAlgError:
            return np.full(n, 1.0 / n), False

    @staticmethod
    def _portfolio_stats(
        weights: np.ndarray[Any, Any],
        expected_returns: np.ndarray[Any, Any],
        cov: np.ndarray[Any, Any],
        rf: float,
        tickers: list[str],
    ) -> OptimizedPortfolioStats:
        ret = float(weights @ expected_returns)
        vol = float(np.sqrt(max(float(weights @ cov @ weights), 0.0)))
        sharpe = (ret - rf) / vol if vol > OPTIM_NEAR_ZERO_EPSILON else 0.0
        return OptimizedPortfolioStats(
            weights={t: float(w) for t, w in zip(tickers, weights, strict=False)},
            expected_return=ret,
            volatility=vol,
            sharpe_ratio=sharpe,
        )

    @staticmethod
    def _compute_capm(
        assets_df: pd.DataFrame,
        market_s: pd.Series,
        rf_annual: float,
        T: int,
        tickers: list[str],
        log: list[str],
    ) -> dict[str, CapmAssetMetrics]:
        if len(assets_df) < 2:
            return {
                t: CapmAssetMetrics(
                    beta=0.0, expected_return_capm=rf_annual, alpha_jensen=0.0, r_squared=0.0
                )
                for t in tickers
            }
        assets, market = assets_df.values, market_s.values
        var_market = float(np.var(market, ddof=1))
        betas = (
            (assets.T @ (market - market.mean())) / (float(len(market) - 1) * var_market)
            if var_market > OPTIM_NEAR_ZERO_EPSILON
            else np.zeros(len(tickers))
        )

        annual_market_ret = float(market.mean()) * T
        expected_capm = rf_annual + betas * (annual_market_ret - rf_annual)
        alphas = (assets.mean(axis=0) * T) - expected_capm

        return {
            t: CapmAssetMetrics(
                beta=float(betas[i]),
                expected_return_capm=float(expected_capm[i]),
                alpha_jensen=float(alphas[i]),
                r_squared=0.0,
            )
            for i, t in enumerate(tickers)
        }

    @staticmethod
    def _compute_efficient_frontier(
        expected_returns: np.ndarray[Any, Any],
        cov: np.ndarray[Any, Any],
        rf: float,
        tickers: list[str],
        points: int,
        log: list[str],
    ) -> pd.DataFrame:
        n = len(expected_returns)
        if n == 0:
            return pd.DataFrame()
        targets = np.linspace(
            float(np.min(expected_returns)), float(np.max(expected_returns)), max(1, points)
        )
        records = []
        w0 = np.full(n, 1.0 / n)
        bounds = Bounds(OPTIM_SHORT_BOUND, OPTIM_LONG_BOUND)

        for target in targets:
            cons = [
                {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
                {"type": "eq", "fun": lambda w: w @ expected_returns - target},
            ]
            res = minimize(
                lambda w: float(w @ cov @ w),
                x0=w0,
                method="SLSQP",
                bounds=bounds,
                constraints=cons,
                options={"ftol": _FRONTIER_FTOL, "maxiter": _FRONTIER_MAXITER},
            )
            if res.success:
                vol = float(np.sqrt(max(float(res.fun), 0.0)))
                row = {
                    "Return": target,
                    "Volatility": vol,
                    "Sharpe": (target - rf) / vol if vol > OPTIM_NEAR_ZERO_EPSILON else 0.0,
                }
                row.update({t: float(res.x[i]) for i, t in enumerate(tickers)})
                records.append(row)
                w0 = res.x

        return pd.DataFrame(records)

    @staticmethod
    def _corr_dist(V: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
        std = np.sqrt(np.diag(V))
        corr = V / np.outer(std, std)
        return pdist(np.sqrt(np.clip(0.5 * (1 - corr), 0.0, 1.0)))

    @staticmethod
    def _get_cluster_var(cov: pd.DataFrame, c_left: list[str], c_right: list[str]) -> float:
        v_l = PortfolioOptimizationEngine._get_ivp_var(cov.loc[c_left, c_left])
        v_r = PortfolioOptimizationEngine._get_ivp_var(cov.loc[c_right, c_right])
        return 1 - v_l / (v_l + v_r)

    @staticmethod
    def _get_ivp_var(cov: pd.DataFrame) -> float:
        ivp = 1.0 / np.diag(cov.values)
        ivp /= ivp.sum()
        return float(ivp @ cov.values @ ivp)


def format_portfolio_optimization_summary(result: PortfolioOptimizationResult) -> str:
    lines = ["=" * 70, "  QuantumAnalyzer - Portfolio Optimization Suite  ", "=" * 70]
    lines.append(f"  Valid: {result.is_valid_optimization}")
    if result.warnings:
        lines.append("  Warnings:")
        for w in result.warnings:
            lines.append(f"   - {w}")
    lines.append(f"\n  Tangency Sharpe: {result.tangency_portfolio.sharpe_ratio:.4f}")
    lines.append(f"  Min-Var Volatility: {result.min_variance_portfolio.volatility*100:.2f}%")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: PORTAFOLIO
# Archivo         : portfolio_optimization.py
# Sub-capa        : Engines
# Solver/Optimizer: Markowitz, HRP, Black-Litterman
# Eliminado       : Import de quantumbeta constants.
# Preservado      : Todas las fórmulas matriciales de optimización.
# Pendientes      : Pruebas de integración con optimización multi-objetivo.
# ────────────────────────────────────────────────────────────────────
