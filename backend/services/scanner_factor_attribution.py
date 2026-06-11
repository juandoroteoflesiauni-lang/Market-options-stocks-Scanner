"""Deterministic factor attribution for scanner conviction scoring.

Maps Barra exposures + Phase A indicators + Phase B modules to unified factor families,
computes contribution percentages, and calculates institutional conviction_score.
"""

from __future__ import annotations

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerRow,
    ScannerConvictionBreakdown,
    ScannerFactorDriver,
)
from backend.services.scanner_factor_history_store import get_percentiles

logger = get_logger(__name__)

UNIFIED_FACTOR_FAMILIES = {
    "market",
    "momentum",
    "volatility",
    "liquidity",
    "gex_positioning",
    "size",
    "value",
    "quality",
    "funding_basis",
    "onchain_activity",
    "module_technical",
    "module_probabilistic",
    "module_options_gex",
    "module_fundamentals",
    "module_macro_micro",
}

PHASE_A_INDICATOR_TO_FACTOR = {
    "rsi": "momentum",
    "rsi_hist": "momentum",
    "macd": "momentum",
    "ema_7_14": "momentum",
    "ema_21_42": "momentum",
    "ema_100_200": "momentum",
    "supertrend": "momentum",
    "bbp": "volatility",
    "vix": "volatility",
    "volume": "liquidity",
    "relative_strength": "momentum",
    "smc": "momentum",
    "market_structure": "momentum",
    "fvg": "momentum",
    "vsa": "liquidity",
    "volume_profile": "liquidity",
    "order_flow_delta": "liquidity",
    "net_gex": "gex_positioning",
    "dealer_bias": "gex_positioning",
    "gamma_flip": "gex_positioning",
    "iv_vol_term": "volatility",
    "flow_signal": "liquidity",
    "obv_oi": "liquidity",
    "mfi_flow": "liquidity",
    "cmf_iv": "liquidity",
    "vpin": "liquidity",
    "lob_microstructure": "liquidity",
    "avwap_vwap": "liquidity",
    "prf": "momentum",
}

MODULE_TO_FACTOR = {
    "technical": "module_technical",
    "probabilistic": "module_probabilistic",
    "options_gex": "module_options_gex",
    "fundamentals": "module_fundamentals",
    "macro_micro": "module_macro_micro",
}


def compute_factor_attribution(row: MarketScannerRow) -> dict[str, ScannerFactorDriver]:
    """Compute deterministic factor loadings from Barra + Phase A + Phase B.

    Args:
        row: Scanner row with barra_exposure, score_audit, module_signals

    Returns:
        Dict mapping factor_key to ScannerFactorDriver
    """
    loadings: dict[str, float] = {}
    sources: dict[str, str] = {}
    tiers: dict[str, str] = {}

    if row.barra_exposure:
        for factor_key, loading in row.barra_exposure.factors.items():
            if factor_key in UNIFIED_FACTOR_FAMILIES:
                loadings[factor_key] = loading
                sources[factor_key] = "barra"
                tiers[factor_key] = row.barra_exposure.factor_sources.get(factor_key, "proxy")

    phase_a_contrib = row.score_audit.get("phase_a_indicator_contributions", {})
    for indicator_key, contrib in phase_a_contrib.items():
        factor_family = PHASE_A_INDICATOR_TO_FACTOR.get(indicator_key)
        if factor_family:
            loadings[factor_family] = loadings.get(factor_family, 0.0) + contrib
            if factor_family not in sources:
                sources[factor_family] = "phase_a"
                indicator_metric = row.indicator_metrics.get(indicator_key, {})
                tiers[factor_family] = indicator_metric.get("data_tier", "proxy")

    phase_b_blend = row.score_audit.get("phase_b_blend", {})
    pre_score = phase_b_blend.get("pre", 50.0)
    final_score = phase_b_blend.get("final", 50.0)
    delta = final_score - pre_score
    if abs(delta) > 0.01:
        module_scores = {mod: sig.score for mod, sig in row.module_signals.items()}
        total_deviation = sum(abs(s - 50.0) for s in module_scores.values())
        if total_deviation > 0:
            for module_key, module_score in module_scores.items():
                factor_key = MODULE_TO_FACTOR.get(module_key)
                if factor_key:
                    weight = abs(module_score - 50.0) / total_deviation
                    loadings[factor_key] = loadings.get(factor_key, 0.0) + delta * weight
                    if factor_key not in sources:
                        sources[factor_key] = "module"
                        sig = row.module_signals.get(module_key)
                        tiers[factor_key] = "real" if sig and sig.available_count > 0 else "proxy"

    total_abs = sum(abs(v) for v in loadings.values())
    if total_abs < 1e-6:
        return {}

    drivers: dict[str, ScannerFactorDriver] = {}
    for factor_key, loading in loadings.items():
        contribution_pct = (abs(loading) / total_abs) * 100.0
        drivers[factor_key] = ScannerFactorDriver(
            factor_key=factor_key,
            contribution_pct=round(contribution_pct, 2),
            loading=round(loading, 4),
            historical_percentile=None,
            data_tier=tiers.get(factor_key, "proxy"),
            source=sources.get(factor_key, "unknown"),
        )

    return drivers


def compute_conviction_breakdown(
    row: MarketScannerRow,
    *,
    historical_percentiles: dict[str, float | None],
    drivers: dict[str, ScannerFactorDriver],
) -> ScannerConvictionBreakdown:
    """Compute institutional conviction score from factor attribution and history.

    Formula v1:
        conviction_raw =
          0.35 * scanner_score_normalized
        + 0.35 * mean(historical_percentile of top-3 drivers)
        + 0.20 * coverage_pct
        + 0.10 * data_quality_factor

    Args:
        row: Scanner row
        historical_percentiles: Factor key → percentile or None
        drivers: Factor attribution drivers

    Returns:
        ScannerConvictionBreakdown with conviction_score and metadata
    """
    warnings: list[str] = []
    top_drivers_list = sorted(drivers.values(), key=lambda d: d.contribution_pct, reverse=True)[:5]

    for driver in top_drivers_list:
        driver.historical_percentile = historical_percentiles.get(driver.factor_key)

    coverage_pct = sum(d.contribution_pct for d in top_drivers_list[:3])

    scanner_norm = row.scanner_score / 100.0

    top3_percentiles = [
        d.historical_percentile for d in top_drivers_list[:3] if d.historical_percentile is not None
    ]
    if len(top3_percentiles) < 2:
        warnings.append("insufficient_factor_history")
    mean_percentile = sum(top3_percentiles) / len(top3_percentiles) if top3_percentiles else 50.0
    mean_percentile_norm = mean_percentile / 100.0

    proxy_count = sum(1 for d in top_drivers_list[:3] if d.data_tier == "proxy")
    data_quality_factor = 1.0 if proxy_count == 0 else max(0.5, 1.0 - proxy_count * 0.2)
    if proxy_count > 1:
        warnings.append("proxy_heavy")

    conviction_raw = (
        0.35 * scanner_norm
        + 0.35 * mean_percentile_norm
        + 0.20 * (coverage_pct / 100.0)
        + 0.10 * data_quality_factor
    )
    conviction_score = round(max(0.0, min(100.0, conviction_raw * 100.0)), 2)

    return ScannerConvictionBreakdown(
        conviction_score=conviction_score,
        scanner_score=row.scanner_score,
        top_drivers=top_drivers_list,
        factor_contributions={k: v.loading for k, v in drivers.items()},
        historical_percentiles={k: v for k, v in historical_percentiles.items() if v is not None},
        coverage_pct=round(coverage_pct, 2),
        warnings=warnings,
    )


def attach_conviction_to_rows(rows: list[MarketScannerRow]) -> None:
    """Attach conviction_breakdown and conviction_score to scanner rows in place.

    Args:
        rows: List of MarketScannerRow to enrich
    """
    for row in rows:
        drivers = compute_factor_attribution(row)
        if not drivers:
            continue

        factor_keys = list(drivers.keys())
        historical_percentiles = get_percentiles(row.symbol, factor_keys)

        conviction_breakdown = compute_conviction_breakdown(
            row, historical_percentiles=historical_percentiles, drivers=drivers
        )
        row.conviction_breakdown = conviction_breakdown
        row.conviction_score = conviction_breakdown.conviction_score

    logger.info(
        "Attached conviction to %d rows (%d with breakdown)",
        len(rows),
        sum(1 for r in rows if r.conviction_breakdown is not None),
    )
