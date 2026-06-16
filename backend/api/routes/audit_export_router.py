import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from backend.infrastructure.repositories.trade_history_repository import TradeHistoryRepository

router = APIRouter(prefix="/api/v1/funding/audit", tags=["funding", "audit"])


def get_trade_repo() -> TradeHistoryRepository:
    return TradeHistoryRepository()


@router.get("/export")
def export_audit_csv(
    window: int = 1000,
    repo: TradeHistoryRepository = Depends(get_trade_repo),  # noqa: B008
) -> StreamingResponse:
    """
    Exports historical trades as a CSV file for prop firm compliance or internal audit.
    """
    trades = repo.get_recent(window=window)

    # Create an in-memory string buffer for CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(
        [
            "trade_id",
            "setup_type",
            "symbol",
            "direction",
            "entry_price",
            "exit_price",
            "quantity",
            "risk_r",
            "realized_r",
            "pnl",
            "opened_at",
            "closed_at",
            "equity_after",
            "mode",
        ]
    )

    # Write rows
    for t in trades:
        writer.writerow(
            [
                t.trade_id,
                t.setup_type,
                t.symbol,
                t.direction,
                str(t.entry_price),
                str(t.exit_price) if t.exit_price else "",
                str(t.quantity),
                str(t.risk_r),
                str(t.realized_r) if t.realized_r else "",
                str(t.pnl) if t.pnl else "",
                t.opened_at.isoformat(),
                t.closed_at.isoformat() if t.closed_at else "",
                str(t.equity_after),
                t.mode,
            ]
        )

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_export.csv"},
    )
