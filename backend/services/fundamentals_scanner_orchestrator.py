"""Fundamentals module synthesis for Market Scanner Phase B (FMP + forensic scores)."""

from __future__ import annotations

import asyncio
from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerRow,
    ScannerCustomization,
    ScannerIndicatorDefinition,
    ScannerModuleSignal,
)
from backend.layer_3_specialists.fundamentales.scoring_engine import calcular_scores
from backend.services.market_scanner_module_signals import (
    build_module_signal,
    neutral_module_signal,
)

logger = get_logger(__name__)


async def fetch_ratios_ttm_batch(symbols: list[str]) -> dict[str, dict[str, Any] | None]:
    """Fetch ratios TTM per symbol (best-effort; missing key on failure)."""
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

    client = FMPClient()

    async def _one(sym: str) -> tuple[str, dict[str, Any] | None]:
        try:
            row = await client.get_ratios_ttm(sym)
            if row is None:
                return sym, None
            return sym, row.model_dump()
        except Exception as exc:
            logger.debug("fundamentals_scanner.ratios_failed symbol=%s err=%s", sym, str(exc)[:120])
            return sym, None

    pairs = await asyncio.gather(*(_one(s) for s in symbols), return_exceptions=False)
    return dict(pairs)


async def fetch_financial_scores_batch(symbols: list[str]) -> dict[str, dict[str, Any] | None]:
    """FMP financial scores (Altman Z, Piotroski) per symbol — forensic desk proxy."""
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

    client = FMPClient()

    async def _one(sym: str) -> tuple[str, dict[str, Any] | None]:
        try:
            rows = await client.get_financial_scores(sym)
            if not rows:
                return sym, None
            return sym, rows[0].model_dump()
        except Exception as exc:
            logger.debug("fundamentals_scanner.scores_failed symbol=%s err=%s", sym, str(exc)[:120])
            return sym, None

    pairs = await asyncio.gather(*(_one(s) for s in symbols), return_exceptions=False)
    return dict(pairs)


async def fetch_key_metrics_ttm_batch(symbols: list[str]) -> dict[str, dict[str, Any] | None]:
    """Fetch Key Metrics TTM per symbol (PE, EV/EBITDA, etc.)."""
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

    client = FMPClient()

    async def _one(sym: str) -> tuple[str, dict[str, Any] | None]:
        try:
            row = await client.get_key_metrics_ttm(sym)
            return sym, row.model_dump() if row else None
        except Exception as exc:
            logger.debug(
                "fundamentals_scanner.key_metrics_failed symbol=%s err=%s", sym, str(exc)[:120]
            )
            return sym, None

    pairs = await asyncio.gather(*(_one(s) for s in symbols), return_exceptions=False)
    return dict(pairs)


async def fetch_earnings_surprises_batch(
    symbols: list[str],
) -> dict[str, list[dict[str, Any]] | None]:
    """Fetch recent earnings surprises per symbol."""
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

    client = FMPClient()

    async def _one(sym: str) -> tuple[str, list[dict[str, Any]] | None]:
        try:
            rows = await client.get_earnings_surprises(sym)
            return sym, [r.model_dump() for r in rows[:4]] if rows else None
        except Exception as exc:
            logger.debug(
                "fundamentals_scanner.earnings_failed symbol=%s err=%s", sym, str(exc)[:120]
            )
            return sym, None

    pairs = await asyncio.gather(*(_one(s) for s in symbols), return_exceptions=False)
    return dict(pairs)


def _ratios_dump_to_scoring_payload(ratios: dict[str, Any]) -> dict[str, Any]:
    """Map FMP ratios-ttm dump into scoring_engine nested structure."""

    def g(*keys: str) -> object | None:
        for k in keys:
            if k in ratios and ratios[k] is not None:
                return ratios[k]
        return None

    valuation_ttm = {
        "peRatio": g("peRatioTTM"),
        "pbRatio": g("priceToBookRatioTTM"),
        "psRatio": g("priceToSalesRatioTTM"),
        "evEbitda": g("enterpriseValueMultipleTTM"),
        "pegRatio": g("pegRatioTTM", "priceEarningsToGrowthRatioTTM"),
        "evSales": g("evToSalesTTM"),
        "evFcf": g("evToFreeCashFlowTTM"),
        "pFcf": g("priceToFreeCashFlowsRatioTTM"),
    }
    profitability_ttm = {
        "roe": g("returnOnEquityTTM"),
        "roa": g("returnOnAssetsTTM"),
        "roic": g("returnOnInvestedCapitalTTM"),
        "roce": g("returnOnCapitalEmployedTTM"),
        "grossMargin": g("grossProfitMarginTTM"),
        "operatingMargin": g("operatingProfitMarginTTM"),
        "netMargin": g("netProfitMarginTTM"),
    }
    debt_ttm = {
        "debtEquity": g("debtEquityRatioTTM"),
        "currentRatio": g("currentRatioTTM"),
        "quickRatio": g("quickRatioTTM"),
        "netDebtToEbitda": g("netDebtToEBITDATTM", "netDebtToEBITDA"),
        "interestCoverage": g("interestCoverageTTM"),
    }
    div_yield = g("dividendYieldTTM", "dividendYieldPercentageTTM")
    if isinstance(div_yield, int | float) and div_yield > 1.0:
        div_yield = float(div_yield) / 100.0
    payout = g("payoutRatioTTM")
    if isinstance(payout, int | float) and payout > 1.0:
        payout = float(payout) / 100.0

    return {
        "valuation": {"ttm": valuation_ttm},
        "profitability": {"ttm": profitability_ttm},
        "debt": {"ttm": debt_ttm},
        "growth": {"yoy": {}, "cagr": {}},
        "dividends": {"metricas": {"yield": div_yield, "payoutRatio": payout}},
        "technical": {},
    }


def _forensic_delta(fin: dict[str, Any] | None) -> tuple[float, list[str]]:
    if not fin:
        return 0.0, []
    delta = 0.0
    reasons: list[str] = []
    z = fin.get("altmanZScore")
    if isinstance(z, int | float):
        zf = float(z)
        if zf < 1.81:
            delta -= 12.0
            reasons.append(f"Altman Z en zona distress ({zf:.2f}).")
        elif zf < 2.99:
            delta += 1.0
            reasons.append(f"Altman Z en zona gris ({zf:.2f}).")
        else:
            delta += 5.0
            reasons.append(f"Altman Z saludable ({zf:.2f}).")

    p = fin.get("piotroskiScore")
    if isinstance(p, int | float):
        pi = int(p)
        if pi <= 3:
            delta -= 10.0
            reasons.append(f"Piotroski débil ({pi}/9).")
        elif pi <= 5:
            delta -= 3.0
            reasons.append(f"Piotroski moderado ({pi}/9).")
        elif pi <= 7:
            delta += 3.0
            reasons.append(f"Piotroski sólido ({pi}/9).")
        else:
            delta += 7.0
            reasons.append(f"Piotroski fuerte ({pi}/9).")

    return delta, reasons


def _earnings_sentiment_delta(surprises: list[dict[str, Any]] | None) -> tuple[float, list[str]]:
    """Score based on last 4 earnings surprises (average surprise %)."""
    if not surprises:
        return 0.0, []

    delta = 0.0
    reasons: list[str] = []

    # Check last surprise
    last = surprises[0]
    s_pct = last.get("earningsSurprisePercentage")
    if isinstance(s_pct, int | float):
        if s_pct > 10.0:
            delta += 4.0
            reasons.append(f"Último earning: sorpresa positiva fuerte ({s_pct:.1f}%).")
        elif s_pct < -5.0:
            delta -= 6.0
            reasons.append(f"Último earning: sorpresa negativa ({s_pct:.1f}%).")

    # Check trend (average of last 4)
    all_surprises = [
        s.get("earningsSurprisePercentage")
        for s in surprises
        if isinstance(s.get("earningsSurprisePercentage"), (int, float))
    ]
    if len(all_surprises) >= 2:
        avg = float(sum(all_surprises) / len(all_surprises))
        if avg > 5.0:
            delta += 3.0
            reasons.append(f"Tendencia earnings: positiva (avg {avg:.1f}%).")
        elif avg < -2.0:
            delta -= 4.0
            reasons.append(f"Tendencia earnings: decepcionante (avg {avg:.1f}%).")

    return delta, reasons


def _heuristic_fundamentals_score(ratios: dict[str, Any]) -> tuple[float, list[str]]:
    score = 50.0
    reasons: list[str] = []
    cur = ratios.get("currentRatioTTM")
    if isinstance(cur, int | float) and cur >= 1.5:
        score += 8.0
        reasons.append("Liquidity: current ratio healthy (>=1.5).")
    elif isinstance(cur, int | float) and cur < 1.0:
        score -= 10.0
        reasons.append("Liquidity stress: current ratio below 1.0.")

    de = ratios.get("debtEquityRatioTTM")
    if isinstance(de, int | float) and de < 0.8:
        score += 6.0
        reasons.append("Leverage: conservative debt/equity.")
    elif isinstance(de, int | float) and de > 2.5:
        score -= 8.0
        reasons.append("Leverage: elevated debt/equity.")

    roe = ratios.get("returnOnEquityTTM")
    if isinstance(roe, int | float) and roe > 0.15:
        score += 7.0
        reasons.append("Quality: strong ROE (TTM).")
    elif isinstance(roe, int | float) and roe < 0.05:
        score -= 6.0
        reasons.append("Quality: weak ROE (TTM).")

    return max(0.0, min(100.0, score)), reasons


def _enabled_fundamental_indicators(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
) -> list[ScannerIndicatorDefinition]:
    requested = set(customization.enabled_indicators or [])
    out: list[ScannerIndicatorDefinition] = []
    for ind in indicators:
        if ind.module != "fundamentals":
            continue
        if customization.enabled_indicators is None and not ind.default_enabled:
            continue
        if customization.enabled_indicators is not None and ind.key not in requested:
            continue
        out.append(ind)
    return out


def synthesize_fundamentals_signal(
    row: MarketScannerRow,
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    ratios: dict[str, Any] | None,
    financial_scores: dict[str, Any] | None = None,
    key_metrics: dict[str, Any] | None = None,
    earnings_surprises: list[dict[str, Any]] | None = None,
) -> ScannerModuleSignal:
    """Build fundamentals ``ScannerModuleSignal`` from FMP ratios + forensic scores."""
    enabled = _enabled_fundamental_indicators(customization, indicators)
    if not enabled:
        return neutral_module_signal("fundamentals", "Fundamentals module disabled.")

    if not ratios:
        from backend.layer_1_data.fetchers.fmp_client import fmp_client_configured

        if not fmp_client_configured():
            return neutral_module_signal(
                "fundamentals",
                "FMP API keys not configured — fundamentals module unavailable.",
                engine_count=len(enabled),
            )
        return neutral_module_signal(
            "fundamentals",
            "No FMP ratios TTM snapshot (provider miss or symbol unsupported).",
            engine_count=len(enabled),
        )

    payload = _ratios_dump_to_scoring_payload(ratios)
    scored = calcular_scores(payload)
    total = scored.get("total")
    reasons: list[str] = []
    if isinstance(total, int):
        base = float(total)
        reasons.append(
            f"Módulos FMP (valoración/rentabilidad/deuda/div): total ponderado {total}/100 "
            f"({scored.get('labels', {}).get('total', '')})."
        )
    else:
        base, h_reasons = _heuristic_fundamentals_score(ratios)
        reasons.extend(h_reasons)
        reasons.append("Scoring parcial: faltan campos para total institucional — heurística TTM.")

    f_delta, f_reasons = _forensic_delta(financial_scores)
    reasons.extend(f_reasons)

    e_delta, e_reasons = _earnings_sentiment_delta(earnings_surprises)
    reasons.extend(e_reasons)

    # Key Metrics TTM quick check (FCF yield)
    if key_metrics:
        fcf_yield = key_metrics.get("freeCashFlowYieldTTM")
        if isinstance(fcf_yield, (int, float)):
            if fcf_yield > 0.08:
                f_delta += 4.0
                reasons.append(f"FCF Yield generoso ({fcf_yield * 100:.1f}%).")
            elif fcf_yield < 0.02:
                f_delta -= 3.0
                reasons.append(f"FCF Yield bajo o negativo ({fcf_yield * 100:.1f}%).")

    score = max(0.0, min(100.0, base + f_delta + e_delta))

    if row.direction == "bearish":
        score = 100.0 - score

    engines = 2 if financial_scores else 1
    avail = engines if (total is not None or financial_scores) else 1
    conf = 0.62 if (total is not None and financial_scores) else 0.52 if total is not None else 0.45

    return build_module_signal(
        "fundamentals",
        score,
        conf,
        engine_count=len(enabled),
        available_count=min(len(enabled), avail),
        reasons=reasons or ["Fundamentals synthesized from FMP ratios TTM."],
        warnings=[],
    )
