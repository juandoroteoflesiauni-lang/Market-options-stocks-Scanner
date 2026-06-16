from fastapi import APIRouter, Depends

from backend.services.sizing_engine import SizingDecision, SizingEngine, SizingRequest

router = APIRouter(prefix="/api/v1/funding", tags=["funding", "sizing"])


def get_sizing_engine() -> SizingEngine:
    return SizingEngine()


@router.post("/sizing", response_model=SizingDecision)
def compute_size(
    request: SizingRequest,
    engine: SizingEngine = Depends(get_sizing_engine),  # noqa: B008
) -> SizingDecision:
    """Compute allowed risk percentage and position notional."""
    return engine.compute_position_size(request)
