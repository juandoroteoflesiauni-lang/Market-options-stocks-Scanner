"""Ruta 1 prioritaria (11 tickers legacy) vs Ruta 2 scan dinámico. # [PD-8][IM]

Con ``DUAL_BOT_FIXED_UNIVERSE=true`` la ruta 1 activa pasa a
``DUAL_BOT_CORE_UNIVERSE`` (20 tickers) vía :func:`resolve_route1_watchlist`.
"""

from __future__ import annotations

import os

from backend.config.dual_bot_core_universe import (
    DUAL_BOT_CORE_UNIVERSE,
    DUAL_BOT_CORE_UNIVERSE_SET,
    dual_bot_fixed_universe_enabled,
)

# Tickers legacy R1 (11) — pipeline completo antes del universo core de 20.
ALPACA_ROUTE1_LEGACY_WATCHLIST: tuple[str, ...] = (
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

# Alias retrocompatible (import estático en módulos legacy).
ALPACA_ROUTE1_WATCHLIST: tuple[str, ...] = ALPACA_ROUTE1_LEGACY_WATCHLIST

ROUTE1_SYMBOLS: frozenset[str] = frozenset(ALPACA_ROUTE1_LEGACY_WATCHLIST)

# Top circunstancial de la ruta 2 (embudo clásico Vol/ATR/RS/MACD).
ROUTE2_FUNNEL_TOP_N: int = 20

# Sizing: R1 siempre gana capital y ejecuta primero.
ROUTE1_NOTIONAL_MULTIPLIER: float = float(os.getenv("ALPACA_ROUTE1_NOTIONAL_MULT", "1.5"))
ROUTE2_NOTIONAL_MULTIPLIER: float = float(os.getenv("ALPACA_ROUTE2_NOTIONAL_MULT", "1.0"))


def resolve_route1_watchlist() -> tuple[str, ...]:
    """Watchlist R1 efectiva según modo fijo o legacy."""
    if dual_bot_fixed_universe_enabled():
        return DUAL_BOT_CORE_UNIVERSE
    return ALPACA_ROUTE1_LEGACY_WATCHLIST


def route1_symbols_set() -> frozenset[str]:
    """Conjunto de símbolos R1 para gates y deduplicación."""
    if dual_bot_fixed_universe_enabled():
        return DUAL_BOT_CORE_UNIVERSE_SET
    return ROUTE1_SYMBOLS


def is_route1_symbol(symbol: str) -> bool:
    """True si el símbolo pertenece a la ruta prioritaria activa."""
    return symbol.upper().strip() in route1_symbols_set()


def scan_pool_excluding_route1(
    universe: tuple[str, ...],
    *,
    pool_size: int,
    benchmark: str = "SPY",
) -> tuple[str, ...]:
    """Pool de scan (~100) sin los tickers R1 activos ni el benchmark."""
    seen: set[str] = set(route1_symbols_set())
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
    "ALPACA_ROUTE1_LEGACY_WATCHLIST",
    "ALPACA_ROUTE1_WATCHLIST",
    "ROUTE1_NOTIONAL_MULTIPLIER",
    "ROUTE1_SYMBOLS",
    "ROUTE2_FUNNEL_TOP_N",
    "ROUTE2_NOTIONAL_MULTIPLIER",
    "is_route1_symbol",
    "resolve_route1_watchlist",
    "route1_symbols_set",
    "scan_pool_excluding_route1",
]
