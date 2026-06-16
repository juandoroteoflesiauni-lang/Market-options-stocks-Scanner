from __future__ import annotations
from typing import Any
"""Non-binding basket optimizer for Market Scanner leaders.

The optimizer is deliberately separate from scanner scoring and funding gates:
it produces portfolio diagnostics only and never authorizes trades.
"""


import math
import os
from dataclasses import dataclass

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerRow,
    ScannerPortfolioOptimizeRequest,
    ScannerPortfolioOptimizeResponse,
    ScannerPortfolioWeight,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class _Candidate:
    symbol: str
    sparkline: tuple[float, ...]
    volatility: float | None
    returns: tuple[float, ...]
    scanner_score: float | None = None


def optimize_scanner_portfolio(
    request: ScannerPortfolioOptimizeRequest,
) -> ScannerPortfolioOptimizeResponse:
    """Optimize Scanner leaders under simple long-only concentration constraints."""
    engine = _selected_engine()
    mode = str(request.constraints.get("risk_budget_mode", "inverse_vol_from_sparkline"))
    if engine in {"skfolio", "riskfolio"}:
        barra_payload = _barra_external_payload(request)
        return ScannerPortfolioOptimizeResponse(
            engine=engine,
            status="unavailable",
            weights=[],
            risk_contribution={},
            warnings=[
                f"{engine} backend is not installed; set SCANNER_PORTFOLIO_OPTIMIZER=internal",
                *(["barra_covariance_attached_for_external_engine"] if barra_payload else []),
            ],
            risk_budget_mode=mode,
            risk_model=barra_payload,
        )
    if engine != "internal":
        logger.warning("scanner_portfolio_optimizer.unknown_engine engine=%s", engine)
        engine = "internal"

    return _optimize_internal(request)


def _optimize_internal(
    request: ScannerPortfolioOptimizeRequest,
) -> ScannerPortfolioOptimizeResponse:
    mode = str(request.constraints.get("risk_budget_mode", "inverse_vol_from_sparkline"))
    max_weight = _constraint_float(request.constraints, "max_weight", 1.0)
    min_weight = _constraint_float(request.constraints, "min_weight", 0.0)
    long_only = bool(request.constraints.get("long_only", True))
    warnings: list[str] = []
    if not long_only:
        warnings.append("short_weights_unavailable_internal_engine")

    candidates = _extract_candidates(request)
    if not candidates:
        return ScannerPortfolioOptimizeResponse(
            engine="internal",
            status="unavailable",
            weights=[],
            risk_contribution={},
            warnings=["no_valid_symbols", *warnings],
            risk_budget_mode=mode,
        )

    if mode == "barra_risk_budget":
        return _optimize_barra_risk_budget(request, candidates, min_weight, max_weight, warnings)
    if mode == "equal_weight":
        raw_weights = {candidate.symbol: 1.0 for candidate in candidates}
        status = "ok"
    elif mode == "score_weighted":
        raw_weights, mode_warnings = _score_weighted_weights(candidates, request)
        warnings.extend(mode_warnings)
        status = "degraded" if mode_warnings else "ok"
    elif mode == "correlation_penalty":
        raw_weights, mode_warnings = _correlation_penalty_weights(candidates)
        warnings.extend(mode_warnings)
        status = "degraded" if mode_warnings else "ok"
    else:
        raw_weights, mode_warnings = _inverse_vol_weights(candidates)
        warnings.extend(mode_warnings)
        status = "degraded" if mode_warnings else "ok"

    bounded, bound_warnings = _apply_constraints(raw_weights, min_weight, max_weight)
    warnings.extend(bound_warnings)
    if bound_warnings and status == "ok":
        status = "degraded"
    risk_contribution = _risk_contribution(candidates, bounded)
    weights = [
        ScannerPortfolioWeight(
            symbol=candidate.symbol,
            weight=bounded.get(candidate.symbol, 0.0),
            risk_contribution=risk_contribution.get(candidate.symbol, 0.0),
            volatility=candidate.volatility,
        )
        for candidate in candidates
        if bounded.get(candidate.symbol, 0.0) > 0
    ]
    return ScannerPortfolioOptimizeResponse(
        engine="internal",
        status=status,
        weights=weights,
        risk_contribution=risk_contribution,
        warnings=warnings,
        risk_budget_mode=mode,
    )


def _selected_engine() -> str:
    return os.getenv("SCANNER_PORTFOLIO_OPTIMIZER", "internal").strip().lower() or "internal"


def _rows_from_request(request: ScannerPortfolioOptimizeRequest) -> list[MarketScannerRow]:
    return list(request.rows)


def _barra_external_payload(
    request: ScannerPortfolioOptimizeRequest,
) -> Any:
    from backend.services.scanner_barra_covariance import skfolio_riskfolio_inputs
    from backend.services.scanner_barra_factor_model import compute_barra_risk_model

    rows = _rows_from_request(request)
    if not rows:
        return None
    model = compute_barra_risk_model(rows)
    if not model.covariance:
        return model
    by_symbol = {e.symbol: e for e in model.exposures}
    skfolio_riskfolio_inputs(
        model.covariance,
        by_symbol,
        [e.symbol for e in model.exposures],
    )
    return model


def _optimize_barra_risk_budget(
    request: ScannerPortfolioOptimizeRequest,
    candidates: list[_Candidate],
    min_weight: float,
    max_weight: float,
    base_warnings: list[str],
) -> ScannerPortfolioOptimizeResponse:
    from backend.services.scanner_barra_covariance import (
        enforce_factor_risk_budget,
        risk_contribution_decomposition,
    )
    from backend.services.scanner_barra_factor_model import (
        apply_barra_to_rows,
        compute_barra_risk_model,
        default_factor_risk_budget,
    )

    warnings = list(base_warnings)
    rows = _rows_from_request(request)
    if not rows:
        warnings.append("barra_mode_requires_market_scanner_rows")
        return ScannerPortfolioOptimizeResponse(
            engine="internal",
            status="unavailable",
            weights=[],
            risk_contribution={},
            warnings=warnings,
            risk_budget_mode="barra_risk_budget",
        )

    apply_barra_to_rows(rows)
    raw_weights, inv_warnings = _inverse_vol_weights(candidates)
    warnings.extend(inv_warnings)
    bounded, bound_warnings = _apply_constraints(raw_weights, min_weight, max_weight)
    warnings.extend(bound_warnings)

    budget_raw = request.constraints.get("factor_risk_budget")
    budget = (
        {str(k): float(v) for k, v in budget_raw.items()}
        if isinstance(budget_raw, dict)
        else default_factor_risk_budget()
    )

    risk_model = compute_barra_risk_model(rows, weights=bounded, factor_risk_budget=budget)
    by_symbol = {e.symbol: e for e in risk_model.exposures}
    cov = risk_model.covariance
    if cov is None:
        warnings.append("barra_covariance_unavailable")
        return ScannerPortfolioOptimizeResponse(
            engine="internal",
            status="degraded",
            weights=[],
            risk_contribution={},
            warnings=warnings,
            risk_budget_mode="barra_risk_budget",
            risk_model=risk_model,
        )

    specific_by_symbol = risk_model.specific_risk_by_symbol
    adjusted, budget_warnings = enforce_factor_risk_budget(
        bounded,
        by_symbol,
        cov,
        specific_by_symbol,
        budget,
    )
    warnings.extend(budget_warnings)
    adjusted, _ = _apply_constraints(adjusted, min_weight, max_weight)

    factor_pct, marginal, spec_pct = risk_contribution_decomposition(
        adjusted,
        by_symbol,
        cov,
        specific_by_symbol,
    )
    vol_by_symbol = {c.symbol: c.volatility for c in candidates}
    risk_contribution = _risk_contribution(candidates, adjusted)
    weights_out: list[ScannerPortfolioWeight] = []
    for candidate in candidates:
        w = adjusted.get(candidate.symbol, 0.0)
        if w <= 0:
            continue
        weights_out.append(
            ScannerPortfolioWeight(
                symbol=candidate.symbol,
                weight=w,
                risk_contribution=risk_contribution.get(candidate.symbol, 0.0),
                volatility=vol_by_symbol.get(candidate.symbol),
                factor_risk_contribution=(
                    sum(
                        abs(by_symbol[candidate.symbol].factors.get(f, 0.0))
                        * factor_pct.get(f, 0.0)
                        for f in factor_pct
                    )
                    if candidate.symbol in by_symbol
                    else 0.0
                ),
                specific_risk_contribution=spec_pct.get(candidate.symbol, 0.0),
                marginal_risk_contribution=marginal.get(candidate.symbol, 0.0),
            )
        )

    status = "degraded" if warnings else "ok"
    if budget_warnings:
        status = "degraded"

    return ScannerPortfolioOptimizeResponse(
        engine="internal",
        status=status,
        weights=weights_out,
        risk_contribution=risk_contribution,
        warnings=warnings,
        risk_budget_mode="barra_risk_budget",
        risk_model=risk_model,
        factor_risk_contribution=factor_pct,
    )


def _extract_candidates(request: ScannerPortfolioOptimizeRequest) -> list[_Candidate]:
    raw_rows: list[dict[str, Any]] = []
    raw_rows.extend(row.model_dump(mode="python") for row in request.rows)
    raw_rows.extend(request.row_summaries)
    seen: set[str] = set()
    candidates: list[_Candidate] = []
    for row in raw_rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        sparkline = _clean_sparkline(row.get("sparkline"))
        returns = _returns(sparkline)
        score_raw = row.get("scanner_score")
        scanner_score: float | None = None
        if score_raw is not None:
            try:
                scanner_score = float(score_raw)
            except (TypeError, ValueError):
                scanner_score = None
        candidates.append(
            _Candidate(
                symbol=symbol,
                sparkline=tuple(sparkline),
                volatility=_sample_std(returns),
                returns=tuple(returns),
                scanner_score=scanner_score,
            )
        )
    return candidates


def _clean_sparkline(raw: object) -> list[float]:
    if not isinstance(raw, list):
        return []
    values: list[float] = []
    for item in raw:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            values.append(value)
    return values


def _returns(prices: list[float]) -> list[float]:
    out: list[float] = []
    for previous, current in zip(prices, prices[1:], strict=False):
        if previous > 0 and math.isfinite(previous) and math.isfinite(current):
            out.append((current - previous) / previous)
    return out


def _sample_std(values: list[float] | tuple[float, ...]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    if var <= 0:
        return 0.0
    return math.sqrt(var)


def _score_weighted_weights(
    candidates: list[_Candidate],
    request: ScannerPortfolioOptimizeRequest,
) -> tuple[dict[str, float], list[str]]:
    warnings: list[str] = []
    raw: dict[str, float] = {}
    for candidate in candidates:
        score = candidate.scanner_score
        if score is None or not math.isfinite(score) or score <= 0:
            warnings.append(f"missing_score:{candidate.symbol}")
            raw[candidate.symbol] = 1.0
        else:
            raw[candidate.symbol] = max(score - 40.0, 1.0)
    return raw, warnings


def _inverse_vol_weights(candidates: list[_Candidate]) -> tuple[dict[str, float], list[str]]:
    warnings: list[str] = []
    vols = [candidate.volatility for candidate in candidates]
    if any(vol is None for vol in vols):
        warnings.append("insufficient_sparkline_for_inverse_vol")
        return ({candidate.symbol: 1.0 for candidate in candidates}, warnings)
    positive = [vol for vol in vols if vol is not None and vol > 0]
    floor = min(positive) * 0.25 if positive else 1.0
    raw = {
        candidate.symbol: 1.0 / max(candidate.volatility or 0.0, floor) for candidate in candidates
    }
    return raw, warnings


def _correlation_penalty_weights(
    candidates: list[_Candidate],
) -> tuple[dict[str, float], list[str]]:
    warnings: list[str] = []
    if len(candidates) < 3 or any(len(candidate.returns) < 3 for candidate in candidates):
        warnings.append("insufficient_sparkline_for_correlation_penalty")
        return _inverse_vol_weights(candidates)[0], warnings

    inverse_vol, inverse_warnings = _inverse_vol_weights(candidates)
    warnings.extend(inverse_warnings)
    raw: dict[str, float] = {}
    for candidate in candidates:
        correlations = [
            abs(correlation)
            for other in candidates
            if other.symbol != candidate.symbol
            if (correlation := _correlation(candidate.returns, other.returns)) is not None
        ]
        average_correlation = sum(correlations) / len(correlations) if correlations else 0.0
        raw[candidate.symbol] = inverse_vol[candidate.symbol] / (1.0 + average_correlation)
    return raw, warnings


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


def _apply_constraints(
    raw_weights: dict[str, float],
    min_weight: float,
    max_weight: float,
) -> tuple[dict[str, float], list[str]]:
    warnings: list[str] = []
    symbols = list(raw_weights)
    if not symbols:
        return {}, warnings
    n = len(symbols)
    max_weight = max(0.0, min(1.0, max_weight))
    min_weight = max(0.0, min(1.0, min_weight))
    if max_weight * n < 1.0:
        warnings.append("max_weight_infeasible")
        max_weight = 1.0
    if min_weight * n > 1.0:
        warnings.append("min_weight_infeasible")
        min_weight = 0.0

    normalized = _normalize(raw_weights)
    if min_weight > 0:
        remainder = max(0.0, 1.0 - min_weight * n)
        normalized = {
            symbol: min_weight + normalized.get(symbol, 0.0) * remainder for symbol in symbols
        }
    capped = _cap_and_redistribute(normalized, max_weight)
    return _round_to_unit_sum(capped), warnings


def _normalize(raw_weights: dict[str, float]) -> dict[str, float]:
    positive = {
        symbol: max(0.0, float(weight))
        for symbol, weight in raw_weights.items()
        if math.isfinite(float(weight))
    }
    total = sum(positive.values())
    if total <= 0:
        count = len(raw_weights)
        return {symbol: 1.0 / count for symbol in raw_weights} if count else {}
    return {symbol: weight / total for symbol, weight in positive.items()}


def _cap_and_redistribute(weights: dict[str, float], max_weight: float) -> dict[str, float]:
    remaining = dict(weights)
    fixed: dict[str, float] = {}
    while remaining:
        budget = 1.0 - sum(fixed.values())
        scaled = _normalize(remaining)
        scaled = {symbol: weight * budget for symbol, weight in scaled.items()}
        over = [symbol for symbol, weight in scaled.items() if weight > max_weight]
        if not over:
            fixed.update(scaled)
            break
        for symbol in over:
            fixed[symbol] = max_weight
            remaining.pop(symbol, None)
    return fixed


def _risk_contribution(
    candidates: list[_Candidate],
    weights: dict[str, float],
) -> dict[str, float]:
    raw: dict[str, float] = {}
    for candidate in candidates:
        weight = weights.get(candidate.symbol, 0.0)
        raw[candidate.symbol] = weight * max(candidate.volatility or 0.0, 1e-9)
    if sum(raw.values()) <= 0:
        raw = {candidate.symbol: weights.get(candidate.symbol, 0.0) for candidate in candidates}
    return _round_to_unit_sum(raw)


def _round_to_unit_sum(weights: dict[str, float]) -> dict[str, float]:
    if not weights:
        return {}
    rounded = {symbol: round(max(0.0, weight), 6) for symbol, weight in weights.items()}
    diff = round(1.0 - sum(rounded.values()), 6)
    if diff:
        target = max(rounded, key=rounded.get)
        rounded[target] = round(max(0.0, rounded[target] + diff), 6)
    return rounded


def _constraint_float(constraints: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(constraints.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default
