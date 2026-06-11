"""Institutional-grade scoring utilities for Market Scanner.

Loads optional JSON grade thresholds, confidence-weighted Phase-B blending,
score uncertainty bands, and lightweight risk hints (Kelly-lite, VaR proxy).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import ScannerGrade, ScannerModuleSignal

logger = get_logger(__name__)

DEFAULT_GRADE_THRESHOLDS_100: dict[str, dict[str, int]] = {
    "5m": {"A+": 80, "A": 65, "B": 50, "WATCH": 35, "C": 0},
    "15m": {"A+": 78, "A": 62, "B": 48, "WATCH": 32, "C": 0},
    "1h": {"A+": 75, "A": 60, "B": 45, "WATCH": 30, "C": 0},
    "1D": {"A+": 70, "A": 58, "B": 45, "WATCH": 30, "C": 0},
}

_CACHED_THRESHOLDS: dict[str, dict[str, int]] | None = None
_CACHED_THRESHOLDS_MTIME: float | None = None


def _env_path(name: str, default: str = "") -> Path | None:
    raw = os.getenv(name, default).strip()
    if not raw:
        return None
    return Path(raw)


def load_grade_thresholds_from_disk() -> dict[str, dict[str, int]]:
    """Load grade thresholds from JSON if path set and file valid; else defaults."""
    global _CACHED_THRESHOLDS, _CACHED_THRESHOLDS_MTIME
    path = _env_path("MARKET_SCANNER_GRADE_THRESHOLDS_JSON")
    if path is None or not path.is_file():
        return {k: dict(v) for k, v in DEFAULT_GRADE_THRESHOLDS_100.items()}

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {k: dict(v) for k, v in DEFAULT_GRADE_THRESHOLDS_100.items()}

    if _CACHED_THRESHOLDS is not None and mtime == _CACHED_THRESHOLDS_MTIME:
        return _CACHED_THRESHOLDS

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("market_scanner_scoring.thresholds_load_failed path=%s err=%s", path, exc)
        return {k: dict(v) for k, v in DEFAULT_GRADE_THRESHOLDS_100.items()}

    if not isinstance(raw, dict):
        return {k: dict(v) for k, v in DEFAULT_GRADE_THRESHOLDS_100.items()}

    merged: dict[str, dict[str, int]] = {
        k: dict(v) for k, v in DEFAULT_GRADE_THRESHOLDS_100.items()
    }
    for tf_key, bands in raw.items():
        tf = "1D" if str(tf_key).lower() == "1d" else str(tf_key)
        if tf not in merged or not isinstance(bands, dict):
            continue
        for grade in ("A+", "A", "B", "WATCH", "C"):
            if grade in bands:
                try:
                    merged[tf][grade] = int(bands[grade])
                except (TypeError, ValueError):
                    continue

    _CACHED_THRESHOLDS = merged
    _CACHED_THRESHOLDS_MTIME = mtime
    logger.info("market_scanner_scoring.thresholds_loaded path=%s", path)
    return merged


def assign_grade(score_0_to_100: float, timeframe: str) -> ScannerGrade:
    bands = load_grade_thresholds_from_disk()
    thresholds = bands.get(timeframe, bands["15m"])
    if score_0_to_100 >= thresholds["A+"]:
        return "A+"
    if score_0_to_100 >= thresholds["A"]:
        return "A"
    if score_0_to_100 >= thresholds["B"]:
        return "B"
    if score_0_to_100 >= thresholds["WATCH"]:
        return "WATCH"
    return "C"


def module_score_std(module_signals: dict[str, ScannerModuleSignal]) -> float:
    """Dispersion of module scores (0–100); used as uncertainty input."""
    scores = [m.score for m in module_signals.values()]
    if len(scores) < 2:
        return 0.0
    mean = sum(scores) / len(scores)
    var = sum((s - mean) ** 2 for s in scores) / max(len(scores) - 1, 1)
    return float(var**0.5)


def score_confidence_band_68(
    base_score: float,
    module_signals: dict[str, ScannerModuleSignal],
) -> tuple[float, float]:
    """Approximate 68% band: tighten with mean module confidence, widen with dispersion."""
    confs = [m.confidence for m in module_signals.values() if m.confidence > 0]
    mean_conf = sum(confs) / len(confs) if confs else 0.35
    dispersion = module_score_std(module_signals)
    half_width = max(2.0, min(18.0, dispersion * 0.45 + (1.0 - mean_conf) * 12.0))
    lo = max(0.0, base_score - half_width)
    hi = min(100.0, base_score + half_width)
    return lo, hi


def blend_phase_b_scanner_score(
    row_base_score: float,
    module_signals: dict[str, ScannerModuleSignal],
    *,
    base_weight: float = 0.78,
    module_weight: float = 0.22,
    module_blend_weights: dict[str, float] | None = None,
    per_indicator_contributions: dict[str, float] | None = None,
    concentration_audit: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any]]:
    """Confidence- and desk-weighted blend of base scanner score and Phase-B modules."""
    if not module_signals:
        return max(0.0, min(100.0, row_base_score)), {
            "blend_mode": "base_only",
            "base_weight": 1.0,
            "module_weight": 0.0,
        }

    weights: list[float] = []
    scores: list[float] = []
    per_module_detail: dict[str, dict[str, float]] = {}
    for name, sig in module_signals.items():
        desk_w = 1.0
        if module_blend_weights is not None:
            desk_w = max(0.05, float(module_blend_weights.get(name, 1.0)))
        w = max(0.05, float(sig.confidence)) * desk_w
        weights.append(w)
        scores.append(float(sig.score))
        per_module_detail[name] = {
            "score": float(sig.score),
            "confidence": float(sig.confidence),
            "desk_weight": round(desk_w, 4),
            "blend_weight": round(w, 4),
        }

    tw = sum(weights)
    module_avg = sum(s * w for s, w in zip(scores, weights, strict=True)) / tw if tw else 50.0

    blended = row_base_score * base_weight + module_avg * module_weight
    blended = max(0.0, min(100.0, blended))
    audit: dict[str, Any] = {
        "blend_mode": "confidence_and_desk_weighted_modules",
        "base_weight": base_weight,
        "module_weight": module_weight,
        "module_avg": round(module_avg, 4),
        "module_dispersion": round(module_score_std(module_signals), 4),
        "per_module": per_module_detail,
    }
    if per_indicator_contributions:
        audit["per_indicator_contributions"] = {
            k: round(v, 4) for k, v in per_indicator_contributions.items()
        }
    if concentration_audit:
        audit["weight_concentration"] = concentration_audit
    return blended, audit


def kelly_fraction_lite(edge: float, avg_payoff: float = 1.0) -> float:
    """Fractional Kelly cap for scanner hints (not execution). edge in [-1,1]."""
    if avg_payoff <= 0:
        return 0.0
    k = edge / avg_payoff
    return float(max(0.0, min(0.25, k * 0.25)))


def var_proxy_pct(atr_pct: float | None, z: float = 1.65) -> float | None:
    """Single-day VaR-style proxy from ATR% (diagnostic only)."""
    if atr_pct is None or atr_pct <= 0:
        return None
    return float(z * atr_pct)


def build_risk_hints(
    scanner_score: float,
    direction: str,
    module_signals: dict[str, ScannerModuleSignal],
    atr_pct: float | None,
) -> dict[str, float | str]:
    """Layer-5-style hints without coupling to execution."""
    edge = (scanner_score - 50.0) / 50.0
    if direction == "bearish":
        edge = -abs(edge)
    elif direction == "bullish":
        edge = abs(edge)
    else:
        edge *= 0.35

    confs = [m.confidence for m in module_signals.values()]
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    kelly = kelly_fraction_lite(edge * max(0.2, mean_conf))
    var_p = var_proxy_pct(atr_pct)
    out: dict[str, float | str] = {
        "kelly_fraction_hint": round(kelly, 4),
        "edge_normalized": round(edge, 4),
        "mean_module_confidence": round(mean_conf, 4),
    }
    if var_p is not None:
        out["var_proxy_pct_1d_95"] = round(var_p, 4)
    return out


def summarize_universe_regime(rows: list[Any]) -> dict[str, Any]:
    """Cross-sectional regime summary from scanner rows (no extra IO)."""
    if not rows:
        return {"status": "empty"}
    bullish = sum(1 for r in rows if getattr(r, "direction", "") == "bullish")
    bearish = sum(1 for r in rows if getattr(r, "direction", "") == "bearish")
    n = len(rows)
    scores = [float(getattr(r, "scanner_score", 0) or 0) for r in rows]
    avg_score = sum(scores) / n if n else 0.0
    tone = "neutral"
    if bullish / max(n, 1) >= 0.55:
        tone = "risk_on"
    elif bearish / max(n, 1) >= 0.55:
        tone = "risk_off"
    return {
        "status": "ok",
        "tone": tone,
        "bullish_share": round(bullish / max(n, 1), 3),
        "bearish_share": round(bearish / max(n, 1), 3),
        "mean_scanner_score": round(avg_score, 2),
        "sample_size": n,
    }
