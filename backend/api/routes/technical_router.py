from __future__ import annotations
from typing import Any
"""Advanced technical terminal routes."""



from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from backend.config.logger_setup import get_logger
from backend.layer_1_data.real_time_ws.generated_candle_store import get_generated_candle_store
from backend.quant_engine.engines.technical.lob_dynamics_engine import (
    LOBConfig,
    LOBEvent,
    LOBSnapshot,
    analyze_lob_dynamics,
)
from backend.services.ai_core.agent_manager import AgentManager
from backend.services.ai_ready_payload import AIReadyPayloadEngine
from backend.services.llm_call_policy import should_call_optional_ai
from backend.services.technical_terminal_payload import (
    build_technical_terminal_payload,
    build_technical_terminal_payload_from_candles,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/technical", tags=["technical"])


class LOBAnalysisRequest(BaseModel):
    """Request body for stateless L2 order-book analysis."""

    model_config = ConfigDict(extra="ignore")

    snapshot: LOBSnapshot | None = None
    events: tuple[LOBEvent, ...] = ()
    config: LOBConfig | None = None


@router.get("/advanced/{symbol}")
async def get_technical_advanced(
    symbol: str,
    timeframe: str = Query("1D", description="Timeframe: 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1S"),
    candle_source: str = Query(
        "auto",
        description="OHLCV source: auto, generated, or fetch.",
    ),
    with_narrative: bool = Query(
        False,
        description="Call the technical AI agent. Requires GEMINI_API_KEY.",
    ),
) -> dict[str, Any]:
    """Return OHLCV, indicators, SMC, fractal analysis, and optional narrative."""
    sym = symbol.upper().strip()
    tf = timeframe.strip()
    tf_lookup = {"1D": "1d", "1S": "1w", "1W": "1w"}.get(tf, tf.lower())

    days_by_timeframe = {
        "1s": 2,
        "1m": 20,
        "5m": 120,
        "15m": 180,
        "30m": 365,
        "1h": 730,
        "4h": 1825,
        "1d": 365,
        "1D": 365,
        "1w": 3650,
        "1S": 730,
    }
    days = days_by_timeframe.get(tf_lookup, days_by_timeframe.get(tf, 320))

    source_mode = candle_source.strip().lower()
    if source_mode not in {"auto", "generated", "fetch"}:
        raise HTTPException(
            status_code=422, detail="candle_source must be auto, generated, or fetch"
        )

    snapshot = None
    if source_mode in {"auto", "generated"}:
        snapshot = get_generated_candle_store().snapshot(sym, tf_lookup)

    if snapshot is not None and len(snapshot.candles) >= 35 and source_mode != "fetch":
        payload = await build_technical_terminal_payload_from_candles(
            sym,
            snapshot.candles,
            tf_lookup,
            live_partial_bar=snapshot.live_partial_bar,
            analysis_cadence="generated_snapshot",
            source=snapshot.source or "generated_candles",
            last_candle_time=snapshot.last_candle_time,
        )
    elif source_mode == "generated":
        payload = await build_technical_terminal_payload_from_candles(
            sym,
            snapshot.candles if snapshot is not None else [],
            tf_lookup,
            live_partial_bar=bool(snapshot.live_partial_bar) if snapshot is not None else False,
            analysis_cadence="generated_snapshot",
            source=(
                snapshot.source if snapshot is not None and snapshot.source else "generated_candles"
            ),
            last_candle_time=snapshot.last_candle_time if snapshot is not None else None,
        )
    else:
        payload = await build_technical_terminal_payload(sym, days=days, timeframe=tf_lookup)
        if source_mode == "auto":
            payload.setdefault("meta", {})
            payload["meta"]["generated_candles"] = snapshot.count if snapshot is not None else 0
            if snapshot is None or snapshot.count < 35:
                payload["meta"]["fallback_reason"] = "generated_candles_insufficient"

    if not payload.get("ok"):
        err = str(payload.get("error", "unknown"))
        if "Insufficient" in err:
            raise HTTPException(status_code=422, detail=err)
        raise HTTPException(status_code=404, detail=err)

    narrative: str | None = None
    if with_narrative:
        evidence_pack = AIReadyPayloadEngine().build_technical_pack(payload)
        policy = should_call_optional_ai(
            feature="technical_narrative",
            signal_score=evidence_pack.signal_score,
            has_critical_risk=evidence_pack.has_critical_risk,
        )
        if policy.call:
            prompt = (
                "Eres el especialista tecnico de QuantumAnalyzer. "
                "Resume en 5-7 bullets institucionales el sesgo tecnico, niveles clave, "
                "riesgos y condicion de invalidacion usando este Evidence Pack compacto. "
                "No inventes series, velas ni overlays ausentes del pack:\n"
                f"{evidence_pack.to_prompt_json(max_chars=2_200)}"
            )
            try:
                manager = AgentManager()
                try:
                    narrative = await manager.invoke_agent("technical", prompt)
                finally:
                    await manager.aclose()
            except Exception as exc:
                logger.exception("technical narrative failed")
                narrative = f"[IA no disponible] {exc}"
        else:
            logger.info(
                "technical.optional_ai_skipped symbol=%s reason=%s signal_score=%.3f",
                sym,
                policy.reason,
                policy.signal_score,
            )

    out = dict(payload)
    out["narrative"] = narrative
    return out


@router.post("/lob/analyze")
async def analyze_technical_lob(
    request: LOBAnalysisRequest,
) -> dict[str, Any]:
    """Analyze real L2 order-book snapshots/events for technical LOB dynamics."""
    result = analyze_lob_dynamics(
        snapshot=request.snapshot,
        events=request.events,
        config=request.config,
    )
    return result.model_dump(mode="json")
