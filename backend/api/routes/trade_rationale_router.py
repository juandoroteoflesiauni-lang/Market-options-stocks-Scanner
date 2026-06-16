"""Trade rationale report HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend.audit.audit_complex_store import AuditComplexStore
from backend.config.settings import load_settings
from backend.services.reports.trade_rationale_report_service import TradeRationaleReportService

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def _service() -> TradeRationaleReportService:
    settings = load_settings()
    store = AuditComplexStore(db_path=settings.audit_db_path)
    return TradeRationaleReportService(store)


@router.get("/trade-rationale/{decision_id}")
async def get_trade_rationale(
    decision_id: str,
    format: str = Query(default="md", pattern="^(md|pdf)$"),
) -> Response:
    """Export institutional trade rationale as Markdown or PDF."""
    service = _service()
    report = await service.load_report(decision_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")

    if format == "pdf":
        pdf_bytes = await service.render_pdf_bytes(report)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="trade_rationale_{decision_id}.pdf"'
            },
        )

    markdown = service.render_markdown(report)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="trade_rationale_{decision_id}.md"'},
    )


__all__ = ["router"]
