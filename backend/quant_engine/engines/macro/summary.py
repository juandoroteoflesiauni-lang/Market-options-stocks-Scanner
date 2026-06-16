from __future__ import annotations
from typing import Any
"""Macro / micro desk summary (deterministic; IO happens in Layer 4 services)."""




def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def macro_desk_summary_from_context(symbol: str, ctx: dict[str, Any] | None) -> dict[str, Any]:
    """Build macro desk overlay from a pre-fetched context dict (FRED + calendar)."""
    _ = symbol.upper()
    limitations: list[str] = []
    if not ctx:
        limitations.append("macro_context_unavailable")
        return {
            "tone": "neutral",
            "score": 52.0,
            "headline": "Macro/micro: sin contexto en vivo — configure FRED_API_KEY y FMP para calendario.",
            "limitations": limitations,
            "payload": {},
        }

    limitations.extend([str(x) for x in ctx.get("limitations", []) if x])
    sources = ctx.get("sources") or {}
    fred = ctx.get("fred") or {}
    vix = ctx.get("vix")
    cal = ctx.get("calendar") or {}

    if not sources.get("fred") and not sources.get("fmp_calendar"):
        limitations.append("no_live_macro_feeds")
        return {
            "tone": "neutral",
            "score": 51.0,
            "headline": "Macro/micro: feeds macro no disponibles (FRED/FMP).",
            "limitations": limitations,
            "payload": {"sources": sources},
        }

    score = 52.0
    parts: list[str] = []

    if isinstance(vix, int | float):
        vx = float(vix)
        parts.append(f"VIX spot ~{vx:.1f}.")
        if vx >= 30:
            score -= 12.0
            parts.append("Vol implícita elevada: sesgo defensivo.")
        elif vx >= 22:
            score -= 5.0
        elif vx <= 14:
            score += 4.0
            parts.append("Vol contenida (riesgo de complacencia).")

    spread = fred.get("yield_spread_10y2y")
    if isinstance(spread, int | float):
        sp = float(spread)
        parts.append(f"Spread 10Y-2Y ~{sp:.2f} pp.")
        if sp < 0:
            score -= 10.0
            parts.append("Curva invertida: señal clásica de recesión / tightening financiero.")
        elif sp > 0.8:
            score += 4.0

    cpi_yoy = fred.get("cpi_yoy")
    if isinstance(cpi_yoy, int | float):
        cy = float(cpi_yoy)
        parts.append(f"CPI YoY ~{cy:.1f}%.")
        if cy > 4.0:
            score -= 6.0
        elif cy < 2.5:
            score += 2.0

    unemp = fred.get("unemployment_rate")
    if isinstance(unemp, int | float):
        parts.append(f"Desempleo ~{float(unemp):.2f}%.")

    hi = cal.get("high_impact_14d")
    tot = cal.get("events_14d")
    if isinstance(hi, int | float) and isinstance(tot, int | float):
        hi_n, tot_n = int(hi), int(tot)
        parts.append(f"Calendario 14d: {tot_n} eventos ({hi_n} alto impacto).")
        if hi_n >= 8:
            score -= 5.0
            parts.append("Ventana densa de eventos — conviene reducir apalancamiento táctico.")

    tone = "neutral"
    if score >= 58:
        tone = "bullish"
    elif score <= 44:
        tone = "bearish"

    headline = " ".join(parts) if parts else "Macro/micro: contexto institucional parcial."
    return {
        "tone": tone,
        "score": _clamp(score),
        "headline": headline,
        "limitations": limitations,
        "payload": {
            "sources": sources,
            "fred_keys": [k for k, v in fred.items() if v is not None],
            "calendar": cal,
            "as_of": ctx.get("as_of"),
        },
    }


def macro_desk_summary(symbol: str) -> dict[str, Any]:
    """Backward-compatible entrypoint when no live context is wired."""
    return macro_desk_summary_from_context(symbol, None)
