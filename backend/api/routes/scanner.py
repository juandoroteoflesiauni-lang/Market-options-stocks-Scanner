"""Scanner candidates endpoint — Phase A results."""

from fastapi import APIRouter

from backend.api.contracts import CandidateResponse

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


@router.get("/candidates", response_model=list[CandidateResponse])
async def get_candidates() -> list[CandidateResponse]:
    """Returns current Phase A scanner candidates.

    TODO: Wire to real Phase A scanner output when the scanner is running.
    Currently returns an empty list — the frontend handles this gracefully.
    """
    return []
