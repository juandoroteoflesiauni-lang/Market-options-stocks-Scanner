from __future__ import annotations
# ruff: noqa: F403, F405

import logging

logger = logging.getLogger(__name__)
from backend.services.research.research_types import *
from backend.services.research.research_types import _now_iso, _safe_float, _unavailable_desk_status


def _project_technical_desk(
    technical_payload: dict | object | None,
) -> TechnicalDeskState:
    """Project an already-computed venue technical payload into TechnicalDeskState.

    The payload is the result of ``build_venue_technical`` serialised to dict
    via ``to_dict()`` / ``asdict()``.  Structure expected (from
    :class:`~backend.services.bingx_technical_bridge.BingXTechnicalBridgeResult`):

    .. code-block:: json

        {
          "status": "available",
          "technical_quality_score": 0.8,
          "summary": {
            "trend_direction": "bullish",
            "smc_bias": "BULLISH",
            "vsa_signal": "absorption",
            "fvg_state": "bullish_dominant",
            "volume_profile_bias": "bullish",
            "composite_score": 0.72,
            "bars_used": 200
          },
          "payload": {
            "engine_status": {"smc": {"ok": true}, "vsa": {"ok": true}, ...}
          }
        }

    All missing fields are handled gracefully — no exception is raised.
    Returns an unavailable desk if ``status != "available"``.
    """
    source_tag = "venue_technical_bridge"

    if technical_payload is None:
        return TechnicalDeskState(
            desk_status=_unavailable_desk_status(
                source=source_tag,
                reason=REASON_DESK_NOT_IMPLEMENTED,
            )
        )

    # Defensive accessor: works for plain dict OR any object with attributes.
    def _g(obj: object, key: str, default: object = None) -> object:
        if isinstance(obj, dict):
            return obj.get(key, default)

        return getattr(obj, key, default)

    status = str(_g(technical_payload, "status") or "").lower()
    if status != "available":
        reason = str(_g(technical_payload, "reason") or "technical_status_not_available")[:180]
        return TechnicalDeskState(
            desk_status=_unavailable_desk_status(source=source_tag, reason=reason)
        )

    quality_score = _safe_float(_g(technical_payload, "technical_quality_score"))

    # Extract the compact summary sub-object.
    summary = _g(technical_payload, "summary") or {}
    trend_direction = str(_g(summary, "trend_direction") or "neutral").lower()
    smc_bias_raw = _g(summary, "smc_bias")
    smc_bias = str(smc_bias_raw).upper() if smc_bias_raw else "NEUTRAL"
    bars_used_val = _g(summary, "bars_used")
    bars_count = int(bars_used_val) if isinstance(bars_used_val, int | float) else 0

    # Count active engines from the engine_status block inside the raw payload.
    raw_payload = _g(technical_payload, "payload") or {}
    engine_status_block = _g(raw_payload, "engine_status") or {}
    engines_ok = [
        k
        for k, v in (engine_status_block.items() if isinstance(engine_status_block, dict) else [])
        if isinstance(v, dict) and v.get("ok")
    ]
    engines_total = len(engine_status_block) if isinstance(engine_status_block, dict) else 0

    logger.debug(
        "_project_technical_desk | trend=%s smc=%s bars=%d " "engines_ok=%d/%d quality=%.4f",
        trend_direction,
        smc_bias,
        bars_count,
        len(engines_ok),
        engines_total,
        quality_score or 0.0,
    )

    return TechnicalDeskState(
        desk_status=DeskReadStatus(
            status="available",
            source=source_tag,
            reason=None,
            quality_score=quality_score,
            latency_ms=None,  # already computed by the bridge; not re-timed here
            captured_at=_now_iso(),
        ),
        trend_direction=trend_direction,
        smc_bias=smc_bias,
        technical_quality_score=quality_score,
        bars_count=bars_count,
    )
