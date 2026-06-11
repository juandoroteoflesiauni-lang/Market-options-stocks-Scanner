"""Institutional portfolio risk stack: score → factor constraints → production sizing (Point 6)."""

from __future__ import annotations

import math
import os
from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    BarraRiskModelOutput,
    MarketScannerRow,
    ScannerPortfolioOptimizeRequest,
    ScannerPortfolioOptimizeResponse,
    ScannerRiskStackAllocation,
    ScannerRiskStackConstraints,
    ScannerRiskStackResponse,
)
from backend.layer_5_risk.portfolio_risk.component import fractional_kelly
from backend.services.scanner_factor_constraints import (
    apply_correlation_penalty_to_weights,
    apply_factor_limits,
    compute_factor_exposure,
    extract_factor_loadings,
)
from backend.services.scanner_portfolio_optimizer import optimize_scanner_portfolio

logger = get_logger(__name__)

_SCHEMA_VERSION = "institutional-risk-stack-v1"


def risk_stack_enabled() -> bool:
    raw = os.getenv("SCANNER_RISK_STACK", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def kelly_production_enabled() -> bool:
    raw = os.getenv("SCANNER_KELLY_PRODUCTION", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def constraints_from_dict(raw: dict[str, Any] | None) -> ScannerRiskStackConstraints:
    if not raw:
        return ScannerRiskStackConstraints()
    return ScannerRiskStackConstraints.model_validate(raw)


def run_scanner_risk_stack(
    rows: list[MarketScannerRow],
    *,
    constraints: ScannerRiskStackConstraints | dict[str, Any] | None = None,
    universe_regime: dict[str, Any] | None = None,
) -> ScannerRiskStackResponse:
    """Full Point-6 chain for scan rows (mutates rows with binding fields)."""
    if not risk_stack_enabled():
        return ScannerRiskStackResponse(enabled=False, warnings=["risk_stack_disabled"])

    limits = constraints_from_dict(constraints if isinstance(constraints, dict) else None)
    if isinstance(constraints, ScannerRiskStackConstraints):
        limits = constraints

    eligible = [row for row in rows if _row_eligible(row)]
    eligible_symbols = {row.symbol for row in eligible}
    if not eligible:
        return ScannerRiskStackResponse(
            enabled=True,
            schema_version=_SCHEMA_VERSION,
            constraints_applied=limits,
            warnings=["no_eligible_rows"],
            optimizer_status="unavailable",
        )

    optimize_req = ScannerPortfolioOptimizeRequest(
        rows=eligible,
        constraints={
            "max_weight": limits.max_weight,
            "min_weight": limits.min_weight,
            "long_only": limits.long_only,
            "risk_budget_mode": limits.risk_budget_mode,
            "factor_limits": limits.factor_limits.model_dump(mode="python"),
        },
    )
    from backend.services.scanner_barra_factor_model import (
        barra_factors_enabled,
        compute_barra_risk_model,
    )

    if barra_factors_enabled():
        compute_barra_risk_model(eligible)

    optimize_out = optimize_scanner_portfolio(optimize_req)
    barra_model: BarraRiskModelOutput | None = optimize_out.risk_model
    weights = {item.symbol: item.weight for item in optimize_out.weights}

    loadings = {row.symbol: extract_factor_loadings(row) for row in eligible}
    sparklines = {row.symbol: list(row.sparkline or []) for row in eligible if row.sparkline}

    factor_warnings: list[str] = []
    if weights:
        weights, factor_warn = apply_factor_limits(weights, loadings, limits.factor_limits)
        factor_warnings.extend(factor_warn)
        weights, corr_warn = apply_correlation_penalty_to_weights(weights, sparklines)
        factor_warnings.extend(corr_warn)

    regime_mult = _regime_kelly_multiplier(universe_regime)
    allocations: list[ScannerRiskStackAllocation] = []
    stack_warnings = list(optimize_out.warnings) + factor_warnings

    for row in rows:
        symbol = row.symbol
        if symbol not in eligible_symbols:
            zero = ScannerRiskStackAllocation(
                symbol=symbol,
                portfolio_weight=0.0,
                production_size_multiplier=0.0,
                production_kelly_fraction=0.0,
                factor_loadings=extract_factor_loadings(row),
                funding_size_multiplier=0.0,
                drawdown_cap_multiplier=0.0,
                warnings=["excluded_from_basket"],
            )
            allocations.append(zero)
            _apply_allocation_to_row(row, zero)
            continue

        portfolio_weight = float(weights.get(symbol, 0.0))
        funding_mult = float(row.recommended_size_multiplier or 1.0)
        if str(row.funding_suitability or "").lower() == "block":
            funding_mult = 0.0

        production_kelly = _production_kelly_fraction(row, limits, regime_mult)
        drawdown_cap = _drawdown_cap_multiplier(row, limits)
        weight_cap = (
            portfolio_weight / max(limits.max_weight, 1e-9) if limits.max_weight > 0 else 1.0
        )
        weight_cap = max(0.0, min(1.0, weight_cap))

        binding_parts = [funding_mult, weight_cap, drawdown_cap]
        if kelly_production_enabled():
            kelly_ratio = production_kelly / max(limits.kelly_cap, 1e-9)
            binding_parts.append(min(1.0, kelly_ratio))

        production_size = max(0.0, min(binding_parts))
        if portfolio_weight <= 0:
            production_size = 0.0

        row_warnings: list[str] = []
        if production_size < funding_mult:
            row_warnings.append("production_cap_below_funding_gate")
        if drawdown_cap < 1.0:
            row_warnings.append("drawdown_cap_applied")

        alloc = ScannerRiskStackAllocation(
            symbol=symbol,
            portfolio_weight=round(portfolio_weight, 6),
            production_size_multiplier=round(production_size, 4),
            production_kelly_fraction=round(production_kelly, 4),
            factor_loadings=loadings.get(symbol, {}),
            funding_size_multiplier=round(funding_mult, 4),
            drawdown_cap_multiplier=round(drawdown_cap, 4),
            warnings=row_warnings,
        )
        allocations.append(alloc)
        _apply_allocation_to_row(row, alloc)

    factor_exposure = compute_factor_exposure(weights, loadings)
    status = optimize_out.status
    if factor_warnings and status == "ok":
        status = "degraded"

    if barra_factors_enabled() and weights:
        barra_model = compute_barra_risk_model(eligible, weights=weights)

    return ScannerRiskStackResponse(
        enabled=True,
        schema_version=_SCHEMA_VERSION,
        allocations=allocations,
        factor_exposure=factor_exposure,
        constraints_applied=limits,
        warnings=stack_warnings,
        optimizer_status=status,
        barra_risk_model=barra_model,
    )


def enrich_portfolio_optimize_response(
    response: ScannerPortfolioOptimizeResponse,
    rows: list[MarketScannerRow],
    *,
    constraints: dict[str, Any] | None = None,
) -> ScannerPortfolioOptimizeResponse:
    """Attach risk stack to portfolio-optimize endpoint output."""
    if not risk_stack_enabled():
        return response
    stack = run_scanner_risk_stack(rows, constraints=constraints)
    payload = response.model_dump(mode="python")
    payload["risk_stack"] = stack.model_dump(mode="python")
    if stack.barra_risk_model and not payload.get("risk_model"):
        payload["risk_model"] = stack.barra_risk_model.model_dump(mode="python")
    return ScannerPortfolioOptimizeResponse.model_validate(payload)


def _row_eligible(row: MarketScannerRow) -> bool:
    if not row.symbol:
        return False
    if str(row.funding_suitability or "").lower() == "block":
        return False
    score = float(row.scanner_score or 0)
    return score > 0 or bool(row.sparkline)


def _apply_allocation_to_row(row: MarketScannerRow, alloc: ScannerRiskStackAllocation) -> None:
    row.portfolio_weight = alloc.portfolio_weight
    row.production_size_multiplier = alloc.production_size_multiplier
    row.production_kelly_fraction = alloc.production_kelly_fraction
    row.factor_loadings = dict(alloc.factor_loadings)
    audit = dict(row.score_audit or {})
    audit["risk_stack"] = {
        "portfolio_weight": alloc.portfolio_weight,
        "production_size_multiplier": alloc.production_size_multiplier,
        "production_kelly_fraction": alloc.production_kelly_fraction,
        "factor_loadings": alloc.factor_loadings,
        "funding_size_multiplier": alloc.funding_size_multiplier,
        "drawdown_cap_multiplier": alloc.drawdown_cap_multiplier,
        "warnings": list(alloc.warnings),
    }
    row.score_audit = audit


def _production_kelly_fraction(
    row: MarketScannerRow,
    limits: ScannerRiskStackConstraints,
    regime_mult: float,
) -> float:
    hints = row.risk_hints or {}
    hint_kelly = hints.get("kelly_fraction_hint")
    win_prob = 0.5 + float(row.scanner_score or 50.0) / 200.0 - 0.25
    win_prob = max(0.05, min(0.95, win_prob))
    confidence = getattr(row, "confidence", None)
    if confidence is None and row.module_signals:
        confs = [float(sig.confidence) for sig in row.module_signals.values()]
        confidence = sum(confs) / len(confs) if confs else None
    if confidence is not None:
        try:
            win_prob = max(0.05, min(0.95, float(confidence)))
        except (TypeError, ValueError):
            pass

    kelly = fractional_kelly(
        win_prob,
        shrink=0.25 * regime_mult,
        cap=limits.kelly_cap,
    )
    if hint_kelly is not None:
        try:
            kelly = min(kelly, float(hint_kelly) * regime_mult, limits.kelly_cap)
        except (TypeError, ValueError):
            pass
    return max(0.0, min(limits.kelly_cap, kelly))


def _drawdown_cap_multiplier(row: MarketScannerRow, limits: ScannerRiskStackConstraints) -> float:
    """Cap size when tail risk or sparkline drawdown proxy is elevated."""
    tail = getattr(row, "tail_risk", None)
    if tail is not None:
        try:
            tail_f = float(tail)
            if tail_f >= 0.85:
                return 0.0
            if tail_f >= 0.7:
                return 0.5
        except (TypeError, ValueError):
            pass

    sparkline = row.sparkline or []
    if len(sparkline) >= 5:
        prices = [float(p) for p in sparkline if _finite_positive(p)]
        if len(prices) >= 5:
            peak = prices[0]
            max_dd = 0.0
            for price in prices:
                peak = max(peak, price)
                if peak > 0:
                    max_dd = max(max_dd, (peak - price) / peak)
            usage_pct = max_dd * 100.0
            if usage_pct >= limits.max_drawdown_usage_pct:
                return 0.0
            if usage_pct >= limits.max_drawdown_usage_pct * 0.75:
                return 0.5

    return 1.0


def _regime_kelly_multiplier(universe_regime: dict[str, Any] | None) -> float:
    if not universe_regime:
        return 1.0
    tone = str(universe_regime.get("tone") or "").lower()
    if tone == "risk_off":
        return 0.75
    if tone == "risk_on":
        return 1.0
    return 0.9


def _finite_positive(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0
