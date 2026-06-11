"""Specialist engine for Corporate Insider Trading analysis."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("backend.layer_3_specialists.fundamentales.insider_specialist")


@dataclass
class InsiderSignal:
    symbol: str
    conviction_score: float  # 0 to 10
    recent_buys_count: int
    net_shares_transacted: float
    notable_names: list[str]
    is_bullish: bool


class InsiderSpecialist:
    """Detects 'Clustered Buying' and 'Conviction' from insider trades."""

    def analyze(self, data: list[Any], symbol: str) -> InsiderSignal | None:
        if not data:
            return None

        # We look for 'P-Purchase' in the last N records
        # FMP transactionType: 'P-Purchase', 'S-Sale', etc.
        buys = [t for t in data if "PURCHASE" in (t.transactionType or "").upper()]
        sales = [t for t in data if "SALE" in (t.transactionType or "").upper()]

        buy_count = len(buys)
        total_shares = sum(t.securitiesTransacted or 0 for t in buys) - sum(
            t.securitiesTransacted or 0 for t in sales
        )

        # Conviction criteria:
        # Multi-person buying (clustered) is much stronger than one person buying a lot.
        unique_buyers = len(set(t.reportingName for t in buys))

        # Base score on unique buyers (max 5 buyers = 8 points)
        conviction_score = min(8.0, unique_buyers * 1.6)

        # Add bonus for volume relative to ticker (stubbed logic, in real we need context)
        if total_shares > 0:
            conviction_score = min(10.0, conviction_score + 1.0)

        return InsiderSignal(
            symbol=symbol,
            conviction_score=round(conviction_score, 2),
            recent_buys_count=buy_count,
            net_shares_transacted=total_shares,
            notable_names=list(set(t.reportingName for t in buys[:3])),
            is_bullish=conviction_score > 5.0,
        )
