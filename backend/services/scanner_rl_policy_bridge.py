"""Experimental bridge for offline Scanner RL policy scores.

The policy file is produced outside this runtime. This bridge only exposes the
score as research evidence; it never trains models and never authorizes trades.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

POLICY_PATH_ENV = "SCANNER_RL_POLICY_PATH"
POLICY_ENABLED_ENV = "SCANNER_RL_POLICY_ENABLED"


def scanner_rl_policy_enabled() -> bool:
    """Return whether scanner rows should include experimental RL evidence."""
    return os.getenv(POLICY_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def get_rl_policy_score(symbol: str, features: dict[str, Any]) -> dict[str, Any]:
    """Read an offline policy score for ``symbol`` from ``SCANNER_RL_POLICY_PATH``.

    Supported lightweight JSON contracts:
    - ``{"symbols": {"AAPL": {"score": 0.7, "confidence": 0.6, "action": "long"}}}``
    - ``{"scores": {"AAPL": 0.7}}``
    - ``{"feature_weights": {"scanner_score": 0.01}, "intercept": 0.0}``

    Scores are clamped to [0, 1] and returned as experimental evidence only.
    """
    policy_path = _policy_path()
    if policy_path is None or not policy_path.exists():
        return _unavailable("policy_missing")

    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("scanner_rl_policy.unavailable reason=policy_invalid error=%s", exc)
        return _unavailable("policy_invalid")
    if not isinstance(raw, dict):
        return _unavailable("policy_invalid")

    score_payload = _symbol_payload(raw, symbol)
    if score_payload is None and isinstance(raw.get("feature_weights"), dict):
        score_payload = _linear_payload(raw, features)
    if score_payload is None:
        return _unavailable("symbol_unavailable")

    score = _finite_float(score_payload.get("score"))
    if score is None:
        return _unavailable("score_invalid")

    result: dict[str, Any] = {
        "status": "available",
        "score": _clamp01(score),
        "confidence": _clamp01(_finite_float(score_payload.get("confidence")) or 0.0),
        "action": str(score_payload.get("action") or "observe"),
        "version": str(raw.get("version") or "unknown"),
        "model_family": str(raw.get("model_family") or "offline_rl_policy"),
        "experimental": True,
        "can_override_funding_gate": False,
    }
    return result


def _policy_path() -> Path | None:
    raw = os.getenv(POLICY_PATH_ENV, "").strip()
    return Path(raw) if raw else None


def _symbol_payload(raw: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    sym = symbol.upper().strip()
    for container_key in ("symbols", "scores"):
        container = raw.get(container_key)
        if not isinstance(container, dict):
            continue
        value = container.get(sym) or container.get(symbol) or container.get(symbol.lower())
        if isinstance(value, dict):
            return dict(value)
        parsed = _finite_float(value)
        if parsed is not None:
            return {
                "score": parsed,
                "confidence": raw.get("confidence"),
                "action": raw.get("action"),
            }
    return None


def _linear_payload(raw: dict[str, Any], features: dict[str, Any]) -> dict[str, Any] | None:
    weights = raw.get("feature_weights")
    if not isinstance(weights, dict):
        return None
    score = _finite_float(raw.get("intercept")) or 0.0
    used = False
    for key, weight_raw in weights.items():
        value = _finite_float(features.get(str(key)))
        weight = _finite_float(weight_raw)
        if value is None or weight is None:
            continue
        score += value * weight
        used = True
    if not used:
        return None
    return {"score": score, "confidence": raw.get("confidence"), "action": raw.get("action")}


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "experimental": True,
        "can_override_funding_gate": False,
    }


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 6)


__all__ = ["get_rl_policy_score", "scanner_rl_policy_enabled"]
