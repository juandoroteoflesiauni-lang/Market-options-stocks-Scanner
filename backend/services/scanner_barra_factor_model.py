from __future__ import annotations
from typing import Any
"""Institutional Barra-style multi-factor risk model for Market Scanner (Point 2)."""


import math
import os
from datetime import UTC, datetime

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    BarraAssetClass,
    BarraFactorCovariance,
    BarraFactorExposure,
    BarraRiskModelOutput,
    MarketScannerRow,
)
from backend.services.scanner_barra_covariance import (
    estimate_factor_covariance,
    estimate_specific_risk,
    risk_contribution_decomposition,
)
from backend.services.scanner_symbol_routing import (
    is_crypto_root,
    is_equity_root,
    normalize_scanner_symbol,
)

logger = get_logger(__name__)

# Equity style factors (GICS sectors as one-hot)
EQUITY_STYLE_FACTORS: tuple[str, ...] = (
    "market",
    "size",
    "value",
    "momentum",
    "volatility",
    "liquidity",
    "quality",
    "gex_positioning",
)

GICS_SECTORS: tuple[str, ...] = (
    "energy",
    "materials",
    "industrials",
    "consumer_discretionary",
    "consumer_staples",
    "health_care",
    "financials",
    "information_technology",
    "communication_services",
    "utilities",
    "real_estate",
)

CRYPTO_STYLE_FACTORS: tuple[str, ...] = (
    "market",
    "momentum",
    "volatility",
    "liquidity",
    "funding_basis",
    "onchain_activity",
    "gex_positioning",
)

CRYPTO_CATEGORIES: tuple[str, ...] = (
    "layer1",
    "layer2",
    "defi",
    "payment",
    "meme",
    "infrastructure",
    "exchange",
    "other",
)

# Static GICS proxy map (expand via FMP sector when available)
_GICS_BY_SYMBOL: dict[str, str] = {
    "AAPL": "information_technology",
    "MSFT": "information_technology",
    "GOOGL": "communication_services",
    "GOOG": "communication_services",
    "AMZN": "consumer_discretionary",
    "META": "communication_services",
    "NVDA": "information_technology",
    "TSLA": "consumer_discretionary",
    "JPM": "financials",
    "XOM": "energy",
    "JNJ": "health_care",
    "UNH": "health_care",
    "V": "financials",
    "PG": "consumer_staples",
    "HD": "consumer_discretionary",
    "MA": "financials",
    "BAC": "financials",
    "PFE": "health_care",
    "KO": "consumer_staples",
    "PEP": "consumer_staples",
}

_CRYPTO_CATEGORY: dict[str, str] = {
    "BTC": "layer1",
    "ETH": "layer1",
    "SOL": "layer1",
    "BNB": "exchange",
    "XRP": "payment",
    "ADA": "layer1",
    "DOGE": "meme",
    "AVAX": "layer1",
    "DOT": "layer1",
    "LINK": "defi",
    "MATIC": "layer2",
    "LTC": "payment",
    "UNI": "defi",
    "NEAR": "layer1",
    "APT": "layer1",
    "ARB": "layer2",
    "OP": "layer2",
    "SUI": "layer1",
}

_DEFAULT_FACTOR_RISK_BUDGET: dict[str, float] = {
    "market": 0.35,
    "momentum": 0.25,
    "volatility": 0.20,
    "liquidity": 0.18,
    "gex_positioning": 0.15,
    "funding_basis": 0.12,
    "onchain_activity": 0.10,
}


def barra_factors_enabled() -> bool:
    raw = os.getenv("SCANNER_BARRA_FACTORS", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def classify_barra_asset_class(symbol: str) -> BarraAssetClass:
    root = normalize_scanner_symbol(symbol)
    if is_crypto_root(root):
        return "crypto"
    if is_equity_root(root):
        return "equity"
    return "other"


def equity_factor_names() -> list[str]:
    sectors = [f"sector_{s}" for s in GICS_SECTORS]
    return [*EQUITY_STYLE_FACTORS, *sectors, "asset_class_equity"]


def crypto_factor_names() -> list[str]:
    cats = [f"category_{c}" for c in CRYPTO_CATEGORIES]
    return [*CRYPTO_STYLE_FACTORS, *cats, "asset_class_crypto"]


def default_factor_risk_budget(asset_class: BarraAssetClass | None = None) -> dict[str, float]:
    budget = dict(_DEFAULT_FACTOR_RISK_BUDGET)
    if asset_class == "equity":
        for sector in GICS_SECTORS:
            budget[f"sector_{sector}"] = 0.08
        budget["asset_class_equity"] = 0.05
    elif asset_class == "crypto":
        for cat in CRYPTO_CATEGORIES:
            budget[f"category_{cat}"] = 0.08
        budget["asset_class_crypto"] = 0.05
    return budget


def _clamp_unit(value: float) -> float:
    return round(max(-1.0, min(1.0, value)), 6)


def _nested_float(data: dict[str, Any], path: tuple[str, ...]) -> float | None:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    try:
        if cur is None:
            return None
        out = float(cur)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _returns_from_sparkline(sparkline: list[float]) -> list[float]:
    out: list[float] = []
    for prev, cur in zip(sparkline, sparkline[1:], strict=False):
        if prev > 0 and cur > 0:
            out.append((cur - prev) / prev)
    return out


def _sample_std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    if var <= 0:
        return 0.0
    return math.sqrt(var)


def _factor_source_tier(
    row: MarketScannerRow,
    *,
    indicator_key: str | None = None,
    used_real: bool = False,
) -> str:
    if used_real:
        return "real"
    attr = row.source_attribution or {}
    if indicator_key and indicator_key in attr:
        tier = str(attr[indicator_key].get("tier") or "")
        if tier == "real":
            return "real"
        if tier == "partial":
            return "partial"
    return "proxy"


def _gex_loading(row: MarketScannerRow) -> tuple[float, str]:
    overlay = row.institutional_overlay if isinstance(row.institutional_overlay, dict) else {}
    net_gex = overlay.get("net_gex") or overlay.get("net_gamma_exposure")
    gex_attr = (row.source_attribution or {}).get("net_gex") or {}
    tier = str(gex_attr.get("tier") or "proxy")
    if net_gex is not None:
        try:
            val = float(net_gex)
            if abs(val) > 1e6:
                return _clamp_unit(val / 1e9), "real" if tier == "real" else "partial"
            return _clamp_unit(val), "real" if tier == "real" else "partial"
        except (TypeError, ValueError):
            pass
    indicators_raw = getattr(row, "indicators", None)
    indicators = indicators_raw if isinstance(indicators_raw, dict) else {}
    net_ind = indicators.get("net_gex")
    if isinstance(net_ind, dict):
        raw = net_ind.get("value")
        try:
            if raw is not None:
                return _clamp_unit(float(raw)), tier if tier in {"real", "partial"} else "proxy"
        except (TypeError, ValueError):
            pass
    return 0.0, "proxy"


def _funding_basis_loading(row: MarketScannerRow) -> tuple[float, str]:
    audit = row.score_audit if isinstance(row.score_audit, dict) else {}
    funding = audit.get("funding") or audit.get("funding_gate") or {}
    rate = None
    if isinstance(funding, dict):
        rate = funding.get("funding_rate") or funding.get("rate")
    if rate is None and row.funding_suitability:
        suitability = str(row.funding_suitability).lower()
        if suitability == "favorable":
            return -0.2, "partial"
        if suitability == "caution":
            return 0.15, "partial"
        if suitability == "block":
            return 0.5, "partial"
    if rate is not None:
        try:
            return _clamp_unit(float(rate) * 100.0), "real"
        except (TypeError, ValueError):
            pass
    return 0.0, "proxy"


def _fundamentals_loadings(
    row: MarketScannerRow,
) -> tuple[dict[str, float], dict[str, str]]:
    """Size / Value / Quality from FMP key metrics when present."""
    out: dict[str, float] = {}
    sources: dict[str, str] = {}
    audit = row.score_audit if isinstance(row.score_audit, dict) else {}
    km = audit.get("fundamentals_key_metrics") or audit.get("key_metrics_ttm")
    ratios = audit.get("fundamentals_ratios") or audit.get("ratios_ttm")
    if not isinstance(km, dict) and not isinstance(ratios, dict):
        module = (row.module_signals or {}).get("fundamentals")
        if isinstance(module, dict):
            km = module.get("key_metrics") or module.get("metrics")
    tier = "proxy"
    if isinstance(km, dict) and km:
        tier = "real"
        mcap = km.get("marketCap") or km.get("market_cap")
        if mcap is not None:
            try:
                log_mcap = math.log10(max(float(mcap), 1.0))
                out["size"] = _clamp_unit((log_mcap - 10.0) / 3.0)
            except (TypeError, ValueError):
                out["size"] = 0.0
        pe = km.get("peRatioTTM") or km.get("pe_ratio")
        if pe is not None:
            try:
                out["value"] = _clamp_unit(1.0 - min(float(pe), 60.0) / 40.0)
            except (TypeError, ValueError):
                pass
        roe = km.get("returnOnEquityTTM") or km.get("roe")
        if roe is not None:
            try:
                out["quality"] = _clamp_unit(float(roe) * 2.0)
            except (TypeError, ValueError):
                pass
    if isinstance(ratios, dict) and ratios:
        tier = "real" if tier == "real" else "partial"
        pb = ratios.get("priceToBookRatioTTM") or ratios.get("price_to_book")
        if pb is not None and "value" not in out:
            try:
                out["value"] = _clamp_unit(1.0 - min(float(pb), 8.0) / 6.0)
            except (TypeError, ValueError):
                pass
    for key in ("size", "value", "quality"):
        sources[key] = tier if key in out else "proxy"
    return out, sources


def _style_from_row(row: MarketScannerRow) -> tuple[dict[str, float], dict[str, str]]:
    data = row.model_dump(mode="python")
    factors: dict[str, float] = {}
    sources: dict[str, str] = {}

    score = float(row.scanner_score or 50.0)
    momentum = _clamp_unit((score - 50.0) / 50.0)
    direction = str(row.direction or "neutral").lower()
    if direction == "bearish":
        momentum = -abs(momentum)
    elif direction == "bullish":
        momentum = abs(momentum)
    factors["momentum"] = momentum
    sources["momentum"] = "proxy"

    factors["market"] = 1.0
    sources["market"] = "proxy"

    rel_vol = _nested_float(data, ("metrics", "relative_volume"))
    liquidity = 0.0
    if rel_vol is not None and rel_vol > 0:
        liquidity = _clamp_unit(math.log1p(rel_vol) / math.log1p(5.0) - 0.35)
    factors["liquidity"] = liquidity
    sources["liquidity"] = _factor_source_tier(row, indicator_key="volume")

    spark = list(row.sparkline or [])
    vol_loading = 0.0
    if len(spark) >= 3:
        rets = _returns_from_sparkline(spark)
        sample_vol = _sample_std(rets)
        if sample_vol is not None:
            vol_loading = _clamp_unit(sample_vol * 25.0)
    factors["volatility"] = vol_loading
    sources["volatility"] = "proxy"

    gex, gex_src = _gex_loading(row)
    factors["gex_positioning"] = gex
    sources["gex_positioning"] = gex_src

    return factors, sources


def _sector_one_hot(symbol: str, fmp_sector: str | None = None) -> tuple[dict[str, float], str]:
    root = normalize_scanner_symbol(symbol)
    sector = fmp_sector or _GICS_BY_SYMBOL.get(root)
    out = {f"sector_{s}": 0.0 for s in GICS_SECTORS}
    tier = "proxy"
    if sector and sector in GICS_SECTORS:
        out[f"sector_{sector}"] = 1.0
        tier = "real" if fmp_sector else "partial"
    return out, tier


def _category_one_hot(symbol: str) -> tuple[dict[str, float], str]:
    root = normalize_scanner_symbol(symbol)
    cat = _CRYPTO_CATEGORY.get(root, "other")
    out = {f"category_{c}": 0.0 for c in CRYPTO_CATEGORIES}
    out[f"category_{cat}"] = 1.0
    return out, "partial" if cat != "other" else "proxy"


def compute_barra_exposure(row: MarketScannerRow) -> BarraFactorExposure:
    """Per-symbol Barra factor exposures (equity vs crypto taxonomy)."""
    asset_class = classify_barra_asset_class(row.symbol)
    style, style_sources = _style_from_row(row)
    factors: dict[str, float] = {}
    sources: dict[str, str] = dict(style_sources)

    if asset_class == "equity":
        fund, fund_src = _fundamentals_loadings(row)
        factors.update(style)
        factors.update(fund)
        sources.update(fund_src)
        sectors, sec_tier = _sector_one_hot(row.symbol)
        factors.update(sectors)
        for k in sectors:
            sources[k] = sec_tier
        factors["asset_class_equity"] = 1.0
        sources["asset_class_equity"] = "real"
        factors["asset_class_crypto"] = 0.0
    elif asset_class == "crypto":
        factors.update(style)
        fund_basis, fb_src = _funding_basis_loading(row)
        factors["funding_basis"] = fund_basis
        sources["funding_basis"] = fb_src
        onchain = _clamp_unit(
            factors.get("liquidity", 0.0) * 0.6 + factors.get("momentum", 0.0) * 0.2
        )
        factors["onchain_activity"] = onchain
        sources["onchain_activity"] = "proxy"
        cats, cat_tier = _category_one_hot(row.symbol)
        factors.update(cats)
        for k in cats:
            sources[k] = cat_tier
        factors["asset_class_crypto"] = 1.0
        sources["asset_class_crypto"] = "real"
        factors["asset_class_equity"] = 0.0
        for key in ("size", "value", "quality"):
            factors.pop(key, None)
    else:
        factors.update(style)
        factors["asset_class_equity"] = 0.0
        factors["asset_class_crypto"] = 0.0

    spark = list(row.sparkline or [])
    explained = sum(abs(v) for k, v in factors.items() if not k.startswith("sector_"))
    spec = estimate_specific_risk(spark, factor_explained_var=min(0.85, explained * 0.05))

    return BarraFactorExposure(
        symbol=row.symbol,
        asset_class=asset_class,
        factors={k: round(float(v), 6) for k, v in factors.items()},
        factor_sources=sources,
        specific_risk=spec,
    )


def apply_barra_to_rows(rows: list[MarketScannerRow]) -> list[BarraFactorExposure]:
    """Compute and attach Barra exposures to scanner rows."""
    exposures: list[BarraFactorExposure] = []
    for row in rows:
        exp = compute_barra_exposure(row)
        row.barra_exposure = exp
        row.specific_risk = exp.specific_risk
        exposures.append(exp)
    return exposures


def compute_barra_risk_model(
    rows: list[MarketScannerRow],
    *,
    weights: dict[str, float] | None = None,
    factor_risk_budget: dict[str, float] | None = None,
) -> BarraRiskModelOutput:
    """Basket-level Barra model: exposures, covariance, risk attribution."""
    if not barra_factors_enabled():
        return BarraRiskModelOutput(enabled=False, warnings=["barra_factors_disabled"])

    exposures = [row.barra_exposure for row in rows if row.barra_exposure]
    if not exposures:
        exposures = apply_barra_to_rows(rows)

    if not exposures:
        return BarraRiskModelOutput(enabled=True, warnings=["no_exposures"])

    by_symbol = {e.symbol: e for e in exposures}
    equity_exp = [e for e in exposures if e.asset_class == "equity"]
    crypto_exp = [e for e in exposures if e.asset_class == "crypto"]

    cov_equity = estimate_factor_covariance(equity_exp, asset_class="equity")
    cov_crypto = estimate_factor_covariance(crypto_exp, asset_class="crypto")
    combined_names = sorted(set(cov_equity.factor_names) | set(cov_crypto.factor_names))
    n = len(combined_names)
    combined_matrix = [[0.0] * n for _ in range(n)]
    idx = {name: i for i, name in enumerate(combined_names)}

    def _merge_block(cov_block: Any) -> None:
        if not cov_block.factor_names:
            return
        local_idx = {name: i for i, name in enumerate(cov_block.factor_names)}
        for i_name, i_local in local_idx.items():
            gi = idx[i_name]
            for j_name, j_local in local_idx.items():
                gj = idx[j_name]
                if i_local < len(cov_block.matrix) and j_local < len(cov_block.matrix[i_local]):
                    combined_matrix[gi][gj] = cov_block.matrix[i_local][j_local]

    _merge_block(cov_equity)
    _merge_block(cov_crypto)

    covariance = BarraFactorCovariance(
        factor_names=combined_names,
        matrix=combined_matrix,
        half_life_days=cov_equity.half_life_days,
        as_of=datetime.now(UTC),
    )

    if weights is None:
        eligible = [r for r in rows if r.barra_exposure and (r.scanner_score or 0) > 0]
        if not eligible:
            eligible = [r for r in rows if r.barra_exposure]
        n_el = max(len(eligible), 1)
        weights = {r.symbol: 1.0 / n_el for r in eligible}

    budget = factor_risk_budget or default_factor_risk_budget()
    specific_by_symbol = {e.symbol: float(e.specific_risk or 0.15) for e in exposures}

    factor_pct, marginal, spec_pct = risk_contribution_decomposition(
        weights,
        by_symbol,
        covariance,
        specific_by_symbol,
    )

    warnings: list[str] = []
    if len(equity_exp) == 0:
        warnings.append("no_equity_exposures")
    if len(crypto_exp) == 0:
        warnings.append("no_crypto_exposures")

    return BarraRiskModelOutput(
        enabled=True,
        exposures=exposures,
        covariance=covariance,
        factor_risk_contribution=factor_pct,
        specific_risk_by_symbol=specific_by_symbol,
        marginal_risk_contribution=marginal,
        factor_risk_budget=budget,
        warnings=warnings,
    )
