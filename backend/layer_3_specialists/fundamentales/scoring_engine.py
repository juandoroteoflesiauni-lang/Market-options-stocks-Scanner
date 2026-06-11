"""Scoring engine for fundamental analysis modules.

Implements the 0-100 scoring system per module with weighted total score
as specified in the QuantumAnalyzer technical report.
"""

from __future__ import annotations

from typing import Any


def calcular_score(
    valor: float | None,
    umbral_100: float,
    umbral_0: float,
    mayor_es_mejor: bool = True,
) -> int | None:
    """Normalize a value to 0-100 scale between two thresholds."""
    if valor is None:
        return None
    if mayor_es_mejor:
        if valor >= umbral_100:
            return 100
        if valor <= umbral_0:
            return 0
        return int((valor - umbral_0) / (umbral_100 - umbral_0) * 100)
    else:
        if valor <= umbral_100:
            return 100
        if valor >= umbral_0:
            return 0
        return int((umbral_0 - valor) / (umbral_0 - umbral_100) * 100)


def get_calificacion(score: int | None) -> str:
    """Return label for a score value."""
    if score is None:
        return "SIN DATOS"
    if score >= 85:
        return "BUENO"
    if score >= 60:
        return "ACEPTABLE"
    if score >= 35:
        return "DÉBIL"
    return "MALO"


def _avg_scores(scores: list[int | None], weights: list[float]) -> int | None:
    """Weighted average of scores, skipping None values."""
    total_w = 0.0
    total_s = 0.0
    for s, w in zip(scores, weights, strict=False):
        if s is not None:
            total_s += s * w
            total_w += w
    if total_w == 0:
        return None
    return int(total_s / total_w)


# ══════════════════════════════════════════════════════════════════════════════
# PER-MODULE SCORING
# ══════════════════════════════════════════════════════════════════════════════


def score_valoracion(data: dict[str, Any]) -> int | None:
    """Score valuation module (lower multiples = better)."""
    ttm = data.get("valuation", {}).get("ttm", {})
    scores = [
        calcular_score(ttm.get("peRatio"), 15, 40, mayor_es_mejor=False),
        calcular_score(ttm.get("pbRatio"), 3, 12, mayor_es_mejor=False),
        calcular_score(ttm.get("psRatio"), 2, 15, mayor_es_mejor=False),
        calcular_score(ttm.get("evEbitda"), 10, 30, mayor_es_mejor=False),
        calcular_score(ttm.get("pegRatio"), 1, 2, mayor_es_mejor=False),
        calcular_score(ttm.get("evSales"), 3, 15, mayor_es_mejor=False),
        calcular_score(ttm.get("evFcf"), 20, 50, mayor_es_mejor=False),
        calcular_score(ttm.get("pFcf"), 15, 50, mayor_es_mejor=False),
    ]
    weights = [0.20, 0.10, 0.10, 0.15, 0.15, 0.10, 0.10, 0.10]
    return _avg_scores(scores, weights)


def score_rentabilidad(data: dict[str, Any]) -> int | None:
    """Score profitability module."""
    ttm = data.get("profitability", {}).get("ttm", {})
    scores = [
        calcular_score(ttm.get("roe"), 0.25, 0.05),
        calcular_score(ttm.get("roa"), 0.15, 0.03),
        calcular_score(ttm.get("roic"), 0.20, 0.05),
        calcular_score(ttm.get("roce"), 0.20, 0.05),
        calcular_score(ttm.get("grossMargin"), 0.60, 0.20),
        calcular_score(ttm.get("operatingMargin"), 0.30, 0.05),
        calcular_score(ttm.get("netMargin"), 0.20, 0.03),
    ]
    weights = [0.20, 0.15, 0.20, 0.15, 0.10, 0.10, 0.10]
    return _avg_scores(scores, weights)


def score_deuda(data: dict[str, Any]) -> int | None:
    """Score debt module (lower debt = better)."""
    ttm = data.get("debt", {}).get("ttm", {})
    scores = [
        calcular_score(ttm.get("debtEquity"), 0.3, 2.0, mayor_es_mejor=False),
        calcular_score(ttm.get("currentRatio"), 2.0, 0.8),
        calcular_score(ttm.get("quickRatio"), 1.5, 0.5),
        calcular_score(ttm.get("netDebtToEbitda"), 0.5, 4.0, mayor_es_mejor=False),
        calcular_score(ttm.get("interestCoverage"), 10, 2),
    ]
    weights = [0.25, 0.20, 0.15, 0.25, 0.15]
    return _avg_scores(scores, weights)


def score_crecimiento(data: dict[str, Any]) -> int | None:
    """Score growth module."""
    yoy = data.get("growth", {}).get("yoy", {})
    cagr = data.get("growth", {}).get("cagr", {})
    scores = [
        calcular_score(yoy.get("revenueGrowth"), 0.30, 0.0),
        calcular_score(yoy.get("netIncomeGrowth"), 0.25, 0.0),
        calcular_score(yoy.get("epsGrowth"), 0.25, 0.0),
        calcular_score(yoy.get("fcfGrowth"), 0.30, 0.0),
        calcular_score(cagr.get("epsCagr5y"), 0.15, 0.0),
        calcular_score(cagr.get("revenueCagr5y"), 0.10, 0.0),
    ]
    weights = [0.20, 0.15, 0.15, 0.15, 0.20, 0.15]
    return _avg_scores(scores, weights)


def score_dividendo(data: dict[str, Any]) -> int | None:
    """Score dividend module."""
    m = data.get("dividends", {}).get("metricas", {})
    div_yield = m.get("yield")
    if div_yield is not None:
        div_yield = div_yield / 100 if div_yield > 1 else div_yield
    payout = m.get("payoutRatio")
    scores = [
        _score_yield(div_yield),
        calcular_score(payout, 0.40, 0.80, mayor_es_mejor=False),
    ]
    dgi_cagr = data.get("dividends", {}).get("dgiCagr5y")
    if dgi_cagr is not None:
        scores.append(calcular_score(dgi_cagr, 0.10, 0.0))
    weights = [0.40, 0.30] + ([0.30] if len(scores) == 3 else [])
    return _avg_scores(scores, weights)


def _score_yield(y: float | None) -> int | None:
    """DGI ideal yield is 2-4%. Too low or too high penalized."""
    if y is None:
        return None
    if 0.02 <= y <= 0.04:
        return 100
    if 0.01 <= y < 0.02 or 0.04 < y <= 0.06:
        return 65
    if y < 0.005 or y > 0.08:
        return 0
    return 40


def score_tecnico(data: dict[str, Any]) -> int | None:
    """Score technical module."""
    tech = data.get("technical", {})
    rsi = tech.get("rsi")
    golden = tech.get("goldenCross")

    rsi_score = None
    if rsi is not None:
        if 30 <= rsi <= 50:
            rsi_score = 88
        elif 50 < rsi <= 65:
            rsi_score = 55
        elif rsi > 70:
            rsi_score = 12
        elif rsi < 30:
            rsi_score = 70
        else:
            rsi_score = 40

    trend_score = None
    if golden is not None:
        trend_score = 88 if golden else 12

    scores = [trend_score, rsi_score]
    weights = [0.55, 0.45]
    return _avg_scores(scores, weights)


# ══════════════════════════════════════════════════════════════════════════════
# TOTAL SCORE CALCULATION
# ══════════════════════════════════════════════════════════════════════════════

MODULE_WEIGHTS = {
    "rentabilidad": 0.25,
    "valoracion": 0.20,
    "deuda": 0.20,
    "crecimiento": 0.20,
    "dividendo": 0.10,
    "tecnico": 0.05,
}


def calcular_scores(data: dict[str, Any]) -> dict[str, Any]:
    """Calculate all module scores and weighted total. Returns scores dict."""
    module_scores = {
        "valoracion": score_valoracion(data),
        "rentabilidad": score_rentabilidad(data),
        "deuda": score_deuda(data),
        "crecimiento": score_crecimiento(data),
        "dividendo": score_dividendo(data),
        "tecnico": score_tecnico(data),
    }

    labels = {k: get_calificacion(v) for k, v in module_scores.items()}

    total_w = 0.0
    total_s = 0.0
    for mod, score in module_scores.items():
        if score is not None:
            total_s += score * MODULE_WEIGHTS[mod]
            total_w += MODULE_WEIGHTS[mod]

    total = int(total_s / total_w) if total_w > 0 else None
    labels["total"] = get_calificacion(total)

    return {
        "total": total,
        **module_scores,
        "labels": labels,
    }
