"""Small policy helpers for optional LLM narratives."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OptionalAIPolicyDecision:
    call: bool
    reason: str
    signal_score: float
    min_signal_score: float
    has_critical_risk: bool


def should_call_optional_ai(
    *,
    feature: str,
    signal_score: float,
    has_critical_risk: bool,
    mode_env: str = "AI_OPTIONAL_NARRATIVE_MODE",
    min_signal_env: str = "AI_OPTIONAL_NARRATIVE_MIN_SIGNAL",
) -> OptionalAIPolicyDecision:
    _ = feature
    mode = (os.environ.get(mode_env, "auto") or "").strip().lower()
    if mode not in {"auto", "always", "off"}:
        mode = "auto"
    min_signal = _float_env(min_signal_env, 0.65)

    if mode == "off":
        return OptionalAIPolicyDecision(
            False, "disabled", signal_score, min_signal, has_critical_risk
        )
    if mode == "always":
        return OptionalAIPolicyDecision(True, "forced", signal_score, min_signal, has_critical_risk)
    if has_critical_risk:
        return OptionalAIPolicyDecision(
            True, "critical_risk", signal_score, min_signal, has_critical_risk
        )
    if signal_score >= min_signal:
        return OptionalAIPolicyDecision(True, "sufficient_signal", signal_score, min_signal, False)
    return OptionalAIPolicyDecision(False, "low_signal", signal_score, min_signal, False)


def _float_env(name: str, default: float) -> float:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


@dataclass(frozen=True)
class AgenticCommitteePolicyDecision:
    run: bool
    reason: str
    signal_score: float
    min_signal_score: float
    quant_fallback_on_degraded: bool


def should_run_agentic_committee(
    *,
    signal_score: float,
    has_critical_risk: bool = False,
    mode_env: str = "AI_AGENTIC_COMMITTEE_MODE",
    min_signal_env: str = "AI_AGENTIC_COMMITTEE_MIN_SIGNAL",
) -> AgenticCommitteePolicyDecision:
    """Gate optional agentic committee invocation."""
    mode = (os.environ.get(mode_env, "auto") or "").strip().lower()
    if mode not in {"auto", "always", "off"}:
        mode = "auto"
    min_signal = _float_env(min_signal_env, 0.65)
    quant_fallback = (
        os.environ.get("AI_AGENTIC_QUANT_FALLBACK", "false") or ""
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if mode == "off":
        return AgenticCommitteePolicyDecision(
            False, "disabled", signal_score, min_signal, quant_fallback
        )
    if mode == "always":
        return AgenticCommitteePolicyDecision(
            True, "forced", signal_score, min_signal, quant_fallback
        )
    if has_critical_risk:
        return AgenticCommitteePolicyDecision(
            True, "critical_risk", signal_score, min_signal, quant_fallback
        )
    if signal_score >= min_signal:
        return AgenticCommitteePolicyDecision(
            True, "sufficient_signal", signal_score, min_signal, quant_fallback
        )
    return AgenticCommitteePolicyDecision(
        False, "low_signal", signal_score, min_signal, quant_fallback
    )
