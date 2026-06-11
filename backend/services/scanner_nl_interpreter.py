"""Keyword-based scanner query interpreter (no LLM — deterministic hints)."""

from __future__ import annotations

import re

from backend.domain.market_scanner_models import ScannerModuleKey, ScannerNaturalLanguageResponse


def interpret_scanner_query(
    query: str, active_universe: str | None = None
) -> ScannerNaturalLanguageResponse:
    """Map NL-ish phrases to universe/module/filter hints."""
    q = (query or "").lower()
    matched: list[str] = []
    explanation_parts: list[str] = []

    suggested_universe = active_universe
    suggested_min_score: float | None = None
    modules: set[ScannerModuleKey] = {"technical", "probabilistic", "options_gex"}
    indicators: set[str] = set()

    if re.search(r"\b(gamma|gex|dealer|0dte|options)\b", q):
        matched.append("options/GEX")
        indicators.update({"net_gex", "gamma_flip", "dealer_bias", "flow_signal"})
        explanation_parts.append("Prioriza módulo opciones/GEX y niveles gamma.")

    if re.search(r"\b(argentina|merval|ggal|ccl|bono)\b", q):
        matched.append("Argentina")
        suggested_universe = "argentina_plus"
        explanation_parts.append("Universo Argentina Plus para lectura local.")

    if re.search(r"\b(squeeze|ignition|compression)\b", q):
        matched.append("squeeze")
        indicators.add("squeeze")
        explanation_parts.append("Activa indicadores de squeeze / ignición.")

    if re.search(r"\b(vpin|toxic|flow|microstructure|tape)\b", q):
        matched.append("microstructure")
        indicators.update({"vpin", "order_flow_delta", "lob_microstructure"})
        explanation_parts.append("Enfatiza proxies VPIN/OFI y microestructura.")

    if re.search(r"\b(momentum|rsi|breakout|trend)\b", q):
        matched.append("momentum")
        indicators.update({"rsi", "macd", "ema_7_14"})
        explanation_parts.append("Sesgo técnico momentum.")

    if re.search(r"\b(high\s*score|strong\s*buy|elite|top)\b", q):
        matched.append("high_score")
        suggested_min_score = 70.0
        explanation_parts.append("Sube umbral mínimo de score.")

    if re.search(r"\b(loose|watchlist|broad)\b", q):
        matched.append("broad")
        suggested_min_score = 25.0
        explanation_parts.append("Relaja umbral para cobertura más amplia.")

    if not matched:
        explanation_parts.append(
            "Sin términos reconocidos — usa keywords: gamma, Argentina, VPIN, momentum."
        )

    return ScannerNaturalLanguageResponse(
        matched_terms=matched,
        suggested_universe=suggested_universe,
        suggested_min_score=suggested_min_score,
        suggested_modules=sorted(modules),
        suggested_indicators=sorted(indicators) if indicators else None,
        explanation=" ".join(explanation_parts),
    )
