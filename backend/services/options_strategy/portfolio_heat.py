"""Portfolio heat and sector correlation guards for options sizing. # [PD-3][TH]"""

from __future__ import annotations

import os

_SECTOR_GROUPS: dict[str, frozenset[str]] = {
    "mega_tech": frozenset({"MSFT", "AAPL", "GOOGL", "META", "NVDA", "AMZN"}),
    "indices": frozenset({"SPY", "QQQ"}),
    "high_beta": frozenset({"TSLA", "IREN", "CRWV"}),
}


def symbol_sector(symbol: str) -> str | None:
    """Return sector bucket for R1 correlation grouping."""
    sym = symbol.upper().strip()
    for sector, members in _SECTOR_GROUPS.items():
        if sym in members:
            return sector
    return None


def sector_correlation_size_mult(symbol: str, open_symbols: tuple[str, ...]) -> float:
    """Shrink size when correlated tickers are already in the book."""
    if not open_symbols:
        return 1.0
    sector = symbol_sector(symbol)
    if sector is None:
        return 1.0
    group = _SECTOR_GROUPS[sector]
    sym = symbol.upper()
    overlap = sum(1 for s in open_symbols if s.upper() in group and s.upper() != sym)
    if overlap <= 0:
        return 1.0
    penalty_per = float(os.getenv("OPTIONS_CORRELATION_PENALTY_PER_SYMBOL", "0.15"))
    floor = float(os.getenv("OPTIONS_CORRELATION_SIZE_FLOOR", "0.55"))
    return max(floor, 1.0 - penalty_per * overlap)


def portfolio_heat_allowed(
    current_heat_pct: float,
    new_risk_pct: float,
    *,
    max_total_pct: float | None = None,
) -> bool:
    """True when total portfolio heat stays within cap (default 10-15%)."""
    cap = max_total_pct if max_total_pct is not None else float(
        os.getenv("OPTIONS_MAX_PORTFOLIO_HEAT_PCT", "12.0")
    )
    return (current_heat_pct + new_risk_pct) <= cap


def sector_heat_allowed(
    sector: str | None,
    sector_exposure: dict[str, float],
    new_risk_pct: float,
    *,
    max_sector_pct: float | None = None,
) -> bool:
    """True when sector-level heat stays within cap (default 3-5%)."""
    if sector is None:
        return True
    cap = max_sector_pct if max_sector_pct is not None else float(
        os.getenv("OPTIONS_MAX_SECTOR_HEAT_PCT", "5.0")
    )
    current = sector_exposure.get(sector, 0.0)
    return (current + new_risk_pct) <= cap


__all__ = [
    "portfolio_heat_allowed",
    "sector_correlation_size_mult",
    "sector_heat_allowed",
    "symbol_sector",
]
