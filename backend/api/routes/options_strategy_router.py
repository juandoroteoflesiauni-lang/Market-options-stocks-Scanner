"""API router del módulo Options Strategy (R1): scan, audits, outcomes, calibración. # [PD-3][TH]"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.config.logger_setup import get_logger
from backend.models.options_strategy import OptionsTradeOutcome
from backend.services.options_strategy.audit_store import OptionsStrategyAuditStore
from backend.services.options_strategy.calibration_loop import OptionsStrategyCalibrationLoop
from backend.services.options_strategy.outcome_store import OptionsStrategyOutcomeStore
from backend.services.options_strategy.signal_loop import OptionsStrategySignalLoop

router = APIRouter(prefix="/api/v1/options-strategy", tags=["options-strategy"])
logger = get_logger(__name__)


class ScanRequest(BaseModel):
    """Petición de scan sobre R1 (subconjunto opcional)."""

    symbols: tuple[str, ...] | None = None
    execute: bool = False
    persist: bool = True


@router.post("/scan")
async def scan_route1(req: ScanRequest) -> dict[str, Any]:
    """Corre el pipeline sobre R1; con ``execute=True`` envía órdenes a Alpaca."""
    try:
        if req.execute:
            report = await OptionsStrategySignalLoop.scan_and_execute(
                symbols=req.symbols,
                persist=req.persist,
            )
        else:
            report = OptionsStrategySignalLoop.scan_once(
                symbols=req.symbols,
                persist=req.persist,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return report.as_dict()


@router.get("/audits")
def list_audits(
    symbol: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Lista las decisiones auditadas más recientes."""
    store = OptionsStrategyAuditStore()
    return {"audits": store.list_recent(symbol=symbol, limit=limit)}


@router.get("/audits/{audit_id}")
def get_audit(audit_id: str) -> dict[str, Any]:
    """Devuelve el payload completo de una decisión auditada."""
    payload = OptionsStrategyAuditStore().get(audit_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"audit_not_found: {audit_id}")
    return payload


@router.post("/outcomes")
def record_outcome(outcome: OptionsTradeOutcome) -> dict[str, Any]:
    """Registra el PnL realizado de un trade (feedback para calibración)."""
    if OptionsStrategyAuditStore().get(outcome.audit_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"audit_not_found_for_outcome: {outcome.audit_id}",
        )
    result = OptionsStrategyOutcomeStore().persist(outcome)
    return {
        "audit_id": result.audit_id,
        "inserted": result.inserted,
        "reason": result.reason,
        "is_win": outcome.is_win(),
    }


@router.get("/outcomes")
def list_outcomes(
    symbol: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Lista outcomes/PnL realizados registrados."""
    return {"outcomes": OptionsStrategyOutcomeStore().list_recent(symbol=symbol, limit=limit)}


@router.get("/calibration")
def calibration_report(
    limit: int = Query(default=500, ge=1, le=2000),
    use_outcomes: bool = Query(default=True),
) -> dict[str, Any]:
    """Reporte de calibración sugerido (no escribe config de producción)."""
    report = OptionsStrategyCalibrationLoop.run(limit=limit, use_outcomes=use_outcomes)
    return report.model_dump(mode="json")


__all__ = ["router"]
