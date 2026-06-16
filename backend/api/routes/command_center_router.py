from __future__ import annotations
from typing import Any
"""Command Center API router."""



from fastapi import APIRouter

from backend.services.command_center_service import build_command_center_payload

router = APIRouter(prefix="/api/v1/command-center", tags=["command-center"])


@router.get("/{symbol}")

async def get_command_center(symbol: str) -> dict[str, Any]:
    """Return the institutional home dashboard payload for a symbol."""
    return await build_command_center_payload(symbol)
