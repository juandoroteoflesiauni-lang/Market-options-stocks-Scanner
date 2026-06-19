"""Política de tier compartida: R1 watchlist + posiciones abiertas = quant completo. # [PD-8][IM]"""

from __future__ import annotations

import os

from backend.config.alpaca_priority_route import is_route1_symbol
from backend.config.dual_bot_core_universe import (
    DUAL_BOT_CORE_UNIVERSE_SET,
    core_symbol_has_full_quant,
    dual_bot_fixed_universe_enabled,
)
from backend.services.bingx_options_bridge import INDEX_OPTIONS_PROXIES
from backend.services.bingx_symbol_linker import underlying_from_bingx_symbol

REASON_TECHNICAL_TIER_ONLY = "technical_tier_only"

_SHARED_OPTIONS_TIER_ENABLED = os.getenv("SHARED_OPTIONS_TIER_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def shared_options_tier_enabled() -> bool:
    """True si el tiering R1+abiertas está activo (BingX y enriquecimiento Alpaca)."""
    return _SHARED_OPTIONS_TIER_ENABLED


def normalize_equity_root(symbol: str) -> str:
    """Normaliza símbolo venue/equity a root subyacente (AAPL, SPX, …)."""
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ""
    if "-" in raw or raw.endswith("USDT") or raw.startswith("NCSK"):
        return underlying_from_bingx_symbol(raw)
    return raw


def _route1_via_index_proxy(root: str) -> bool:
    proxy = INDEX_OPTIONS_PROXIES.get(root)
    return bool(proxy and is_route1_symbol(proxy))


def is_full_quant_tier(
    symbol: str,
    *,
    open_position_roots: frozenset[str] | None = None,
) -> bool:
    """True si el símbolo recibe stack quant completo (opciones + predictivo institucional).

    Elegibles:
    - Los 11 tickers de Ruta 1 Alpaca (``ALPACA_ROUTE1_WATCHLIST``).
    - Índices cuyo proxy ETF está en R1 (p. ej. SPX → SPY).
    - Subyacentes con posición abierta en BingX o Alpaca.
    """
    if not shared_options_tier_enabled():
        return True
    if core_symbol_has_full_quant(symbol):
        return True
    root = normalize_equity_root(symbol)
    if not root:
        return False
    if dual_bot_fixed_universe_enabled() and root in DUAL_BOT_CORE_UNIVERSE_SET:
        return True
    if is_route1_symbol(root) or _route1_via_index_proxy(root):
        return True
    return bool(open_position_roots and root in open_position_roots)


def options_query_symbol_for_root(root: str) -> str:
    """Símbolo a consultar en ``options_snapshot_service`` / SQLite GEX."""
    upper = root.upper().strip()
    if is_route1_symbol(upper):
        return upper
    proxy = INDEX_OPTIONS_PROXIES.get(upper)
    if proxy and is_route1_symbol(proxy):
        return proxy
    return upper


__all__ = [
    "REASON_TECHNICAL_TIER_ONLY",
    "is_full_quant_tier",
    "normalize_equity_root",
    "options_query_symbol_for_root",
    "shared_options_tier_enabled",
]
