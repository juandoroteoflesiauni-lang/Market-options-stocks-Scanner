"""Markdown export for Market Scanner runs (audit / desk handoff)."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.domain.market_scanner_models import MarketScannerResponse, MarketScannerRow


def render_scanner_markdown_report(
    result: MarketScannerResponse, *, title: str = "Market Scanner"
) -> str:
    """Render a compact institutional-style markdown report."""
    lines: list[str] = [
        f"# {title}",
        "",
        f"- Generated: `{result.generated_at.isoformat()}`",
        f"- Universe: `{result.universe}`",
        f"- Scoring: `{result.scoring_version}`",
        f"- Catalog: `{result.catalog_version}`",
        "",
    ]
    regime = result.universe_regime_summary or {}
    if regime.get("status") == "ok":
        lines.extend(
            [
                "## Universe regime",
                "",
                f"- Tone: **{regime.get('tone')}**",
                f"- Mean score: {regime.get('mean_scanner_score')}",
                f"- Bullish share: {regime.get('bullish_share')}",
                f"- Bearish share: {regime.get('bearish_share')}",
                "",
            ]
        )

    lines.append("## Top opportunities")
    lines.append("")
    for idx, row in enumerate(result.rows[:30], start=1):
        lines.extend(_row_section(idx, row))

    lines.append("---")
    lines.append("")
    lines.append("## Data quality")
    lines.append("")
    for k, v in (result.data_quality or {}).items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    if result.skipped_symbols:
        lines.append("## Skipped symbols")
        lines.append("")
        for sym, reason in list(result.skipped_symbols.items())[:40]:
            lines.append(f"- `{sym}`: {reason}")
        lines.append("")
    lines.append(f"_Exported at {datetime.now(UTC).isoformat()}_")
    return "\n".join(lines)


def _row_section(idx: int, row: MarketScannerRow) -> list[str]:
    out: list[str] = [
        f"### {idx}. {row.symbol}",
        "",
        f"- Score: **{row.scanner_score}** (grade `{row.setup_grade}`, dir `{row.direction}`)",
    ]
    if row.score_ci_low is not None and row.score_ci_high is not None:
        out.append(f"- Score 68% band: `{row.score_ci_low}` – `{row.score_ci_high}`")
    if row.risk_hints:
        out.append(f"- Risk hints: `{row.risk_hints}`")
    if row.reasons:
        out.append("- Reasons:")
        for r in row.reasons[:5]:
            out.append(f"  - {r}")
    if row.warnings:
        out.append("- Warnings:")
        for w in row.warnings[:4]:
            out.append(f"  - {w}")
    audit = row.score_audit or {}
    if audit.get("meta_learner") and isinstance(audit["meta_learner"], dict):
        ml = audit["meta_learner"]
        if ml.get("status") != "unavailable":
            out.append(f"- Meta-learner: `{ml}`")
    out.append("")
    return out
