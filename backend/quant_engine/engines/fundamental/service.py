from __future__ import annotations
from typing import Any
"""Bloque fundamental para thesis (entradas ya resueltas; sin IO en capa 3)."""



from backend.domain.fmp_models import FMPProfile, FMPRatiosTTM
from backend.domain.thesis_v2 import ThesisBlock


def build_fundamental_thesis_block_from_snapshots(
    symbol: str,
    profile: FMPProfile | None,
    ratios_ttm: FMPRatiosTTM | None,
    enrichment: dict[str, Any] | None = None,
) -> ThesisBlock:
    """Perfil + ratios TTM cuando existen; si no, UNAVAILABLE explícito."""
    sym = symbol.upper().strip()
    enrichment = enrichment or {}
    if profile is None and ratios_ttm is None:
        return ThesisBlock(
            metrics={"symbol": sym, **enrichment},
            source="UNAVAILABLE",
            limitations=[
                "FMP profile and ratios TTM empty — check FMP_KEY_* in environment or symbol validity.",
            ],
            confidence=0.0,
        )

    metrics: dict[str, object] = {"symbol": sym}
    if profile is not None:
        metrics.update(
            {
                "company_name": profile.companyName,
                "sector": profile.sector,
                "industry": profile.industry,
                "mkt_cap": profile.mktCap,
            }
        )
    if ratios_ttm is not None:
        metrics.update(
            {
                "pe_ratio_ttm": ratios_ttm.peRatioTTM,
                "pb_ratio_ttm": ratios_ttm.priceToBookRatioTTM,
                "return_on_equity_ttm": ratios_ttm.returnOnEquityTTM,
                "debt_equity_ratio_ttm": ratios_ttm.debtEquityRatioTTM,
            }
        )
    metrics.update(enrichment)

    return ThesisBlock(
        metrics=metrics,
        source="FMP",
        limitations=[
            "Single snapshot (profile + ratios TTM); full stack via "
            "FMPClient.get_full_fundamental_analysis can be added later.",
        ],
        confidence=0.75 if profile and ratios_ttm else 0.45,
    )
