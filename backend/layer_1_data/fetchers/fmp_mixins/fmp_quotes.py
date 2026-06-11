# ruff: noqa: F403, F405
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.config.logger_setup import get_logger
from backend.domain.fmp_models import *

logger = get_logger(__name__)

if TYPE_CHECKING:
    pass


class FMPQuotesMixin:
    """Mixin for FMP Client."""

    async def get_quote(
        self,
        symbol: str,
    ) -> FMPQuote | None:
        """
        Fetch real-time quote snapshot for symbol.

        FMP endpoint: GET /quote/{symbol}
        https://financialmodelingprep.com/api/v3/quote/{symbol}

        Returns FMPQuote or None.
        """
        data = await self._get(f"/quote/{symbol.upper()}", module="QUOTES", ttl_secs=15.0)
        if isinstance(data, list) and data:
            try:
                return FMPQuote(**data[0])
            except Exception as exc:
                logger.debug("FMP quote parse error: %s", exc)
        return None

    async def get_quotes(
        self,
        symbols: list[str],
    ) -> dict[str, FMPQuote]:
        """
        Fetch real-time quote snapshots for multiple symbols.

        FMP stable endpoint: GET /stable/batch-quote?symbols={symbol1,symbol2,...}
        Falls back to legacy GET /quote/{symbols} when the stable endpoint is unavailable.

        Returns a dictionary mapping each symbol to its FMPQuote or an empty dict.
        """
        if not symbols:
            return {}
        cleaned = [str(s).upper().strip() for s in symbols if str(s).strip()]
        if not cleaned:
            return {}
        symbols_str = ",".join(cleaned)
        data = await self._get_stable(
            "/batch-quote",
            module="QUOTES",
            params={"symbols": symbols_str},
            ttl_secs=15.0,
        )
        if not isinstance(data, list) or not data:
            data = await self._get(f"/quote/{symbols_str}", module="QUOTES", ttl_secs=15.0)
        result = {}
        if isinstance(data, list):
            for item in data:
                try:
                    q = FMPQuote(**item)
                    if q.symbol:
                        result[q.symbol.upper()] = q
                except Exception as exc:
                    logger.debug("FMP quotes parse error: %s", exc)
        return result

    async def get_aftermarket_trades(self, symbols: list[str]) -> dict[str, SimpleNamespace]:
        """Fetch batch aftermarket trades. GET /stable/batch-aftermarket-trade."""
        cleaned = [str(s).upper().strip() for s in symbols if str(s).strip()]
        if not cleaned:
            return {}
        data = await self._get_stable(
            "/batch-aftermarket-trade",
            module="QUOTES",
            params={"symbols": ",".join(cleaned)},
            ttl_secs=5.0,
        )
        return self._namespace_rows_by_symbol(data)

    async def get_aftermarket_quotes(self, symbols: list[str]) -> dict[str, SimpleNamespace]:
        """Fetch batch aftermarket quotes. GET /stable/batch-aftermarket-quote."""
        cleaned = [str(s).upper().strip() for s in symbols if str(s).strip()]
        if not cleaned:
            return {}
        data = await self._get_stable(
            "/batch-aftermarket-quote",
            module="QUOTES",
            params={"symbols": ",".join(cleaned)},
            ttl_secs=5.0,
        )
        return self._namespace_rows_by_symbol(data)

    async def get_aftermarket_trade(self, symbol: str) -> SimpleNamespace | None:
        """Fetch a single aftermarket trade. GET /stable/aftermarket-trade."""
        sym = symbol.upper().strip()
        data = await self._get_stable(
            "/aftermarket-trade",
            module="QUOTES",
            params={"symbol": sym},
            ttl_secs=5.0,
        )
        return self._namespace_rows_by_symbol(data).get(sym)

    async def get_aftermarket_quote(self, symbol: str) -> SimpleNamespace | None:
        """Fetch a single aftermarket quote. GET /stable/aftermarket-quote."""
        sym = symbol.upper().strip()
        data = await self._get_stable(
            "/aftermarket-quote",
            module="QUOTES",
            params={"symbol": sym},
            ttl_secs=5.0,
        )
        return self._namespace_rows_by_symbol(data).get(sym)
