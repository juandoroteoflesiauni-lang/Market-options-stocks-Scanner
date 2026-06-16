from __future__ import annotations
from typing import Any
"""Barra-style factor loadings and exposure limits for Scanner portfolio risk (Point 6)."""


import math

from backend.domain.market_scanner_models import MarketScannerRow, ScannerRiskStackFactorLimits

_FACTOR_KEYS = ("momentum", "liquidity", "gex", "volatility")


def extract_factor_loadings(row: MarketScannerRow | dict[str, Any]) -> dict[str, float]:
    """Map a scanner row to normalized factor loadings in [-1, 1]."""
    if isinstance(row, MarketScannerRow):
        data = row.model_dump(mode="python")
    else:
        data = dict(row)

    score = float(data.get("scanner_score") or 50.0)
    momentum = max(-1.0, min(1.0, (score - 50.0) / 50.0))
    direction = str(data.get("direction") or "neutral").lower()
    if direction == "bearish":
        momentum = -abs(momentum)
    elif direction == "bullish":
        momentum = abs(momentum)

    rel_vol = _nested_float(data, ("metrics", "relative_volume"))
    liquidity = 0.0
    if rel_vol is not None and rel_vol > 0:
        liquidity = max(-1.0, min(1.0, math.log1p(rel_vol) / math.log1p(5.0) - 0.35))

    gex = 0.0
    overlay = data.get("institutional_overlay")
    if isinstance(overlay, dict):
        net_gex = overlay.get("net_gex") or overlay.get("net_gamma_exposure")
        if net_gex is not None:
            try:
                gex = max(-1.0, min(1.0, float(net_gex) / 1e9))
            except (TypeError, ValueError):
                gex = 0.0

    sparkline = data.get("sparkline")
    vol_loading = 0.0
    if isinstance(sparkline, list) and len(sparkline) >= 3:
        returns = _returns_from_prices(sparkline)
        sample_vol = _sample_std(returns)
        if sample_vol is not None:
            vol_loading = max(-1.0, min(1.0, sample_vol * 25.0))

    tail = data.get("tail_risk")
    if tail is not None:
        try:
            vol_loading = max(vol_loading, min(1.0, float(tail)))
        except (TypeError, ValueError):
            pass

    return {
        "momentum": round(momentum, 4),
        "liquidity": round(liquidity, 4),
        "gex": round(gex, 4),
        "volatility": round(vol_loading, 4),
    }


def compute_factor_exposure(
    weights: dict[str, float],
    loadings_by_symbol: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Gross factor exposure: sum(weight * abs(loading)) per factor."""
    exposure = {key: 0.0 for key in _FACTOR_KEYS}
    for symbol, weight in weights.items():
        if weight <= 0:
            continue
        loads = loadings_by_symbol.get(symbol, {})
        for key in _FACTOR_KEYS:
            loading = float(loads.get(key, 0.0) or 0.0)
            exposure[key] += weight * abs(loading)
    return {key: round(value, 6) for key, value in exposure.items()}


def apply_factor_limits(
    weights: dict[str, float],
    loadings_by_symbol: dict[str, dict[str, float]],
    limits: ScannerRiskStackFactorLimits,
) -> tuple[dict[str, float], list[str]]:
    """Scale weights down when factor gross exposure exceeds limits."""
    warnings: list[str] = []
    adjusted = dict(weights)
    limit_map = {
        "momentum": limits.momentum,
        "liquidity": limits.liquidity,
        "gex": limits.gex,
        "volatility": limits.volatility,
    }
    for _ in range(20):
        exposure = compute_factor_exposure(adjusted, loadings_by_symbol)
        violated = [
            (factor, limit_map[factor], exposure.get(factor, 0.0))
            for factor in limit_map
            if exposure.get(factor, 0.0) > limit_map[factor] + 1e-9
        ]
        if not violated:
            break
        factor, cap, current = max(violated, key=lambda item: item[2] - item[1])
        ratio = cap / max(current, 1e-9)
        contrib = {
            symbol: adjusted[symbol]
            * abs(float(loadings_by_symbol.get(symbol, {}).get(factor, 0.0) or 0.0))
            for symbol in adjusted
        }
        total_contrib = sum(contrib.values()) or 1.0
        for symbol in adjusted:
            loading = abs(float(loadings_by_symbol.get(symbol, {}).get(factor, 0.0) or 0.0))
            if loading <= 0:
                continue
            symbol_scale = 1.0 - (1.0 - ratio) * (contrib[symbol] / total_contrib)
            adjusted[symbol] *= max(0.0, symbol_scale)
        if sum(adjusted.values()) <= 0:
            return {}, warnings
        warnings.append(f"factor_limit:{factor}:{ratio:.4f}")
    return adjusted, warnings


def apply_correlation_penalty_to_weights(
    weights: dict[str, float],
    sparklines_by_symbol: dict[str, list[float]],
) -> tuple[dict[str, float], list[str]]:
    """Reduce weights for symbols highly correlated with the rest of the basket."""
    warnings: list[str] = []
    symbols = [s for s, w in weights.items() if w > 0]
    if len(symbols) < 3:
        return weights, warnings

    returns_map: dict[str, tuple[float, ...]] = {}
    for symbol in symbols:
        prices = sparklines_by_symbol.get(symbol, [])
        rets = _returns_from_prices(prices if isinstance(prices, list) else [])
        if len(rets) >= 3:
            returns_map[symbol] = tuple(rets)

    if len(returns_map) < 3:
        warnings.append("insufficient_sparkline_for_correlation_constraint")
        return weights, warnings

    raw: dict[str, float] = {}
    for symbol in symbols:
        correlations = [
            abs(corr)
            for other, other_rets in returns_map.items()
            if other != symbol
            if (corr := _correlation(returns_map[symbol], other_rets)) is not None
        ]
        avg_corr = sum(correlations) / len(correlations) if correlations else 0.0
        raw[symbol] = weights[symbol] / (1.0 + avg_corr)

    total = sum(raw.values())
    if total <= 0:
        return weights, warnings
    return {symbol: weight / total for symbol, weight in raw.items()}, warnings


def _nested_float(data: dict[str, Any], path: tuple[str, ...]) -> float | None:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    try:
        value = float(current)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _returns_from_prices(prices: list[Any]) -> list[float]:
    values: list[float] = []
    for item in prices:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            values.append(value)
    out: list[float] = []
    for previous, current in zip(values, values[1:], strict=False):
        if previous > 0:
            out.append((current - previous) / previous)
    return out


def _sample_std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    if var <= 0:
        return 0.0
    return math.sqrt(var)


def _correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float | None:
    n = min(len(left), len(right))
    if n < 3:
        return None
    left_values = left[-n:]
    right_values = right[-n:]
    left_mean = sum(left_values) / n
    right_mean = sum(right_values) / n
    cov = sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left_values, right_values, strict=False)
    )
    left_var = sum((a - left_mean) ** 2 for a in left_values)
    right_var = sum((b - right_mean) ** 2 for b in right_values)
    denom = math.sqrt(left_var * right_var)
    if denom <= 0:
        return None
    return cov / denom
