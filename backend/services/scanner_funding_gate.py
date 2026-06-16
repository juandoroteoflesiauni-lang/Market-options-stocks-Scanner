from __future__ import annotations
from typing import Any
"""Funding-suitability evaluation for scanner rows.

This module separates the directional thesis from the funding-risk gate. The
scanner can still emit a high directional score, but any candidate that fails a
funding-rule check must be flagged as ``size_down`` or ``block`` so that the
Risk Desk never authorizes normal sizing for a weak source or overfit history.

Reason codes are intentionally stable strings — the Risk Desk and UI grep them.
Do not rename without coordinating with ``portfolio_risk_service`` and the
``/management`` cockpit.
"""


import math

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# Stable reason codes — keep in sync with portfolio_risk_service and UI.
REASON_OVERFIT_MODULE = "overfit_module"
REASON_INSUFFICIENT_BACKTEST = "insufficient_backtest_evidence"
REASON_WEAK_EDGE = "weak_edge_backtest"
REASON_FUNDING_WOULD_BREACH = "funding_would_breach_in_history"
REASON_FUNDING_AT_RISK = "funding_at_risk_consistency"
REASON_WEAK_SOURCE_TIER = "weak_source_tier"
REASON_LIGHT_PROXY_ONLY = "light_proxy_only"
REASON_SNAPSHOT_CHAIN_ONLY = "snapshot_chain_only"
REASON_LOW_DATA_QUALITY = "low_data_quality"
REASON_CONFLICTING_MODULES = "conflicting_modules"
REASON_DAILY_LOSS_USAGE_HIGH = "daily_loss_usage_high"
REASON_STOP_EXCEEDS_REMAINING_RISK = "stop_exceeds_remaining_risk"
REASON_CONSISTENCY_CAP_RISK = "consistency_cap_risk"
REASON_LOW_L2_QUALITY = "low_l2_quality"
# L2 quality below this threshold forces size-down. Calibrated so a stock-perp
# instrument with a wide spread *or* a thin book still trades at half-size while
# we gather evidence — never at full size.
L2_QUALITY_SIZE_DOWN_THRESHOLD: float = 0.4
REASON_SCANNER_UNAVAILABLE = "scanner_unavailable"
REASON_SCANNER_SCORE_TOO_LOW = "scanner_score_too_low"
REASON_SCANNER_TREND_MISALIGNED = "scanner_trend_misaligned"
REASON_SCANNER_INTRADAY_NOT_ALIGNED = "scanner_intraday_not_aligned"
REASON_SCANNER_DAILY_OPPOSES = "scanner_daily_opposes"
REASON_SCANNER_PHASE_B_MISSING = "scanner_phase_b_missing"
REASON_SCANNER_VETO_PRESENT = "scanner_veto_present"
REASON_SCANNER_CONFIDENCE_TOO_LOW = "scanner_confidence_too_low"

SUITABILITY_ALLOW = "allow"
SUITABILITY_SIZE_DOWN = "size_down"
SUITABILITY_BLOCK = "block"
SUITABILITY_INSUFFICIENT = "insufficient_data"
# Missing backtest history — advisory only; must not veto Phase B or sizing prep.
SUITABILITY_INFORMATIONAL_ONLY = "informational_only"


def evaluate_funding_suitability(
    *,
    backtest_evidence: dict[str, Any] | None,
    source_tier: str | None,
    data_quality_score: float | None,
    conflict_score: float | None,
    daily_loss_usage_pct: float | None = None,
    stop_pct: float | None = None,
    remaining_risk_pct: float | None = None,
    consistency_ratio: float | None = None,
    lob_analysis_data_quality_score: float | None = None,
) -> dict[str, Any]:
    """Return ``{suitability, reason_codes, size_multiplier}``.

    ``backtest_evidence`` is the per-symbol result emitted by
    ``run_prediction_backtest`` — it contains ``module_backtest_grade``,
    ``funding_risk_metrics``, ``source_tier`` and ``data_quality_score``.

    ``lob_analysis_data_quality_score`` is the optional 0.0-1.0 score produced
    by the BingX L2 bridge. When provided and below
    :data:`L2_QUALITY_SIZE_DOWN_THRESHOLD` we add ``REASON_LOW_L2_QUALITY`` and
    halve the size multiplier. When ``None`` the scoring/gating is unchanged
    for instruments without an L2 feed.

    The function never authorizes anything on its own — its job is to surface
    reason codes so the Risk Desk can downgrade or block. When backtest history
    is missing (empty DB or no rows for the symbol), the outcome is
    ``informational_only`` so Phase B modules still run; hard blocks apply only
    for overfit, simulated funding breach, and live account pressure.
    """
    reasons: list[str] = []
    size_multiplier = 1.0

    if not backtest_evidence:
        return _missing_backtest_outcome()

    grade = str(backtest_evidence.get("module_backtest_grade") or "").lower()
    funding_metrics = backtest_evidence.get("funding_risk_metrics") or {}
    survival = str(funding_metrics.get("funding_survival_grade") or "").lower()

    # ---- Hard blockers ---------------------------------------------------------
    if grade in {"insufficient_data", ""}:
        return _missing_backtest_outcome()
    if grade == "overfit_risk":
        reasons.append(REASON_OVERFIT_MODULE)
        return {
            "suitability": SUITABILITY_BLOCK,
            "reason_codes": reasons,
            "size_multiplier": 0.0,
        }
    if survival == "would_breach":
        reasons.append(REASON_FUNDING_WOULD_BREACH)
        return {
            "suitability": SUITABILITY_BLOCK,
            "reason_codes": reasons,
            "size_multiplier": 0.0,
        }

    # Funding-rule pressure from live account state.
    if daily_loss_usage_pct is not None and daily_loss_usage_pct >= 80.0:
        reasons.append(REASON_DAILY_LOSS_USAGE_HIGH)
        return {
            "suitability": SUITABILITY_BLOCK,
            "reason_codes": reasons,
            "size_multiplier": 0.0,
        }
    if (
        stop_pct is not None
        and remaining_risk_pct is not None
        and stop_pct > max(remaining_risk_pct, 1e-9)
    ):
        reasons.append(REASON_STOP_EXCEEDS_REMAINING_RISK)
        return {
            "suitability": SUITABILITY_BLOCK,
            "reason_codes": reasons,
            "size_multiplier": 0.0,
        }

    # ---- Reductions ------------------------------------------------------------
    if grade == "weak_edge":
        reasons.append(REASON_WEAK_EDGE)
        size_multiplier *= 0.5
    if survival == "at_risk":
        reasons.append(REASON_FUNDING_AT_RISK)
        size_multiplier *= 0.5

    tier = str(source_tier or "").lower()
    if tier == "light_proxy":
        reasons.append(REASON_LIGHT_PROXY_ONLY)
        size_multiplier *= 0.5
    elif tier == "snapshot_chain":
        reasons.append(REASON_SNAPSHOT_CHAIN_ONLY)
        size_multiplier *= 0.75

    if data_quality_score is not None and data_quality_score < 0.35:
        reasons.append(REASON_LOW_DATA_QUALITY)
        size_multiplier *= 0.5

    if conflict_score is not None and conflict_score >= 0.5:
        reasons.append(REASON_CONFLICTING_MODULES)
        size_multiplier *= 0.5

    if consistency_ratio is not None and consistency_ratio >= 0.35:
        reasons.append(REASON_CONSISTENCY_CAP_RISK)
        size_multiplier *= 0.5

    # L2 quality is degrade-only — never authorizes a missing-evidence symbol.
    # Only applied when explicitly provided; instruments without an L2 feed are
    # unchanged so this stays opt-in for synthetic stock perpetuals.
    if (
        lob_analysis_data_quality_score is not None
        and lob_analysis_data_quality_score < L2_QUALITY_SIZE_DOWN_THRESHOLD
    ):
        reasons.append(REASON_LOW_L2_QUALITY)
        size_multiplier *= 0.5

    suitability = SUITABILITY_ALLOW if not reasons else SUITABILITY_SIZE_DOWN
    return {
        "suitability": suitability,
        "reason_codes": reasons,
        "size_multiplier": round(max(0.0, min(1.0, size_multiplier)), 4),
    }


def evaluate_module_evidence(
    *,
    module: str,
    backtest_evidence: dict[str, Any] | None,
    source_tier: str | None,
    data_quality_score: float | None,
    signal_coverage: float | None,
) -> dict[str, Any]:
    """Return evidence dict for one module: grade, tier, quality, survival, reasons, size_multiplier.

    This is the per-module counterpart to ``evaluate_funding_suitability`` which handles the
    single-module path. All sizing decisions are still advisory — the Risk Desk is the final
    authority.
    """
    reasons: list[str] = []
    size_multiplier = 1.0

    grade = ""
    survival = ""

    if backtest_evidence:
        grade = str(backtest_evidence.get("module_backtest_grade") or "").lower()
        funding_metrics = backtest_evidence.get("funding_risk_metrics") or {}
        survival = str(funding_metrics.get("funding_survival_grade") or "").lower()

    # ---- Hard blockers --------------------------------------------------------
    if not backtest_evidence or grade in {"insufficient_data", ""}:
        base = _missing_backtest_outcome()
        return {
            "module": module,
            "module_backtest_grade": grade or "insufficient_data",
            "source_tier": source_tier,
            "data_quality_score": data_quality_score,
            "signal_coverage": signal_coverage,
            "funding_survival_grade": survival or None,
            "reasons": list(base["reason_codes"]),
            "size_multiplier": base["size_multiplier"],
            "suitability": base["suitability"],
        }

    if grade == "overfit_risk":
        reasons.append(REASON_OVERFIT_MODULE)
        return {
            "module": module,
            "module_backtest_grade": grade,
            "source_tier": source_tier,
            "data_quality_score": data_quality_score,
            "signal_coverage": signal_coverage,
            "funding_survival_grade": survival or None,
            "reasons": reasons,
            "size_multiplier": 0.0,
            "suitability": SUITABILITY_BLOCK,
        }

    if survival == "would_breach":
        reasons.append(REASON_FUNDING_WOULD_BREACH)
        return {
            "module": module,
            "module_backtest_grade": grade,
            "source_tier": source_tier,
            "data_quality_score": data_quality_score,
            "signal_coverage": signal_coverage,
            "funding_survival_grade": survival,
            "reasons": reasons,
            "size_multiplier": 0.0,
            "suitability": SUITABILITY_BLOCK,
        }

    # ---- Reductions -----------------------------------------------------------
    if grade == "weak_edge":
        reasons.append(REASON_WEAK_EDGE)
        size_multiplier *= 0.5

    if survival == "at_risk":
        reasons.append(REASON_FUNDING_AT_RISK)
        size_multiplier *= 0.5

    tier = str(source_tier or "").lower()
    if tier == "light_proxy":
        reasons.append(REASON_LIGHT_PROXY_ONLY)
        size_multiplier *= 0.5
    elif tier == "snapshot_chain":
        reasons.append(REASON_SNAPSHOT_CHAIN_ONLY)
        size_multiplier *= 0.75

    if data_quality_score is not None and data_quality_score < 0.35:
        reasons.append(REASON_LOW_DATA_QUALITY)
        size_multiplier *= 0.5

    suitability = SUITABILITY_ALLOW if not reasons else SUITABILITY_SIZE_DOWN
    return {
        "module": module,
        "module_backtest_grade": grade,
        "source_tier": source_tier,
        "data_quality_score": data_quality_score,
        "signal_coverage": signal_coverage,
        "funding_survival_grade": survival or None,
        "reasons": reasons,
        "size_multiplier": round(max(0.0, min(1.0, size_multiplier)), 4),
        "suitability": suitability,
    }


def split_directional_and_risk_scores(
    scanner_score: float, *, risk_penalty: float
) -> tuple[float, float]:
    """Decompose the legacy scanner_score into directional vs risk scores.

    ``scanner_score`` is the historical 0–100 confluence score. We treat it as
    the directional component and compute a separate risk component as
    ``100 - risk_penalty`` (capped to [0, 100]). The Risk Desk consumes both
    independently — never multiply them back together silently.
    """
    directional = max(0.0, min(100.0, float(scanner_score)))
    risk = max(0.0, min(100.0, 100.0 - float(risk_penalty)))
    return directional, risk


def risk_penalty_from_evidence(
    *,
    backtest_evidence: dict[str, Any] | None,
    source_tier: str | None,
    data_quality_score: float | None,
    conflict_score: float | None,
) -> float:
    """Translate evidence into a 0–100 risk penalty (0 = safest, 100 = block)."""
    penalty = 0.0
    if not backtest_evidence:
        return 0.0  # Missing history is informational — not a risk veto.
    grade = str(backtest_evidence.get("module_backtest_grade") or "").lower()
    if grade == "insufficient_data":
        return 0.0
    elif grade == "overfit_risk":
        penalty += 80.0
    elif grade == "weak_edge":
        penalty += 40.0

    funding = backtest_evidence.get("funding_risk_metrics") or {}
    survival = str(funding.get("funding_survival_grade") or "").lower()
    if survival == "would_breach":
        penalty += 60.0
    elif survival == "at_risk":
        penalty += 25.0
    elif survival == "monitor":
        penalty += 10.0

    tier = str(source_tier or "").lower()
    if tier == "light_proxy":
        penalty += 25.0
    elif tier == "snapshot_chain":
        penalty += 10.0

    if data_quality_score is not None and data_quality_score < 0.35:
        penalty += 20.0

    if conflict_score is not None and conflict_score >= 0.5:
        penalty += 15.0

    return min(100.0, penalty)


def evaluate_scanner_confirmation(
    *,
    row: dict[str, Any],
    entry_direction: str,
    min_score: float,
) -> dict[str, Any]:
    """
    Evalua si una fila devuelta por el Market Scanner pasa los filtros
    estrictos de FTMO segun el plan de Doble Llave.
    Retorna {"status": "PASS"/"FAIL", "reasons": list, "trend_score": float}
    """
    side = _normalize_scanner_direction(entry_direction)
    scanner_score = _score_0_to_100(row.get("scanner_score")) if isinstance(row, dict) else None
    if not isinstance(row, dict) or not row or side is None or scanner_score is None:
        return {
            "status": "FAIL",
            "reasons": [REASON_SCANNER_UNAVAILABLE],
            "trend_score": 0.0,
        }

    reasons: list[str] = []
    side_score = scanner_score if side == "bullish" else 100.0 - scanner_score
    if side_score < float(min_score):
        reasons.append(REASON_SCANNER_SCORE_TOO_LOW)

    vetoes = row.get("vetoes")
    if isinstance(vetoes, list) and vetoes:
        reasons.append(REASON_SCANNER_VETO_PRESENT)

    confidence_floor = float(min_score) - 10.0
    ci_side_score = _scanner_confidence_floor(row, side)
    if ci_side_score is not None and ci_side_score < confidence_floor:
        reasons.append(REASON_SCANNER_CONFIDENCE_TOO_LOW)

    technical = _technical_signal(row)
    if technical is None:
        reasons.append(REASON_SCANNER_PHASE_B_MISSING)
    else:
        technical_score = _score_0_to_100(_mapping_value(technical, "score"))
        technical_confidence = _float_or_none(_mapping_value(technical, "confidence"))
        if technical_score is None:
            reasons.append(REASON_SCANNER_PHASE_B_MISSING)
        else:
            technical_side_score = technical_score if side == "bullish" else 100.0 - technical_score
            if technical_side_score < 65.0:
                reasons.append(REASON_SCANNER_TREND_MISALIGNED)
        if technical_confidence is None or technical_confidence < 0.55:
            reasons.append(REASON_SCANNER_CONFIDENCE_TOO_LOW)

    signals = row.get("signals")
    trend_score = _trend_alignment_score(signals, side)
    if trend_score < 0.80:
        reasons.append(REASON_SCANNER_INTRADAY_NOT_ALIGNED)

    daily = signals.get("1D") if isinstance(signals, dict) else None
    if _signal_ok(daily) and _signal_direction(daily) == _opposite_direction(side):
        reasons.append(REASON_SCANNER_DAILY_OPPOSES)

    return {
        "status": "PASS" if not reasons else "FAIL",
        "reasons": _dedupe_reasons(reasons),
        "trend_score": round(trend_score, 4),
    }


def _normalize_scanner_direction(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if text in {"UP", "LONG", "BUY", "BULLISH"}:
        return "bullish"
    if text in {"DOWN", "SHORT", "SELL", "BEARISH"}:
        return "bearish"
    return None


def _score_0_to_100(value: object) -> float | None:
    parsed = _float_or_none(value)
    if parsed is None or parsed < 0.0 or parsed > 100.0:
        return None
    return parsed


def _float_or_none(value: object) -> float | None:
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if parsed is None or not math.isfinite(parsed):
        return None
    return parsed


def _scanner_confidence_floor(row: dict[str, Any], side: str) -> float | None:
    if side == "bullish":
        return _score_0_to_100(row.get("score_ci_low"))
    high = _score_0_to_100(row.get("score_ci_high"))
    return None if high is None else 100.0 - high


def _technical_signal(row: dict[str, Any]) -> object | None:
    module_signals = row.get("module_signals")
    if not isinstance(module_signals, dict):
        return None
    return module_signals.get("technical")


def _mapping_value(payload: object, key: str) -> object | None:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _trend_alignment_score(signals: object, side: str) -> float:
    if not isinstance(signals, dict):
        return 0.0
    score = 0.0
    for timeframe, weight in (("5m", 0.25), ("15m", 0.35), ("1h", 0.40)):
        signal = signals.get(timeframe)
        if not _signal_ok(signal):
            continue
        direction = _signal_direction(signal)
        if direction == side:
            score += weight
        elif direction == "neutral":
            score += weight * 0.5
    return score


def _signal_ok(signal: object) -> bool:
    return bool(_mapping_value(signal, "ok"))


def _signal_direction(signal: object) -> str | None:
    direction = str(_mapping_value(signal, "direction") or "").strip().lower()
    if direction in {"bullish", "bearish", "neutral"}:
        return direction
    return None


def _opposite_direction(side: str) -> str:
    return "bearish" if side == "bullish" else "bullish"


def _missing_backtest_outcome() -> dict[str, Any]:
    """Advisory outcome when predictions/backtest DB has no usable history."""
    return {
        "suitability": SUITABILITY_INFORMATIONAL_ONLY,
        "reason_codes": [REASON_INSUFFICIENT_BACKTEST],
        "size_multiplier": 1.0,
    }


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            out.append(reason)
    return out
