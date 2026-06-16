from __future__ import annotations
from typing import Any
"""Market context API router."""



from fastapi import APIRouter

from backend.services.market_context_service import build_market_context_payload

router = APIRouter(prefix="/api/v1/market-context", tags=["market-context"])


@router.get("/{symbol}")

async def get_market_context(symbol: str) -> dict[str, Any]:
    """Return compact macro/live context for a symbol."""
    return await build_market_context_payload(symbol)
