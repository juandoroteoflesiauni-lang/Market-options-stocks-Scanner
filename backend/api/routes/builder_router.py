"""Builder Plan funding API routes."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.services.builder_backtest_service import (
    BuilderBacktestResult,
    BuilderBacktestService,
)
from backend.services.builder_dashboard_service import (
    BuilderDashboardService,
    BuilderEvaluateBatchRequest,
    BuilderEvaluateBatchResponse,
    BuilderEvaluateRequest,
    BuilderEvaluateResponse,
    BuilderMetricsResponse,
    BuilderStateResponse,
)


class BuilderBacktestRequest(BaseModel):
    """Daily PnL sequence to replay through Builder survival rules."""

    daily_pnls: list[float] = Field(default_factory=list)

router = APIRouter(prefix="/api/v1/funding/builder", tags=["funding", "builder"])


@lru_cache(maxsize=1)
def get_builder_dashboard_service() -> BuilderDashboardService:
    """Return the shared Builder dashboard service."""
    return BuilderDashboardService()


@router.get("/state", response_model=BuilderStateResponse)
def get_builder_state(
    account_id: str = "default",
    service: BuilderDashboardService = Depends(get_builder_dashboard_service),  # noqa: B008
) -> BuilderStateResponse:
    """Return persisted Builder account state and live metrics."""
    return service.get_state(account_id)


@router.get("/metrics", response_model=BuilderMetricsResponse)
def get_builder_metrics(
    account_id: str = "default",
    service: BuilderDashboardService = Depends(get_builder_dashboard_service),  # noqa: B008
) -> BuilderMetricsResponse:
    """Return Builder-native survival and payout metrics."""
    return service.get_metrics(account_id)


@router.post("/evaluate", response_model=BuilderEvaluateResponse)
def post_builder_evaluate(
    request: BuilderEvaluateRequest,
    service: BuilderDashboardService = Depends(get_builder_dashboard_service),  # noqa: B008
) -> BuilderEvaluateResponse:
    """Evaluate a trade candidate under Builder funding rules."""
    return service.evaluate_candidate(request)


@router.post("/evaluate-batch", response_model=BuilderEvaluateBatchResponse)
def post_builder_evaluate_batch(
    request: BuilderEvaluateBatchRequest,
    service: BuilderDashboardService = Depends(get_builder_dashboard_service),  # noqa: B008
) -> BuilderEvaluateBatchResponse:
    """Evaluate a batch of candidates (e.g. scanner leaders) under Builder rules."""
    return service.evaluate_batch(request)


@router.post("/backtest", response_model=BuilderBacktestResult)
def post_builder_backtest(request: BuilderBacktestRequest) -> BuilderBacktestResult:
    """Replay a daily-PnL sequence through Builder survival and payout rules."""
    return BuilderBacktestService().run(request.daily_pnls)
