"""Watchlist fija para feed L2 BingX → motores de microestructura. # [PD-8][IM]"""

from __future__ import annotations

from backend.config.alpaca_priority_route import ALPACA_ROUTE1_WATCHLIST

# Misma lista que Ruta 1 prioritaria (11 tickers con perp sintético BingX).
EQUITY_L2_WATCHLIST: tuple[str, ...] = ALPACA_ROUTE1_WATCHLIST

EQUITY_L2_POLL_INTERVAL_S: int = 5
EQUITY_L2_FAST_POLL_INTERVAL_S: int = 2
EQUITY_L2_DEPTH_LIMIT: int = 20
EQUITY_L2_WS_DEPTH_LEVEL: int = 20
EQUITY_L2_TRADE_LIMIT: int = 120
EQUITY_L2_FETCH_CONCURRENCY: int = 4
