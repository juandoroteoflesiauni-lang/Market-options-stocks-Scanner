from __future__ import annotations
"""Central BingX symbol-to-underlying mapper.

Provides three canonical operations for resolving BingX venue symbols:

- :func:`normalize_venue_symbol`       — clean up raw input format
- :func:`underlying_from_bingx_symbol` — extract the canonical underlying ticker
- :func:`classify_underlying`          — determine the instrument category

**ON-suffix quirk**: BingX appends ``ON`` to some stock tickers in
websocket / ticker data (``MSFTON/USDT`` for MSFT, ``PLTRON-USDT`` for PLTR).
The display name from the contracts API is always clean (``MSFT-USDT``), but
raw market-data streams may carry the ON-form.  All three functions handle
this transparently via the ``ON-USDT`` branch in
:func:`underlying_from_bingx_symbol`.
"""


import re
from typing import Literal

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import is_perp_symbol
from backend.services.bingx_universe import MarketType, classify_instrument, is_stock_index_root

logger = get_logger(__name__)

# BingX VST / prod-vst internal API symbols for synthetic equity perps:
# ``NCSKPLTR2USD-USDT`` → underlying ``PLTR``.
_NCSK_VST_STOCK_PERP_RE = re.compile(r"^NCSK([A-Z0-9]+)2USD-USDT$")


def is_ncsk_vst_stock_perp_symbol(symbol: str) -> bool:
    """Return True for BingX internal synthetic stock perp tickers (NCSK*2USD-USDT)."""
    return bool(_NCSK_VST_STOCK_PERP_RE.match(normalize_venue_symbol(symbol)))


def normalize_venue_symbol(symbol: str) -> str:
    """Normalize a raw BingX symbol to canonical venue format.

    Uppercases, strips whitespace, and converts ``/`` separators to ``-``.
    The quote currency (``USDT``) is preserved — use
    :func:`underlying_from_bingx_symbol` to strip it.

    >>> normalize_venue_symbol("msfton/usdt")
    'MSFTON-USDT'
    >>> normalize_venue_symbol("BTC-USDT")
    'BTC-USDT'
    """
    return str(symbol or "").upper().strip().replace("/", "-")


def display_name_from_bingx_symbol(symbol: str) -> str:
    """Convierte símbolo API (``NCSKAAPL2USD-USDT``) o venue a display (``AAPL-USDT``)."""
    root = underlying_from_bingx_symbol(symbol)
    return f"{root}-USDT"


def underlying_from_bingx_symbol(symbol: str) -> str:
    """Extract the canonical underlying ticker from a BingX venue symbol.

    Handles both clean display names and the BingX ON-suffix quirk.  The
    ``ON-USDT`` branch is tested *before* the plain ``-USDT`` branch so that
    ``MSFTON-USDT`` resolves to ``MSFT`` and not ``MSFTON``.

    Examples::

        GOOGL-USDT   → GOOGL
        AAPL-USDT    → AAPL
        MSFTON/USDT  → MSFT
        PLTRON-USDT  → PLTR
        NCSKPLTR2USD-USDT → PLTR
        BTC-USDT     → BTC
    """
    normalized = normalize_venue_symbol(symbol)
    ncsk_match = _NCSK_VST_STOCK_PERP_RE.match(normalized)
    if ncsk_match is not None:
        return ncsk_match.group(1)
    if normalized.endswith("ON-USDT"):
        base = normalized[: -len("ON-USDT")]
    elif normalized.endswith("-USDT"):
        base = normalized[: -len("-USDT")]
    elif normalized.endswith("USDT"):
        base = normalized[: -len("USDT")]
    else:
        base = normalized
    return base.rstrip("-")


def classify_underlying(symbol: str) -> MarketType:
    """Return the :data:`~backend.services.bingx_universe.MarketType` for a
    BingX venue symbol.

    Combines :func:`underlying_from_bingx_symbol` with the BingX universe
    policy from :func:`~backend.services.bingx_universe.classify_instrument`.
    Perp-ness is evaluated on the *resolved* root so that ``MSFTON/USDT``
    (resolved to ``MSFT``) is correctly classified as a synthetic-stock perp.

    Examples::

        classify_underlying("GOOGL-USDT")  → 'stock_perp'
        classify_underlying("BTC-USDT")    → 'crypto_standard'
        classify_underlying("GOLD-USDT")   → 'excluded'
        classify_underlying("USDC-USDT")   → 'excluded'
    """
    root = underlying_from_bingx_symbol(symbol)
    asset_class: Literal["crypto", "synthetic_stock"] = (
        "synthetic_stock" if is_perp_symbol(root) or is_stock_index_root(root) else "crypto"
    )
    market_type, _, _ = classify_instrument(root, asset_class)
    return market_type
