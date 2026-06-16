"""Institutional trade rationale report exporter. # [TH][PD-2]"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from backend.domain.agentic_models import TradeRationaleReport
from backend.services.market_scanner_pdf import _clean_pdf_text

logger = logging.getLogger(__name__)


class TradeRationaleReportService:
    """Loads DuckDB agentic decisions and renders Markdown/PDF."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def load_report(self, decision_id: str) -> TradeRationaleReport | None:
        """Load report model from audit store (executor offload)."""
        loop = asyncio.get_running_loop()
        row = await loop.run_in_executor(None, self._store.get_agentic_decision, decision_id)
        if row is None:
            return None
        return self._assemble_report(row)

    @staticmethod
    def _assemble_report(row: dict[str, Any]) -> TradeRationaleReport:
        payload = row.get("payload") or {}
        committee = payload.get("committee") or {}
        verdict = committee.get("verdict") or {}
        bull = committee.get("bull") or {}
        bear = committee.get("bear") or {}
        consensus_parts = [
            f"Bull: {bull.get('thesis', '')}",
            f"Bear: {bear.get('thesis', '')}",
            f"Verdict: {verdict.get('rationale', '')}",
        ]
        consensus_text = "\n".join(p for p in consensus_parts if p.strip())
        consensus_available = bool(consensus_text.strip()) and not payload.get("quant_default_used")
        if not consensus_available:
            consensus_text = "LLM consensus unavailable — technical quant record only."

        created_raw = payload.get("created_at") or row.get("timestamp")
        if isinstance(created_raw, str):
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        else:
            created_at = datetime.now(tz=UTC)

        return TradeRationaleReport(
            decision_id=str(row.get("event_id", "")),
            module=str(row.get("module", "unknown")),
            symbol=str(row.get("symbol", "")),
            contract_symbol=str(row.get("contract_symbol", "")),
            final_decision=str(row.get("final_decision", "PASS")),
            quant_default_used=bool(row.get("quant_default_used", False)),
            technical_summary={
                "correlation_id": payload.get("correlation_id"),
                "macro_risk": payload.get("macro_risk"),
                "options_analysis": payload.get("options_analysis"),
                "verdict": verdict,
            },
            consensus_text=consensus_text,
            consensus_available=consensus_available,
            created_at=created_at,
        )

    @staticmethod
    def render_markdown(report: TradeRationaleReport) -> str:
        """Render institutional Markdown report."""
        lines = [
            "# Trade Rationale Report",
            "",
            f"- **Decision ID:** {report.decision_id}",
            f"- **Module:** {report.module}",
            f"- **Symbol:** {report.symbol}",
            f"- **Contract:** {report.contract_symbol}",
            f"- **Final decision:** {report.final_decision}",
            f"- **Quant default used:** {report.quant_default_used}",
            f"- **Created at:** {report.created_at.isoformat()}",
            "",
            "## Technical Record",
            "",
            "```json",
            str(report.technical_summary),
            "```",
            "",
            "## LLM Consensus",
            "",
        ]
        if report.consensus_available:
            lines.append(report.consensus_text)
        else:
            lines.append("*LLM consensus unavailable — technical quant record only.*")
        return "\n".join(lines)

    async def render_pdf_bytes(self, report: TradeRationaleReport) -> bytes:
        """Render PDF off the event loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._render_pdf_sync, report)

    @staticmethod
    def _render_pdf_sync(report: TradeRationaleReport) -> bytes:
        try:
            from fpdf import FPDF
        except ImportError as exc:
            raise RuntimeError("Install fpdf2 for PDF export: pip install fpdf2") from exc

        pdf = FPDF(orientation="P", unit="mm", format="A4")
        pdf.set_margins(left=15, top=15, right=15)
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.multi_cell(pdf.epw, 8, _clean_pdf_text("Trade Rationale Report"))
        pdf.set_font("Helvetica", size=9)
        pdf.ln(2)
        for line in (
            f"Decision: {report.decision_id}",
            f"Module: {report.module} | Symbol: {report.symbol}",
            f"Contract: {report.contract_symbol}",
            f"Final: {report.final_decision} | Quant default: {report.quant_default_used}",
        ):
            pdf.multi_cell(pdf.epw, 5, _clean_pdf_text(line))
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "LLM Consensus", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=8)
        pdf.multi_cell(pdf.epw, 5, _clean_pdf_text(report.consensus_text[:4000]))
        return pdf.output()


__all__ = ["TradeRationaleReportService"]
