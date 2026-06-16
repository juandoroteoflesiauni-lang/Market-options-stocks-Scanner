from __future__ import annotations
from typing import Any
"""Pure FTMO survival scoring for Funding Lab.

This module is intentionally read-only and deterministic. It does not fetch
market data, inspect broker state, or depend on the BingX Bot.
"""


from collections.abc import Iterable

FTMO_PROFILE_ID = "ftmo_2_step"
FTMO_INITIAL_CAPITAL = 100_000.0
FTMO_DAILY_LOSS_LIMIT_PCT = 5.0
FTMO_MAX_LOSS_LIMIT_PCT = 10.0
FTMO_CONSISTENCY_WARNING = 0.35
FTMO_CONSISTENCY_BLOCK = 0.50
FTMO_BASE_RISK_PER_TRADE_PCT = 0.50

_STATUS_RANK = {
    "SAFE": 0,
    "MONITOR": 1,
    "AT_RISK": 2,
    "WOULD_BREACH": 3,
    "INSUFFICIENT": 4,
}


def default_ftmo_account_state() -> dict[str, Any]:
    """Return the default flat FTMO 2-Step account state."""
    return {
        "initial_capital": FTMO_INITIAL_CAPITAL,
        "current_equity": FTMO_INITIAL_CAPITAL,
        "start_of_day_balance": FTMO_INITIAL_CAPITAL,
        "realized_daily_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "trade_history": [],
    }


def compute_ftmo_survival_score(
    *,
    module_evidence: list[dict[str, Any]],
    account_state: dict[str, Any] | None = None,
    reason_codes: list[str] | None = None,
    required_horizons: tuple[int | str, ...] | None = None,
    profile: Any | None = None,
    profile_id: str = FTMO_PROFILE_ID,
) -> dict[str, Any]:
    """Compute a JSON-safe Funding Lab survival summary.

    ``module_evidence`` represents historical/OOS evidence. ``account_state``
    represents the current funding-account runway. Both must be compatible for
    the score to be SAFE.
    """
    account = _normalized_account(account_state)
    evidence = [row for row in module_evidence if not _is_context_evidence(profile, row)]
    observed_horizons = sorted(
        {horizon for row in module_evidence if (horizon := _evidence_horizon_key(row)) is not None}
    )
    incoming_reasons = _clean_reasons(reason_codes)
    summary_reasons: list[str] = []

    daily = _daily_loss_metrics(account)
    max_loss = _max_loss_metrics(account)
    consistency = _consistency_metrics(account)
    historical = _historical_metrics(evidence)

    if (
        not evidence
        or _has_insufficient_reason(incoming_reasons)
        or historical["status"] == "INSUFFICIENT"
    ):
        summary_reasons.extend(
            [reason for reason in incoming_reasons if _is_insufficient_reason(reason)]
        )
        if not evidence and "missing_backtest_evidence" not in summary_reasons:
            summary_reasons.append("missing_backtest_evidence")
        if historical["status"] == "INSUFFICIENT" and "insufficient_data" not in summary_reasons:
            summary_reasons.append("insufficient_data")
        return _payload(
            status="INSUFFICIENT",
            score=None,
            reason_codes=_dedupe(summary_reasons),
            evidence_count=len(module_evidence),
            horizons=observed_horizons,
            daily=daily,
            max_loss=max_loss,
            consistency=consistency,
            historical=historical,
            profile_id=profile_id,
            recommended_risk_per_trade_pct=0.0,
        )

    status = "SAFE"

    if daily["breached"]:
        summary_reasons.append("daily_loss_breach")
        status = _worse_status(status, "WOULD_BREACH")
    elif daily["usage_pct"] >= 80.0:
        summary_reasons.append("daily_loss_usage_high")
        status = _worse_status(status, "AT_RISK")

    if max_loss["breached"]:
        summary_reasons.append("max_loss_breach")
        status = _worse_status(status, "WOULD_BREACH")
    elif max_loss["usage_pct"] >= 80.0:
        summary_reasons.append("max_loss_usage_high")
        status = _worse_status(status, "AT_RISK")

    if consistency["blocked"]:
        summary_reasons.append("best_day_concentration")
        status = _worse_status(status, "WOULD_BREACH")
    elif consistency["warning"]:
        summary_reasons.append("consistency_warning")
        status = _worse_status(status, "AT_RISK")

    if historical["status"] == "WOULD_BREACH":
        summary_reasons.append("historical_would_breach")
        status = _worse_status(status, "WOULD_BREACH")
    elif historical["status"] == "AT_RISK":
        summary_reasons.append("historical_at_risk")
        status = _worse_status(status, "AT_RISK")
    elif historical["status"] == "MONITOR":
        status = _worse_status(status, "MONITOR")

    score_components = {
        "daily_loss_runway": round(max(0.0, 1.0 - daily["usage_pct"] / 100.0), 4),
        "max_loss_runway": round(max(0.0, 1.0 - max_loss["usage_pct"] / 100.0), 4),
        "consistency_runway": consistency["runway_score"],
        "historical_oos_quality": historical["quality_score"],
        "signal_conflict_pressure": historical["conflict_pressure_score"],
    }
    score = (
        0.30 * score_components["daily_loss_runway"]
        + 0.25 * score_components["max_loss_runway"]
        + 0.20 * score_components["consistency_runway"]
        + 0.15 * score_components["historical_oos_quality"]
        + 0.10 * score_components["signal_conflict_pressure"]
    ) * 100.0

    if status == "WOULD_BREACH":
        score = 0.0
    elif status == "MONITOR":
        score = min(score, 65.0)
    elif status == "AT_RISK":
        score = min(score, 49.0)
    else:
        status = "SAFE" if score >= 70.0 else "MONITOR" if score >= 50.0 else "AT_RISK"

    score = round(max(0.0, min(100.0, score)), 2)
    recommended = _recommended_risk(status, summary_reasons)
    return _payload(
        status=status,
        score=score,
        reason_codes=_dedupe(summary_reasons),
        evidence_count=len(module_evidence),
        horizons=observed_horizons,
        daily=daily,
        max_loss=max_loss,
        consistency=consistency,
        historical={**historical, "score_components": score_components},
        profile_id=profile_id,
        recommended_risk_per_trade_pct=recommended,
    )


def _payload(
    *,
    status: str,
    score: float | None,
    reason_codes: list[str],
    evidence_count: int,
    horizons: list[str],
    daily: dict[str, Any],
    max_loss: dict[str, Any],
    consistency: dict[str, Any],
    historical: dict[str, Any],
    profile_id: str,
    recommended_risk_per_trade_pct: float,
) -> dict[str, Any]:
    return {
        "status": status,
        "score": score,
        "reason_codes": reason_codes,
        "evidence_count": evidence_count,
        "horizons": horizons,
        "daily_loss_usage_pct": daily["usage_pct"],
        "max_loss_usage_pct": max_loss["usage_pct"],
        "best_day_contribution_pct": consistency["best_day_contribution_pct"],
        "remaining_daily_risk_pct": daily["remaining_pct"],
        "remaining_max_loss_pct": max_loss["remaining_pct"],
        "consistency_headroom_pct": consistency["headroom_pct"],
        "max_attempts_remaining_today": daily["max_attempts_remaining_today"],
        "recommended_risk_per_trade_pct": recommended_risk_per_trade_pct,
        "score_components": historical.get(
            "score_components",
            {
                "daily_loss_runway": None,
                "max_loss_runway": None,
                "consistency_runway": None,
                "historical_oos_quality": historical.get("quality_score"),
                "signal_conflict_pressure": historical.get("conflict_pressure_score"),
            },
        ),
        "profile_id": profile_id,
    }


def _normalized_account(account_state: dict[str, Any] | None) -> dict[str, Any]:
    account = default_ftmo_account_state()
    if isinstance(account_state, dict):
        account.update(account_state)
    initial = _positive_float(account.get("initial_capital"), FTMO_INITIAL_CAPITAL)
    account["initial_capital"] = initial
    account["current_equity"] = _positive_float(account.get("current_equity"), initial)
    account["start_of_day_balance"] = _positive_float(account.get("start_of_day_balance"), initial)
    account["realized_daily_pnl"] = _float(account.get("realized_daily_pnl"), 0.0)
    account["unrealized_pnl"] = _float(account.get("unrealized_pnl"), 0.0)
    history = account.get("trade_history")
    account["trade_history"] = history if isinstance(history, list) else []
    return account


def _daily_loss_metrics(account: dict[str, Any]) -> dict[str, Any]:
    initial = float(account["initial_capital"])
    limit = initial * FTMO_DAILY_LOSS_LIMIT_PCT / 100.0
    pnl = float(account["realized_daily_pnl"]) + float(account["unrealized_pnl"])
    used = abs(min(0.0, pnl))
    usage = _pct(used, limit)
    remaining = max(0.0, limit - used)
    return {
        "limit": round(limit, 2),
        "used": round(used, 2),
        "usage_pct": round(usage, 2),
        "remaining_pct": round(remaining / initial * 100.0, 4),
        "breached": usage >= 100.0,
        "max_attempts_remaining_today": int(
            remaining // (initial * FTMO_BASE_RISK_PER_TRADE_PCT / 100.0)
        ),
    }


def _max_loss_metrics(account: dict[str, Any]) -> dict[str, Any]:
    initial = float(account["initial_capital"])
    limit = initial * FTMO_MAX_LOSS_LIMIT_PCT / 100.0
    used = max(0.0, initial - float(account["current_equity"]))
    usage = _pct(used, limit)
    remaining = max(0.0, limit - used)
    return {
        "limit": round(limit, 2),
        "used": round(used, 2),
        "usage_pct": round(usage, 2),
        "remaining_pct": round(remaining / initial * 100.0, 4),
        "breached": usage >= 100.0,
    }


def _consistency_metrics(account: dict[str, Any]) -> dict[str, Any]:
    daily_pnl: dict[str, float] = {}
    for index, trade in enumerate(account.get("trade_history") or []):
        if not isinstance(trade, dict):
            continue
        key = str(trade.get("date") or f"__unknown_{index}")[:10]
        daily_pnl[key] = daily_pnl.get(key, 0.0) + _float(trade.get("pnl"), 0.0)
    positive = [value for value in daily_pnl.values() if value > 0.0]
    total_positive = sum(positive)
    best_day_pct = max(positive) / total_positive * 100.0 if total_positive > 0.0 else 0.0
    ratio = best_day_pct / 100.0
    blocked = ratio >= FTMO_CONSISTENCY_BLOCK
    warning = ratio >= FTMO_CONSISTENCY_WARNING and not blocked
    headroom = max(0.0, (FTMO_CONSISTENCY_BLOCK - ratio) * 100.0)
    if blocked:
        runway = 0.0
    elif warning:
        runway = 0.3
    else:
        runway = 1.0 if not positive else max(0.0, min(1.0, headroom / 50.0))
    return {
        "best_day_contribution_pct": round(best_day_pct, 2),
        "headroom_pct": round(headroom, 4),
        "blocked": blocked,
        "warning": warning,
        "runway_score": round(runway, 4),
    }


def _historical_metrics(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    grades = [str(row.get("funding_survival_grade") or "").lower() for row in evidence]
    metric_grades = [
        str(metrics.get("funding_survival_grade") or "").lower()
        for row in evidence
        if isinstance((metrics := row.get("funding_risk_metrics")), dict)
    ]
    all_grades = [grade for grade in [*grades, *metric_grades] if grade]
    if "would_breach" in all_grades:
        status = "WOULD_BREACH"
        quality = 0.0
    elif "at_risk" in all_grades:
        status = "AT_RISK"
        quality = 0.25
    elif "monitor" in all_grades:
        status = "MONITOR"
        quality = 0.35
    elif any(grade in {"", "insufficient_data"} for grade in grades):
        status = "INSUFFICIENT"
        quality = 0.0
    else:
        status = "SAFE"
        quality = _average_quality(evidence)
    max_conflict = max((_float(row.get("conflict_score"), 0.0) for row in evidence), default=0.0)
    return {
        "status": status,
        "quality_score": round(max(0.0, min(1.0, quality)), 4),
        "conflict_pressure_score": round(max(0.0, min(1.0, 1.0 - max_conflict)), 4),
    }


def _average_quality(evidence: list[dict[str, Any]]) -> float:
    scores: list[float] = []
    for row in evidence:
        score = row.get("data_quality_score")
        if score is not None:
            scores.append(max(0.0, min(1.0, _float(score, 0.0))))
        elif str(row.get("module_backtest_grade") or "").lower() == "validated":
            scores.append(1.0)
    return sum(scores) / len(scores) if scores else 0.75


def _recommended_risk(status: str, reasons: list[str]) -> float:
    if status in {"WOULD_BREACH", "INSUFFICIENT"}:
        return 0.0
    if any(reason in {"daily_loss_usage_high", "max_loss_usage_high"} for reason in reasons):
        return 0.0
    if status == "AT_RISK":
        return 0.10
    if status == "MONITOR":
        return 0.25
    return FTMO_BASE_RISK_PER_TRADE_PCT


def _is_context_evidence(profile: Any | None, evidence: dict[str, Any]) -> bool:
    if profile is None:
        return False
    context_modules = set(getattr(profile, "context_modules", ()) or ())
    required_modules = set(getattr(profile, "required_modules", ()) or ())
    module = str(evidence.get("module") or "").lower()
    return module in context_modules and module not in required_modules


def _evidence_horizon_key(evidence: dict[str, Any]) -> str | None:
    raw = evidence.get("horizon")
    if raw is None:
        raw = evidence.get("n_days")
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in {"1", "1d"}:
        return "1d"
    return text


def _clean_reasons(reasons: Iterable[str] | None) -> list[str]:
    return _dedupe(str(reason) for reason in (reasons or []) if str(reason).strip())


def _has_insufficient_reason(reasons: list[str]) -> bool:
    return any(_is_insufficient_reason(reason) for reason in reasons)


def _is_insufficient_reason(reason: str) -> bool:
    return (
        reason == "missing_backtest_evidence"
        or reason == "missing_oos_horizons"
        or reason == "insufficient_data"
        or reason.startswith("required_module_missing:")
        or reason == "crypto_derivatives_unvalidated"
    )


def _worse_status(current: str, candidate: str) -> str:
    return candidate if _STATUS_RANK[candidate] > _STATUS_RANK[current] else current


def _pct(numerator: float, denominator: float) -> float:
    return numerator / denominator * 100.0 if denominator > 0.0 else 100.0


def _positive_float(value: object, default: float) -> float:
    parsed = _float(value, default)
    return parsed if parsed > 0.0 else default


def _float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _dedupe(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out
