"""Dark Pool prints snapshot — frozen inter-phase model (Motor ⑭). # [PD-2][IM][TH]

Aggregated dark-pool activity for one underlying over a trailing window. All
notional values are ``Decimal`` (PD-2 — never float for money). Produced by the
``DarkPoolNormalizer`` from a ``MarketDataHub`` fetch; consumed by the BingX
candidate analysis / decision engine (Turno 7).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DarkPoolSnapshot(BaseModel):
    """Immutable dark-pool reading for one symbol."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    print_count_1h: int
    net_notional_usd: Decimal  # PD-2: signed net (buy minus sell), never float
    bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    confidence: float  # [0, 1]
    fetched_at: datetime
    source: str = "unusual_whales"  # or "fmp_fallback"


__all__ = ["DarkPoolSnapshot"]
