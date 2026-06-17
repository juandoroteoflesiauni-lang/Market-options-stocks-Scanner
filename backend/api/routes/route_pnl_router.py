"""API PnL agregado por ruta. # [PD-3][TH]"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.domain.route_pnl_models import RoutePnLDashboardResponse
from backend.services.route_pnl_service import build_route_pnl_dashboard

router = APIRouter(prefix="/route-pnl", tags=["route-pnl"])


@router.get("/summary", response_model=RoutePnLDashboardResponse)
async def get_route_pnl_summary(
    limit: int = Query(default=200, ge=1, le=500),
) -> RoutePnLDashboardResponse:
    """Rollup PnL por R1, R2, BingX y Options R1."""
    return build_route_pnl_dashboard(limit=limit)
