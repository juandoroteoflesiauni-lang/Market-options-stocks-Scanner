"""Symbol routing: equity synthetics (BingX) vs crypto (BingX + Deribit) vs options tickers."""

from __future__ import annotations

import re

from backend.layer_1_data.datos.bingx_client import is_perp_symbol
from backend.services.bingx_symbol_linker import underlying_from_bingx_symbol

_CRYPTO_ROOTS: frozenset[str] = frozenset(
    {
        "BTC",
        "ETH",
        "SOL",
        "BNB",
        "XRP",
        "ADA",
        "DOGE",
        "AVAX",
        "DOT",
        "LINK",
        "MATIC",
        "LTC",
        "BCH",
        "ATOM",
        "UNI",
        "NEAR",
        "APT",
        "ARB",
        "OP",
        "SUI",
    }
)

_STABLE_SUFFIXES = ("USDT", "USDC", "USD")


def normalize_scanner_symbol(symbol: str) -> str:
    """Uppercase root ticker without venue suffix."""
    raw = str(symbol or "").upper().strip()
    if not raw:
        return ""
    if raw.endswith("-USDT"):
        return underlying_from_bingx_symbol(raw)
    if raw.endswith("USDT") and "-" not in raw:
        return raw[: -len("USDT")]
    if raw.endswith("USD") and len(raw) > 3:
        return raw[: -len("USD")]
    return raw


def is_crypto_root(root: str) -> bool:
    """True for major crypto underlyings (Deribit options path)."""
    r = normalize_scanner_symbol(root)
    return r in _CRYPTO_ROOTS


def is_equity_root(root: str) -> bool:
    """True for US equity-style roots (Massive options + BingX synthetic perp)."""
    r = normalize_scanner_symbol(root)
    if not r or is_crypto_root(r):
        return False
    if r.endswith("ON") and len(r) > 2:
        r = r[:-2]
    if is_perp_symbol(f"{r}-USDT") and not is_crypto_root(r):
        return True
    return bool(re.fullmatch(r"[A-Z]{1,6}", r)) and not is_crypto_root(r)


def bingx_venue_symbol(root: str) -> str:
    """Map scanner root to BingX venue symbol (e.g. AAPL → AAPL-USDT)."""
    r = normalize_scanner_symbol(root)
    if not r:
        return ""
    if is_perp_symbol(f"{r}-USDT") or is_equity_root(r) or is_crypto_root(r):
        return f"{r}-USDT"
    return f"{r}-USDT"


def options_chain_symbol(root: str) -> str:
    """Underlying for options chain: equity ticker or crypto currency code."""
    r = normalize_scanner_symbol(root)
    if is_crypto_root(r):
        return r
    return r


def deribit_currency(root: str) -> str | None:
    """Deribit currency code for crypto options, else None."""
    r = normalize_scanner_symbol(root)
    if is_crypto_root(r):
        return r if r in {"BTC", "ETH"} else r
    return None


def instrument_data_class(root: str) -> str:
    """Routing label for real-data providers."""
    r = normalize_scanner_symbol(root)
    if is_crypto_root(r):
        return "crypto"
    if is_equity_root(r):
        return "equity"
    return "other"
