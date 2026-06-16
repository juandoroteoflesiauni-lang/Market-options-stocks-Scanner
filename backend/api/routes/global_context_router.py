from fastapi import APIRouter, Depends

from backend.models.global_context_snapshot import GlobalContextSnapshot
from backend.services.global_context_engine import GlobalContextEngine

router = APIRouter(prefix="/api/v1/funding", tags=["funding", "global-context"])


def get_context_engine() -> GlobalContextEngine:
    return GlobalContextEngine()


@router.get("/global-context", response_model=GlobalContextSnapshot)
async def get_global_context(
    engine: GlobalContextEngine = Depends(get_context_engine),  # noqa: B008
) -> GlobalContextSnapshot:
    """Retrieve the current macro context and market regime."""
    # Placeholder context data for Sprint 1 until full hub wiring
    context_data = {
        "vix": 15.0,
        "spy": None,
        "qqq": None,
    }
    return engine.evaluate(context_data)
