from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.models.global_context_snapshot import GlobalContextSnapshot
from backend.services.convergence_gate import ConvergenceDecision, ConvergenceGate

router = APIRouter(prefix="/api/v1/funding", tags=["funding", "convergence"])


class ConvergenceRequest(BaseModel):
    direction: str
    context: GlobalContextSnapshot


def get_convergence_gate() -> ConvergenceGate:
    return ConvergenceGate()


@router.post("/convergence", response_model=ConvergenceDecision)
def evaluate_convergence(
    request: ConvergenceRequest,
    gate: ConvergenceGate = Depends(get_convergence_gate),  # noqa: B008
) -> ConvergenceDecision:
    """Evaluate a trade direction against the macro context."""
    return gate.evaluate(request.direction, request.context)
