"""Factor covariance estimation and portfolio risk attribution (Barra Point 2)."""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime
from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    BarraAssetClass,
    BarraFactorCovariance,
    BarraFactorExposure,
)

logger = get_logger(__name__)

DEFAULT_HALFLIFE_DAYS = 60
SHRINKAGE_ALPHA = 0.35


def barra_covariance_halflife() -> int:
    raw = os.getenv("SCANNER_BARRA_COVARIANCE_HALFLIFE", str(DEFAULT_HALFLIFE_DAYS))
    try:
        return max(5, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_HALFLIFE_DAYS


def _prior_covariance(factor_names: list[str]) -> list[list[float]]:
    """Institutional prior factor covariance (annualized), diagonal-dominant."""
    n = len(factor_names)
    if n == 0:
        return []
    diag_defaults: dict[str, float] = {
        "market": 0.04,
        "momentum": 0.025,
        "volatility": 0.02,
        "liquidity": 0.015,
        "size": 0.012,
        "value": 0.01,
        "quality": 0.01,
        "gex_positioning": 0.018,
        "funding_basis": 0.022,
        "onchain_activity": 0.015,
    }
    matrix = [[0.0] * n for _ in range(n)]
    for i, name in enumerate(factor_names):
        base = 0.008
        for key, var in diag_defaults.items():
            if name.startswith(key) or name == key:
                base = var
                break
        if name.startswith("sector_") or name.startswith("category_"):
            base = 0.006
        matrix[i][i] = base
    return matrix


def ledoit_wolf_shrink_sample(
    sample: list[list[float]],
    *,
    alpha: float = SHRINKAGE_ALPHA,
) -> list[list[float]]:
    """Shrink sample covariance toward diagonal (Ledoit-Wolf lite)."""
    n = len(sample)
    if n == 0:
        return []
    target = [[0.0] * n for _ in range(n)]
    for i in range(n):
        target[i][i] = sample[i][i] if i < len(sample) else 0.0
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            out[i][j] = (1.0 - alpha) * sample[i][j] + alpha * target[i][j]
    return out


def estimate_factor_covariance(
    exposures: list[BarraFactorExposure],
    *,
    asset_class: BarraAssetClass | None = None,
    half_life_days: int | None = None,
) -> BarraFactorCovariance:
    """Build factor covariance from cross-sectional exposure dispersion + prior."""
    hl = half_life_days or barra_covariance_halflife()
    filtered = [e for e in exposures if asset_class is None or e.asset_class == asset_class]
    if not filtered:
        return BarraFactorCovariance(half_life_days=hl)

    factor_names = sorted({name for exp in filtered for name in exp.factors})
    if not factor_names:
        return BarraFactorCovariance(half_life_days=hl)

    n = len(factor_names)
    sample = [[0.0] * n for _ in range(n)]
    values_by_factor: dict[str, list[float]] = {name: [] for name in factor_names}

    for exp in filtered:
        for name, value in exp.factors.items():
            if name in values_by_factor:
                values_by_factor[name].append(float(value))

    for i, fi in enumerate(factor_names):
        xi = values_by_factor[fi]
        if len(xi) < 2:
            continue
        mean_i = sum(xi) / len(xi)
        var_i = sum((x - mean_i) ** 2 for x in xi) / max(len(xi) - 1, 1)
        sample[i][i] = max(var_i * 0.01, 1e-6)
        for j in range(i + 1, n):
            fj = factor_names[j]
            xj = values_by_factor[fj]
            if len(xj) < 2:
                continue
            mean_j = sum(xj) / len(xj)
            cov_ij = sum(
                (xi[k] - mean_i) * (xj[k] - mean_j) for k in range(min(len(xi), len(xj)))
            ) / max(min(len(xi), len(xj)) - 1, 1)
            sample[i][j] = sample[j][i] = cov_ij * 0.005

    prior = _prior_covariance(factor_names)
    blended = [
        [
            (
                0.7 * prior[i][j] + 0.3 * sample[i][j]
                if i < len(prior) and j < len(prior[i])
                else sample[i][j]
            )
            for j in range(n)
        ]
        for i in range(n)
    ]
    shrunk = ledoit_wolf_shrink_sample(blended)

    return BarraFactorCovariance(
        factor_names=factor_names,
        matrix=shrunk,
        half_life_days=hl,
        as_of=datetime.now(UTC),
        asset_class=asset_class,
    )


def estimate_specific_risk(
    row_sparkline: list[float],
    *,
    factor_explained_var: float = 0.0,
) -> float:
    """Annualized specific risk fraction from residual vol after factors."""
    if len(row_sparkline) < 5:
        return max(0.05, 0.25 - factor_explained_var * 0.1)
    rets: list[float] = []
    for prev, cur in zip(row_sparkline, row_sparkline[1:], strict=False):
        if prev > 0 and cur > 0:
            rets.append((cur - prev) / prev)
    if len(rets) < 3:
        return 0.15
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    vol = math.sqrt(max(var, 0.0)) * math.sqrt(252.0)
    residual = max(0.02, vol * max(0.25, 1.0 - min(factor_explained_var, 0.85)))
    return round(min(1.0, residual), 6)


def portfolio_factor_exposures(
    weights: dict[str, float],
    exposures_by_symbol: dict[str, BarraFactorExposure],
) -> dict[str, float]:
    """Weighted sum of factor exposures: f_k = sum_i w_i * x_ik."""
    out: dict[str, float] = {}
    for symbol, weight in weights.items():
        if weight <= 0:
            continue
        exp = exposures_by_symbol.get(symbol)
        if not exp:
            continue
        for name, loading in exp.factors.items():
            out[name] = out.get(name, 0.0) + weight * float(loading)
    return {k: round(v, 6) for k, v in out.items()}


def factor_risk_variance(
    factor_portfolio: dict[str, float],
    cov: BarraFactorCovariance,
) -> float:
    """f' Sigma_f f for portfolio factor vector."""
    if not cov.factor_names or not cov.matrix:
        return 0.0
    idx = {name: i for i, name in enumerate(cov.factor_names)}
    n = len(idx)
    vec = [factor_portfolio.get(name, 0.0) for name in cov.factor_names]
    total = 0.0
    for i in range(n):
        for j in range(n):
            if i < len(cov.matrix) and j < len(cov.matrix[i]):
                total += vec[i] * cov.matrix[i][j] * vec[j]
    return max(0.0, total)


def risk_contribution_decomposition(
    weights: dict[str, float],
    exposures_by_symbol: dict[str, BarraFactorExposure],
    cov: BarraFactorCovariance,
    specific_risk_by_symbol: dict[str, float],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Return (factor_risk_pct_by_factor, marginal_risk_by_symbol, specific_pct_by_symbol)."""
    f_port = portfolio_factor_exposures(weights, exposures_by_symbol)
    factor_var = factor_risk_variance(f_port, cov)
    specific_var = sum(
        (weights.get(s, 0.0) ** 2) * (specific_risk_by_symbol.get(s, 0.15) ** 2) for s in weights
    )
    total_var = factor_var + specific_var
    if total_var <= 1e-12:
        return {}, {}, {}

    factor_pct: dict[str, float] = {}
    if factor_var > 0 and cov.factor_names:
        for name in cov.factor_names:
            loading = f_port.get(name, 0.0)
            idx = cov.factor_names.index(name)
            contrib = 0.0
            for j, fname in enumerate(cov.factor_names):
                if idx < len(cov.matrix) and j < len(cov.matrix[idx]):
                    contrib += loading * cov.matrix[idx][j] * f_port.get(fname, 0.0)
            factor_pct[name] = round(max(0.0, contrib / total_var), 6)

    marginal: dict[str, float] = {}
    for symbol, weight in weights.items():
        if weight <= 0:
            continue
        exp = exposures_by_symbol.get(symbol)
        if not exp:
            marginal[symbol] = 0.0
            continue
        beta_var = 0.0
        for name, loading in exp.factors.items():
            if name not in cov.factor_names:
                continue
            i = cov.factor_names.index(name)
            for j, fname in enumerate(cov.factor_names):
                if i < len(cov.matrix) and j < len(cov.matrix[i]):
                    beta_var += loading * cov.matrix[i][j] * f_port.get(fname, 0.0)
        spec = specific_risk_by_symbol.get(symbol, 0.15) ** 2
        marginal[symbol] = round(
            weight * (beta_var + spec) / max(total_var, 1e-12),
            6,
        )

    spec_pct = {
        s: round(
            (weights.get(s, 0.0) ** 2)
            * (specific_risk_by_symbol.get(s, 0.15) ** 2)
            / max(total_var, 1e-12),
            6,
        )
        for s in weights
        if weights.get(s, 0.0) > 0
    }
    return factor_pct, marginal, spec_pct


def enforce_factor_risk_budget(
    weights: dict[str, float],
    exposures_by_symbol: dict[str, BarraFactorExposure],
    cov: BarraFactorCovariance,
    specific_risk_by_symbol: dict[str, float],
    budget: dict[str, float],
) -> tuple[dict[str, float], list[str]]:
    """Scale weights down when factor risk contribution exceeds budget caps."""
    warnings: list[str] = []
    adjusted = dict(weights)
    for _ in range(12):
        factor_pct, _, _ = risk_contribution_decomposition(
            adjusted, exposures_by_symbol, cov, specific_risk_by_symbol
        )
        breach_scale = 1.0
        for factor, cap in budget.items():
            current = factor_pct.get(factor, 0.0)
            if current > cap + 1e-6 and current > 0:
                breach_scale = min(breach_scale, cap / current)
        if breach_scale >= 1.0 - 1e-9:
            break
        warnings.append(f"barra_risk_budget_scale:{breach_scale:.4f}")
        adjusted = {s: w * breach_scale for s, w in adjusted.items()}
    return adjusted, warnings


def skfolio_riskfolio_inputs(
    cov: BarraFactorCovariance,
    exposures_by_symbol: dict[str, BarraFactorExposure],
    symbols: list[str],
) -> dict[str, Any]:
    """Package covariance + exposures for external optimizers (skfolio/riskfolio)."""
    return {
        "factor_names": list(cov.factor_names),
        "factor_covariance": cov.matrix,
        "asset_factor_exposure": {
            sym: dict(exposures_by_symbol[sym].factors)
            for sym in symbols
            if sym in exposures_by_symbol
        },
        "half_life_days": cov.half_life_days,
    }
