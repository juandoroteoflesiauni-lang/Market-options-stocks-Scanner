from __future__ import annotations
from typing import Any
"""Fuse Phase-A technical metrics with news/sentiment/catalyst context (Scanner desk).

Deterministic transforms only — no cross-layer specialist imports. Used by POST /fusion-enrich
after the client merges scan rows with the context rail.
"""


import math


def _finite(x: object) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, int | float):
        v = float(x)
        return v if math.isfinite(v) else None
    return None


def sentiment_scalar(sent: dict[str, Any] | None) -> float:
    """Map sentiment payload to [-1, 1]."""
    if not sent:
        return 0.0
    raw = sent.get("score") or sent.get("sentiment_score")
    v = _finite(raw)
    if v is not None:
        if -1.0 <= v <= 1.0:
            return max(-1.0, min(1.0, v))
        if 0.0 <= v <= 1.0:
            return max(-1.0, min(1.0, (v - 0.5) * 2.0))
        return max(-1.0, min(1.0, v / 100.0))
    label = str(sent.get("label") or sent.get("status") or "").lower()
    if any(k in label for k in ("positive", "bull", "bullish")):
        return 0.65
    if any(k in label for k in ("negative", "bear", "bearish")):
        return -0.65
    return 0.0


def _sentiment_source(sent: dict[str, Any] | None) -> str | None:
    if not sent:
        return None
    source = str(sent.get("source") or "").strip()
    return source or None


def _sentiment_confidence(sent: dict[str, Any] | None) -> float | None:
    if not sent:
        return None
    confidence = _finite(sent.get("confidence"))
    if confidence is None:
        return None
    return max(0.0, min(1.0, confidence))


def catalyst_scalar(cat: dict[str, Any] | None) -> tuple[float, float]:
    """Return (event_risk [0,1], tone_bias [-1,1])."""
    if not cat:
        return 0.0, 0.0
    er = cat.get("event_risk_score")
    risk = _finite(er)
    risk01 = max(0.0, min(1.0, float(risk))) if risk is not None else 0.0
    tone = str(cat.get("tone") or "").upper()
    tb = 0.0
    if "BULL" in tone:
        tb = 0.4
    elif "BEAR" in tone:
        tb = -0.4
    return risk01, tb


def argentina_bias(symbol: str, argentina_summary: dict[str, Any] | None) -> float:
    """Small overlay when Argentina summary implies local tape emphasis."""
    if not argentina_summary:
        return 0.0
    sym = symbol.upper()
    ar_hints = ("GGAL", "YPF", "PAMP", "BMA", "TXAR", "ALUA", "COME")
    if any(sym.startswith(h) or h in sym for h in ar_hints):
        risk = argentina_summary.get("risk_country") or argentina_summary.get("country_risk")
        if isinstance(risk, int | float) and float(risk) > 1800:
            return -0.03
        return 0.04
    return 0.0


def _metrics_for_tf(row: dict[str, Any], tf: str) -> dict[str, Any]:
    deep = row.get("deep_metrics") or {}
    tfm = deep.get(tf)
    if isinstance(tfm, dict):
        return tfm
    sigs = row.get("signals") or {}
    s = sigs.get(tf)
    if isinstance(s, dict):
        m = s.get("metrics")
        if isinstance(m, dict):
            return m
    return {}


def enrich_row_fusion_metrics(
    row: dict[str, Any],
    *,
    sentiment_by_symbol: dict[str, dict[str, Any]],
    catalysts_by_symbol: dict[str, dict[str, Any]],
    primary_tf: str,
    argentina_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return updated row dict with fusion keys merged into deep_metrics[primary_tf]."""
    symbol = str(row.get("symbol") or "").upper()
    sent = sentiment_by_symbol.get(symbol)
    cat = catalysts_by_symbol.get(symbol)
    s = sentiment_scalar(sent if isinstance(sent, dict) else None)
    sentiment_source = _sentiment_source(sent if isinstance(sent, dict) else None)
    sentiment_confidence = _sentiment_confidence(sent if isinstance(sent, dict) else None)
    cat_risk, cat_tone = catalyst_scalar(cat if isinstance(cat, dict) else None)
    ar_bias = argentina_bias(symbol, argentina_summary)

    m = dict(_metrics_for_tf(row, primary_tf))
    rsi = _finite(m.get("rsi"))
    macd_hist = _finite(m.get("macd_hist"))
    rvol = _finite(m.get("relative_volume"))
    atr_pct = _finite(m.get("atr_pct"))
    ofi = _finite(m.get("ofi_proxy"))
    vpin = _finite(m.get("vpin_proxy"))
    imb = _finite(m.get("volume_imbalance"))
    change_pct = _finite(m.get("change_pct"))

    sar: float | None = None
    if rsi is not None:
        sar = max(0.0, min(100.0, rsi + 9.0 * s + 5.0 * cat_tone + 50.0 * ar_bias))

    sam: float | None = None
    if macd_hist is not None:
        sam = macd_hist * (1.0 + 0.35 * s + 0.15 * cat_tone)

    flow_parts: list[float] = []
    if ofi is not None:
        flow_parts.append((ofi + 1.0) / 2.0)
    if imb is not None:
        flow_parts.append((imb + 1.0) / 2.0)
    if vpin is not None:
        flow_parts.append(max(0.0, 1.0 - min(1.0, vpin)))
    flow_mid = sum(flow_parts) / len(flow_parts) if flow_parts else 0.5
    composite_flow = max(
        0.0,
        min(100.0, 45.0 + 28.0 * flow_mid + 22.0 * ((s + 1.0) / 2.0) + 10.0 * (1.0 - cat_risk)),
    )

    alignment = 50.0
    if change_pct is not None and s != 0:
        aligned = (change_pct >= 0 and s > 0.1) or (change_pct < 0 and s < -0.1)
        alignment += 18.0 if aligned else -12.0
    if rvol is not None:
        alignment += min(22.0, max(0.0, (rvol - 1.0) * 12.0))
    alignment += (1.0 - cat_risk) * 15.0
    if atr_pct is not None and atr_pct > 8.0:
        alignment -= 8.0
    news_catalyst_alignment = max(0.0, min(100.0, alignment))

    regime_sent = 50.0
    if rsi is not None:
        if 48.0 <= rsi <= 72.0:
            regime_sent += 12.0
        elif rsi < 35.0:
            regime_sent -= 8.0
        elif rsi > 78.0:
            regime_sent -= 6.0
    regime_sent += 18.0 * s
    regime_sent += 10.0 * (1.0 - cat_risk) * cat_tone
    regime_sentiment_score = max(0.0, min(100.0, regime_sent))

    tech_component = _finite(row.get("scanner_score"))
    tc = tech_component if tech_component is not None else 50.0
    rsi_component = ((rsi or 50.0) / 100.0) * 100.0
    bull_bear = (
        0.34 * tc
        + 0.18 * rsi_component
        + 0.22 * (50.0 + 50.0 * s)
        + 0.14 * (100.0 * (1.0 - cat_risk))
        + 0.12 * composite_flow
        + 50.0 * ar_bias
    )
    bull_bear_fusion_index = max(0.0, min(100.0, bull_bear))

    fusion_keys = {
        "sentiment_adjusted_rsi": round(sar, 2) if sar is not None else None,
        "sentiment_adjusted_momentum": round(sam, 4) if sam is not None else None,
        "composite_flow_fusion": round(composite_flow, 2),
        "news_catalyst_alignment": round(news_catalyst_alignment, 2),
        "regime_sentiment_score": round(regime_sentiment_score, 2),
        "bull_bear_fusion_index": round(bull_bear_fusion_index, 2),
        "fusion_sentiment_scalar": round(s, 4),
        "fusion_sentiment_source": sentiment_source,
        "fusion_sentiment_confidence": (
            round(sentiment_confidence, 4) if sentiment_confidence is not None else None
        ),
        "fusion_catalyst_risk": round(cat_risk, 4),
    }

    out = dict(row)
    deep_out = dict(out.get("deep_metrics") or {})
    tf_block = dict(deep_out.get(primary_tf) or {})
    for k, v in fusion_keys.items():
        tf_block[k] = v
    deep_out[primary_tf] = tf_block
    out["deep_metrics"] = deep_out
    return out


def enrich_scanner_rows(
    rows: list[dict[str, Any]],
    *,
    sentiment_by_symbol: dict[str, dict[str, Any]],
    catalysts_by_symbol: dict[str, dict[str, Any]],
    primary_timeframe: str,
    argentina_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply fusion enrichment to each row (dict-shaped JSON payloads)."""
    tf = "1D" if str(primary_timeframe).lower() == "1d" else str(primary_timeframe)
    return [
        enrich_row_fusion_metrics(
            dict(r),
            sentiment_by_symbol=sentiment_by_symbol,
            catalysts_by_symbol=catalysts_by_symbol,
            primary_tf=tf,
            argentina_summary=argentina_summary,
        )
        for r in rows
    ]
