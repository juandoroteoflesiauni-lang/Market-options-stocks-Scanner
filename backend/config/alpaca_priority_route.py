"""Ruta 1 prioritaria (11 tickers fijos) vs Ruta 2 scan dinámico. # [PD-8][IM]"""

from __future__ import annotations

import os

# Tickers con pipeline completo: técnico avanzado + opciones + predictivos + L2 BingX.
ALPACA_ROUTE1_WATCHLIST: tuple[str, ...] = (
    "MSFT",
    "TSLA",
    "AAPL",
    "GOOGL",
    "META",
    "NVDA",
    "SPY",
    "QQQ",
    "IREN",
    "CRWV",
    "AMZN",
)

ROUTE1_SYMBOLS: frozenset[str] = frozenset(ALPACA_ROUTE1_WATCHLIST)

# Top circunstancial de la ruta 2 (embudo clásico Vol/ATR/RS/MACD).
ROUTE2_FUNNEL_TOP_N: int = 20

# Sizing: R1 siempre gana capital y ejecuta primero.
ROUTE1_NOTIONAL_MULTIPLIER: float = float(os.getenv("ALPACA_ROUTE1_NOTIONAL_MULT", "1.5"))
ROUTE2_NOTIONAL_MULTIPLIER: float = float(os.getenv("ALPACA_ROUTE2_NOTIONAL_MULT", "1.0"))


def is_route1_symbol(symbol: str) -> bool:
    """True si el símbolo pertenece a la ruta prioritaria."""
    return symbol.upper().strip() in ROUTE1_SYMBOLS


def scan_pool_excluding_route1(
    universe: tuple[str, ...],
    *,
    pool_size: int,
    benchmark: str = "SPY",
) -> tuple[str, ...]:
    """Pool de scan (~100) sin los 11 de R1 ni el benchmark como candidato."""
    seen: set[str] = set(ROUTE1_SYMBOLS)
    pool: list[str] = []
    for sym in universe:
        root = sym.upper().strip()
        if root in seen or root == benchmark.upper():
            continue
        seen.add(root)
        pool.append(root)
        if len(pool) >= pool_size:
            break
    return tuple(pool)


__all__ = [
    "ALPACA_ROUTE1_WATCHLIST",
    "ROUTE1_NOTIONAL_MULTIPLIER",
    "ROUTE1_SYMBOLS",
    "ROUTE2_FUNNEL_TOP_N",
    "ROUTE2_NOTIONAL_MULTIPLIER",
    "is_route1_symbol",
    "scan_pool_excluding_route1",
]
