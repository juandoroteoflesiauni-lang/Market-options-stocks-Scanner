from __future__ import annotations
from typing import Any
"""Macro / micro context module for Market Scanner (lightweight, no cross-specialist coupling)."""



from backend.domain.market_scanner_models import (
    MarketScannerRow,
    ScannerCustomization,
    ScannerIndicatorDefinition,
    ScannerModuleSignal,
)
from backend.quant_engine.engines.macro.summary import macro_desk_summary_from_context
from backend.services.market_scanner_module_signals import (
    build_module_signal,
    neutral_module_signal,
)


def _enabled_macro_indicators(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
) -> list[ScannerIndicatorDefinition]:
    requested = set(customization.enabled_indicators or [])
    out: list[ScannerIndicatorDefinition] = []
    for ind in indicators:
        if ind.module != "macro_micro":
            continue
        if customization.enabled_indicators is None and not ind.default_enabled:
            continue
        if customization.enabled_indicators is not None and ind.key not in requested:
            continue
        out.append(ind)
    return out


def synthesize_macro_micro_signal(
    row: MarketScannerRow,
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    macro_ctx: dict[str, Any] | None = None,
) -> ScannerModuleSignal:
    """Desk-style macro/micro overlay from optional live context (FRED + FMP calendar)."""
    enabled = _enabled_macro_indicators(customization, indicators)
    if not enabled:
        return neutral_module_signal("macro_micro", "Macro/micro module disabled.")

    summary = macro_desk_summary_from_context(row.symbol, macro_ctx)
    base = float(summary.get("score", 50.0))
    tone = str(summary.get("tone", "neutral"))
    reasons = [str(summary.get("headline", "Macro/micro baseline."))]
    lim = [str(x) for x in summary.get("limitations", []) if x]
    sources = (macro_ctx or {}).get("sources") or {}
    live_n = sum(1 for v in sources.values() if v)
    conf = 0.58 if live_n >= 2 else 0.44 if live_n == 1 else 0.32
    avail = 1 if live_n else 0

    if row.direction == "bearish":
        base = 100.0 - base

    return build_module_signal(
        "macro_micro",
        max(0.0, min(100.0, base)),
        conf,
        engine_count=len(enabled),
        available_count=avail,
        reasons=reasons + [f"Desk tone: {tone}"],
        warnings=lim[:4],
    )
