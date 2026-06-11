"""Funnel overview endpoint — phase metrics + market breadth for the dashboard."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request

from backend.api.contracts import (
    FunnelOverviewResponse,
    MarketBreadthResponse,
    PhaseMetricsResponse,
)

router = APIRouter(prefix="/api/funnel", tags=["funnel"])


@router.get("/overview", response_model=FunnelOverviewResponse)
async def get_funnel_overview(request: Request) -> FunnelOverviewResponse:
    """Returns metrics for all 4 funnel phases + real-time market breadth."""
    now = datetime.now(UTC).isoformat()

    breadth = getattr(request.app.state, "market_breadth", None)
    breadth_data: MarketBreadthResponse | None = None
    if breadth is not None:
        snap = breadth.snapshot
        breadth_data = MarketBreadthResponse(
            bullish=snap.bullish,
            bearish=snap.bearish,
            no_data=snap.no_data,
            total_scanned=snap.total_scanned,
            bullish_pct=snap.bullish_pct,
            bearish_pct=snap.bearish_pct,
            coverage_pct=snap.coverage_pct,
            last_updated=snap.last_updated,
        )

    return FunnelOverviewResponse(
        phases=[
            PhaseMetricsResponse(
                phase_id="A",
                label="Scanner",
                status="ACTIVE" if breadth_data and breadth_data.total_scanned > 0 else "IDLE",
                input_count=breadth_data.total_scanned if breadth_data else 0,
                output_count=0,
                last_processed_at=breadth_data.last_updated if breadth_data else None,
                processing_time_ms=None,
            ),
            PhaseMetricsResponse(
                phase_id="B",
                label="Microstructure",
                status="IDLE",
                input_count=0,
                output_count=0,
                last_processed_at=None,
                processing_time_ms=None,
            ),
            PhaseMetricsResponse(
                phase_id="C",
                label="Derivatives",
                status="IDLE",
                input_count=0,
                output_count=0,
                last_processed_at=None,
                processing_time_ms=None,
            ),
            PhaseMetricsResponse(
                phase_id="D",
                label="Monitor",
                status="DISABLED",
                input_count=0,
                output_count=0,
                last_processed_at=None,
                processing_time_ms=None,
            ),
        ],
        total_signals_emitted=0,
        updated_at=now,
        market_breadth=breadth_data,
    )
