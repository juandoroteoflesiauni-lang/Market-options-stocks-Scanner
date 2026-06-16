from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config.logger_setup import get_logger
from backend.services.alpaca_bot_service import AlpacaBotService

router = APIRouter(prefix="/api/v1/alpaca-bot", tags=["alpaca-bot"])
logger = get_logger(__name__)

_service: AlpacaBotService | None = None


def get_service() -> AlpacaBotService:
    global _service
    if _service is None:
        _service = AlpacaBotService()
    return _service


def configure_service(service: AlpacaBotService) -> None:
    global _service
    _service = service

class AlpacaScanRequest(BaseModel):
    pass

class AlpacaTradeRequest(BaseModel):
    allow_live: bool = False

@router.get("/status")
async def get_status() -> dict[str, Any]:
    """Return current bot configuration and status."""
    svc = get_service()
    try:
        balance = await svc._client.fetch_account_balance()
    except Exception:
        balance = {}
    return {
        "service": "alpaca_bot",
        "dry_run": svc.dry_run,
        "trading_mode": svc.trading_mode,
        "is_live": svc.is_live,
        "trading_environment": svc.trading_mode,
        "universe": list(svc._universe),
        "balance": balance,
    }

@router.get("/positions")
async def get_positions() -> list[dict[str, Any]]:
    """Return current open positions."""
    svc = get_service()
    try:
        positions = await svc._client.fetch_positions()
        return positions
    except Exception as exc:
        logger.error("alpaca_bot_router.get_positions error=%s", exc)
        return []

@router.get("/trades")
async def list_trades() -> list[dict[str, Any]]:
    """Return historical executions for the session."""
    return []

@router.post("/cycle")
async def run_cycle(req: AlpacaTradeRequest) -> dict[str, Any]:
    """Run a full cycle."""
    svc = get_service()
    if svc.is_live and not req.allow_live:
        raise HTTPException(
            status_code=400,
            detail="allow_live=true required for LIVE (real-money) trading",
        )

    try:
        result = await svc.run_cycle()
        return result.to_dict()
    except Exception as exc:
        logger.exception("alpaca_bot.cycle_failed")
        raise HTTPException(status_code=500, detail=str(exc))
