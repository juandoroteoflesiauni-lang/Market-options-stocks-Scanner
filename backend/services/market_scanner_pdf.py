from __future__ import annotations
"""Minimal PDF export for Market Scanner (desk print / audit)."""


import math
from io import BytesIO
from typing import TYPE_CHECKING

from backend.domain.market_scanner_models import MarketScannerResponse

if TYPE_CHECKING:
    pass


def _clean_pdf_text(text: str | None) -> str:
    if not text:
        return ""
    # Replace common non-latin1 characters used in the app
    return (
        str(text)
        .replace("↑", "^")
        .replace("↓", "v")
        .replace("⚡", "!")
        .replace("⭐", "*")
        .replace("💎", "#")
        .replace("🔥", "!!")
        .replace(" BULL", " (Bull)")
        .replace(" BEAR", " (Bear)")
    )


def render_scanner_pdf_bytes(
    result: MarketScannerResponse, *, title: str = "Market Scanner"
) -> bytes:
    """Build a compact single-page PDF. Requires ``fpdf2``."""
    try:
        from fpdf import FPDF
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install fpdf2 for PDF export: pip install fpdf2") from exc

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=15, top=15, right=15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(pdf.epw, 8, _clean_pdf_text(title))
    pdf.set_font("Helvetica", size=9)
    pdf.ln(2)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        pdf.epw,
        5,
        f"Universe: {result.universe} | Rows: {len(result.rows)} | "
        f"Scoring: {result.scoring_version} | Generated: {result.generated_at.isoformat()}",
    )
    pdf.ln(3)
    regime = result.universe_regime_summary or {}
    if regime.get("status") == "ok":
        mean_score = regime.get("mean_scanner_score")
        mean_score_str = (
            f"{float(mean_score):.1f}"
            if mean_score is not None and math.isfinite(float(mean_score))
            else "N/A"
        )
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(
            pdf.epw,
            5,
            _clean_pdf_text(f"Regime tone: {regime.get('tone')} | mean score: {mean_score_str}"),
        )
        pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Top symbols", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=8)
    pdf.set_font("Helvetica", size=8)
    for row in result.rows[:35]:
        s_val = row.scanner_score
        s_str = (
            f"{float(s_val):.1f}" if s_val is not None and math.isfinite(float(s_val)) else "N/A"
        )
        txt = _clean_pdf_text(
            f"{row.symbol} | score: {s_str} | grade: {row.setup_grade} | dir: {row.direction}"
        )
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.epw, 5, txt)
    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()
