"""Optional FinRobot adapter for Market Scanner leaders research.

This module is dependency-light by design. It imports FinRobot or a configured
callable only inside ``run_finrobot_leaders_research`` so normal scanner imports
do not require optional research dependencies.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Callable
from typing import Any

from backend.config.logger_setup import get_logger
from backend.services.scanner_external_contracts import ResearchBriefResult

logger = get_logger(__name__)

_MAX_LEADERS = 8
_MAX_PAYLOAD_CHARS = 16_000


def _unavailable(reason: str, *, warning: str | None = None) -> ResearchBriefResult:
    warnings = [warning] if warning else []
    return ResearchBriefResult(
        engine="finrobot",
        status="unavailable",
        reason=reason,
        mode="finrobot_unavailable",
        warnings=warnings,
    )


def _build_research_payload(
    symbols: list[str],
    row_summaries: list[dict[str, Any]],
    *,
    universe: str | None,
    regime_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "task": "market_scanner_leaders_research",
        "restrictions": [
            "Research/thesis/report only.",
            "Do not authorize trades, orders, entries, exits, leverage, or position sizing.",
            "Do not bypass scanner_funding_gate.py or any risk/funding gate.",
            "Do not invent catalysts or figures absent from the payload.",
        ],
        "symbols": symbols,
        "universe": universe,
        "universe_regime": regime_summary,
        "leaders": row_summaries[:_MAX_LEADERS],
    }


def _load_callable_from_spec(spec: str) -> Callable[..., Any] | None:
    module_name, sep, attr_path = spec.partition(":")
    if not sep:
        module_name, _, attr_path = spec.rpartition(".")
    module_name = module_name.strip()
    attr_path = attr_path.strip()
    if not module_name or not attr_path:
        return None
    module = importlib.import_module(module_name)
    target: Any = module
    for attr in attr_path.split("."):
        target = getattr(target, attr)
    if not callable(target):
        return None
    return target


def _find_default_finrobot_callable() -> Callable[..., Any] | None:
    module = importlib.import_module("finrobot")
    for attr in (
        "run_leaders_research",
        "leaders_research",
        "research_leaders",
        "run_research",
    ):
        candidate = getattr(module, attr, None)
        if callable(candidate):
            return candidate
    return None


def _coerce_research_result(
    raw: object,
    *,
    symbols: list[str],
    mode: str,
) -> ResearchBriefResult:
    if isinstance(raw, ResearchBriefResult):
        return raw
    if isinstance(raw, dict):
        payload = {"engine": "finrobot", "symbols": symbols, "mode": mode, **raw}
        if "status" not in raw and any(raw.get(key) for key in ("summary", "key_points", "risks")):
            payload["status"] = "available"
            payload["reason"] = "ok"
        return ResearchBriefResult.model_validate(payload)
    text = str(raw or "").strip()
    if not text:
        return _unavailable("empty_result")
    return ResearchBriefResult(
        engine="finrobot",
        status="available",
        reason="ok",
        symbols=symbols,
        mode=mode,
        title="FinRobot leaders research",
        summary=text,
        confidence=0.5,
        data_quality_score=0.5,
    )


def run_finrobot_leaders_research(
    symbols: list[str],
    row_summaries: list[dict[str, Any]],
    *,
    universe: str | None = None,
    regime_summary: dict[str, Any] | None = None,
) -> ResearchBriefResult:
    """Run optional FinRobot leaders research and normalize it to ``ResearchBriefResult``.

    Configure ``SCANNER_FINROBOT_CALLABLE`` as ``module:function`` when the
    installed FinRobot package does not expose one of the supported lightweight
    research callables.
    """
    payload = _build_research_payload(
        symbols,
        row_summaries,
        universe=universe,
        regime_summary=regime_summary,
    )
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)[:_MAX_PAYLOAD_CHARS]

    callable_spec = os.getenv("SCANNER_FINROBOT_CALLABLE", "").strip()
    try:
        if callable_spec:
            research_callable = _load_callable_from_spec(callable_spec)
            mode = "finrobot_configured_callable"
        else:
            research_callable = _find_default_finrobot_callable()
            mode = "finrobot_default_callable"
    except ModuleNotFoundError as exc:
        if exc.name == "finrobot":
            logger.info("scanner.finrobot_unavailable reason=not_installed")
            return _unavailable("not_installed")
        logger.warning("scanner.finrobot_callable_import_failed error=%s", str(exc)[:200])
        return _unavailable("callable_import_failed", warning=str(exc)[:200])
    except Exception as exc:
        logger.warning("scanner.finrobot_callable_import_failed error=%s", str(exc)[:200])
        return _unavailable("callable_import_failed", warning=str(exc)[:200])

    if research_callable is None:
        logger.info("scanner.finrobot_unavailable reason=callable_not_configured")
        return _unavailable("callable_not_configured")

    try:
        raw = research_callable(
            symbols=symbols,
            row_summaries=row_summaries,
            universe=universe,
            regime_summary=regime_summary,
            payload=payload,
            payload_json=payload_json,
        )
    except TypeError:
        raw = research_callable(payload_json)
    except Exception as exc:
        logger.warning("scanner.finrobot_research_failed error=%s", str(exc)[:200])
        return _unavailable("research_failed", warning=str(exc)[:200])

    result = _coerce_research_result(raw, symbols=symbols, mode=mode)
    if not result.symbols:
        result.symbols = symbols
    if not result.engine or result.engine == "insufficient_data":
        result.engine = "finrobot"
    return result


__all__ = ["run_finrobot_leaders_research"]
