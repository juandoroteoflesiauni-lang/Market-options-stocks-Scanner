"""Funnel overview endpoint — phase metrics for the dashboard."""

from datetime import UTC, datetime

from fastapi import APIRouter

from backend.api.contracts import FunnelOverviewResponse, PhaseMetricsResponse

router = APIRouter(prefix="/api/funnel", tags=["funnel"])


@router.get("/overview", response_model=FunnelOverviewResponse)
async def get_funnel_overview() -> FunnelOverviewResponse:
    """Returns metrics for all 4 funnel phases.

    TODO: Wire to real phase state managers when engines are connected.
    Currently returns representative idle state.
    """
    now = datetime.now(UTC).isoformat()

    return FunnelOverviewResponse(
        phases=[
            PhaseMetricsResponse(
                phase_id="A",
                label="Scanner",
                status="IDLE",
                input_count=5000,
                output_count=0,
                last_processed_at=None,
                processing_time_ms=None,
            ),
            PhaseMetricsResponse(
                phase_id="B",
                label="Microstructure",
                status="IDLE",
                input_count=300,
                output_count=0,
                last_processed_at=None,
                processing_time_ms=None,
            ),
            PhaseMetricsResponse(
                phase_id="C",
                label="Derivatives",
                status="IDLE",
                input_count=20,
                output_count=0,
                last_processed_at=None,
                processing_time_ms=None,
            ),
            PhaseMetricsResponse(
                phase_id="D",
                label="Monitor",
                status="DISABLED",
                input_count=5,
                output_count=0,
                last_processed_at=None,
                processing_time_ms=None,
            ),
        ],
        total_signals_emitted=0,
        updated_at=now,
    )
