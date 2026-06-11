"""API Router para la Mesa de Dinero Virtual

Endpoints para orquestación de agentes, streaming de tesis y gestión de informes.
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.layer_5_mesa_dinero.orchestrator import MesaDineroOrchestrator, ThesisStreamManager
from backend.layer_5_mesa_dinero.report_factory import ReportFactory, ReportType

router = APIRouter(prefix="/api/v1/mesa-dinero", tags=["mesa-dinero"])

# Instancia lazy del orquestador y gestor de streams
_orchestrator: MesaDineroOrchestrator | None = None
stream_manager = ThesisStreamManager()


def _get_orchestrator() -> MesaDineroOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MesaDineroOrchestrator()
    return _orchestrator


@router.post("/thesis-stream/{symbol}")
async def start_thesis_stream(symbol: str) -> dict[str, str]:
    """Inicia un stream de tesis para un símbolo específico"""
    try:
        client_id = str(uuid.uuid4())
        stream_id = await stream_manager.start_thesis_stream(symbol, client_id)
        asyncio.create_task(_run_thesis_stream(symbol, stream_id))
        return {"streamId": stream_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start stream: {e!s}") from e


async def _append_stream_event(stream_id: str, event: dict[str, Any]) -> None:
    if stream_id not in stream_manager.active_streams:
        return
    stream = stream_manager.active_streams[stream_id]
    stream.setdefault("events", []).append(event)
    stream["last_update"] = datetime.now()
    if event.get("type") in {"completion", "error"}:
        stream["is_complete"] = True


async def _run_thesis_stream(symbol: str, stream_id: str) -> None:
    """Genera la tesis en background y publica eventos consumibles por SSE."""
    try:
        await _append_stream_event(
            stream_id,
            {
                "type": "status_update",
                "content": f"Iniciando tesis institucional para {symbol.upper()}",
                "agent": "orchestrator",
                "progress": 5,
                "timestamp": datetime.now().isoformat(),
            },
        )
        await _append_stream_event(
            stream_id,
            {
                "type": "narrative_update",
                "content": "Recolectando contexto tecnico, fundamental, de opciones y probabilistico.",
                "agent": "data_ingestion",
                "progress": 20,
                "timestamp": datetime.now().isoformat(),
            },
        )

        thesis_report = await _get_orchestrator().generate_thesis(symbol)
        narratives = list(thesis_report.narratives.items())
        total = max(len(narratives), 1)

        for idx, (agent_type, narrative) in enumerate(narratives, start=1):
            content = narrative.content or narrative.error or "No se devolvio narrativa."
            await _append_stream_event(
                stream_id,
                {
                    "type": "narrative_update",
                    "content": content,
                    "agent": agent_type.value,
                    "progress": min(85, 25 + int(idx / total * 55)),
                    "timestamp": datetime.now().isoformat(),
                },
            )

        final_content = thesis_report.multimodal_synthesis or thesis_report.tactical_recommendation
        if not final_content:
            final_content = (
                "\n\n".join(narrative.content for _, narrative in narratives if narrative.content)
                or "La tesis fue generada, pero no se recibio contenido narrativo."
            )
        await _append_stream_event(
            stream_id,
            {
                "type": "completion",
                "content": final_content,
                "agent": "orchestrator",
                "progress": 100,
                "timestamp": datetime.now().isoformat(),
            },
        )
    except Exception as exc:
        await _append_stream_event(
            stream_id,
            {
                "type": "error",
                "error": f"No se pudo generar la tesis: {exc}",
                "agent": "orchestrator",
                "progress": 0,
                "timestamp": datetime.now().isoformat(),
            },
        )


@router.get("/stream/{stream_id}")
async def stream_thesis_events(stream_id: str):
    """Endpoint SSE para streaming de eventos de tesis"""

    async def event_generator():
        """Generador de eventos SSE"""
        cursor = 0
        try:
            while True:
                # Verificar si el stream aún existe
                if stream_id not in stream_manager.active_streams:
                    break

                stream_info = stream_manager.active_streams[stream_id]
                events = stream_info.get("events", [])
                while cursor < len(events):
                    yield f"data: {json.dumps(events[cursor])}\n\n"
                    cursor += 1

                if stream_info.get("is_complete") and cursor >= len(events):
                    await asyncio.sleep(1)
                    break

                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            # Cliente desconectado
            await stream_manager.close_stream(stream_id)
            raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.delete("/stream/{stream_id}")
async def stop_thesis_stream(stream_id: str):
    """Detiene un stream de tesis"""
    try:
        await stream_manager.close_stream(stream_id)
        return {"message": "Stream stopped successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop stream: {e!s}") from e


@router.post("/generate-thesis/{symbol}")
async def generate_institutional_thesis(symbol: str) -> dict[str, Any]:
    """Genera una tesis institucional completa"""
    try:
        # Generar tesis usando el orquestador
        thesis_report = await _get_orchestrator().generate_thesis(symbol)

        # Convertir a formato serializable
        return {
            "symbol": thesis_report.symbol,
            "timestamp": thesis_report.timestamp.isoformat(),
            "bias": thesis_report.bias,
            "conviction": thesis_report.conviction,
            "narratives": {
                agent_type.value: {
                    "content": narrative.content,
                    "confidence": narrative.confidence,
                    "error": narrative.error,
                }
                for agent_type, narrative in thesis_report.narratives.items()
            },
            "multimodal_synthesis": thesis_report.multimodal_synthesis,
            "data_sources": [source.value for source in thesis_report.data_sources],
            "risk_assessment": thesis_report.risk_assessment,
            "tactical_recommendation": thesis_report.tactical_recommendation,
            "invalidations": thesis_report.invalidations,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate thesis: {e!s}") from e


@router.post("/report/{report_type}/{symbol}")
async def generate_specialized_report(
    report_type: str, symbol: str, request: Request
) -> dict[str, Any]:
    """Genera un informe especializado"""
    try:
        # Parsear datos de la solicitud
        data = await request.json()

        # Convertir tipo de informe
        try:
            report_type_enum = ReportType(report_type.lower())
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail=f"Invalid report type: {report_type}"
            ) from e

        # Generar informe usando la fábrica
        report = ReportFactory.create_report(
            report_type_enum,
            symbol,
            data,
            [],  # Fuentes de datos vacías por ahora
        )

        # Convertir a formato serializable
        return {
            "report_id": report.metadata.report_id,
            "created_at": report.metadata.created_at.isoformat(),
            "symbol": report.metadata.symbol,
            "report_type": report.metadata.report_type.value,
            "confidence_score": report.metadata.confidence_score,
            "executive_summary": {
                "title": report.executive_summary.title,
                "key_insights": report.executive_summary.key_insights,
                "recommendation": report.executive_summary.recommendation,
                "risk_level": report.executive_summary.risk_level.value,
                "timeframe": report.executive_summary.timeframe,
            },
            "report_data": report.dict(),  # Datos específicos del tipo de informe
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {e!s}") from e


@router.get("/status")
async def get_mesa_dinero_status() -> dict[str, Any]:
    """Obtiene el estado del sistema Mesa de Dinero"""
    return {
        "status": "operational",
        "active_streams": len(stream_manager.active_streams),
        "agents_available": 6,
        "data_sources_active": 5,
        "last_updated": datetime.now().isoformat(),
    }
