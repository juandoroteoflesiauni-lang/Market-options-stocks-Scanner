"""Provider health diagnostics."""

from __future__ import annotations

from fastapi import APIRouter

from backend.layer_1_data.fetchers.massive_key_registry import get_massive_key_registry

router = APIRouter(prefix="/api/v1/provider-health", tags=["provider-health"])


@router.get("/massive")
async def get_massive_provider_health() -> dict[str, object]:
    """Return Massive/Polygon key health without exposing raw credentials."""
    return get_massive_key_registry().snapshot()
