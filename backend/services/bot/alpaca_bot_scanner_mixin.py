"""Mixin de obtención de datos del bot Alpaca (klines → SymbolBars). # [IM][TH]"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.config.logger_setup import get_logger
from backend.services.alpaca_universe_funnel import SymbolBars

logger = get_logger(__name__)


class AlpacaBotScannerMixin:
    """Obtiene velas OHLCV y las normaliza a ``SymbolBars`` para el embudo."""

    _scan_interval: str
    _klines_per_symbol: int
    _gather_concurrency: int

    async def _fetch_alpaca_klines(
        self, symbol: str, *, interval: str, limit: int
    ) -> list[dict[str, Any]]:
        try:
            from backend.layer_1_data.datos.intraday_bars_fetcher import fetch_intraday_bars

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: fetch_intraday_bars(symbol, interval=interval, max_bars=limit)
            )
            bars: list[dict[str, Any]] = result.get("bars", [])
            return bars
        except Exception as exc:
            logger.debug("alpaca_bot.klines_fallback symbol=%s error=%s", symbol, exc)
            return []

    @staticmethod
    def _bars_from_klines(symbol: str, klines: list[dict[str, Any]]) -> SymbolBars:
        highs: list[float] = []
        lows: list[float] = []
        closes: list[float] = []
        volumes: list[float] = []
        for k in klines:
            highs.append(float(k.get("high") or k.get("h") or 0.0))
            lows.append(float(k.get("low") or k.get("l") or 0.0))
            closes.append(float(k.get("close") or k.get("c") or 0.0))
            volumes.append(float(k.get("volume") or k.get("v") or 0.0))
        return SymbolBars(
            symbol=symbol,
            highs=tuple(highs),
            lows=tuple(lows),
            closes=tuple(closes),
            volumes=tuple(volumes),
        )

    async def _symbol_bars(self, symbol: str) -> SymbolBars:
        klines = await self._fetch_alpaca_klines(
            symbol, interval=self._scan_interval, limit=self._klines_per_symbol
        )
        return self._bars_from_klines(symbol, klines)

    async def _gather_klines(self, symbols: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
        sem = asyncio.Semaphore(self._gather_concurrency)

        async def _one(sym: str) -> tuple[str, list[dict[str, Any]]]:
            async with sem:
                try:
                    klines = await self._fetch_alpaca_klines(
                        sym, interval=self._scan_interval, limit=self._klines_per_symbol
                    )
                    return sym, klines
                except Exception as exc:
                    logger.warning("alpaca_bot.klines_failed symbol=%s error=%s", sym, exc)
                    return sym, []

        results = await asyncio.gather(*[_one(s) for s in symbols])
        return {sym: klines for sym, klines in results}

    async def _gather_bars_and_klines(
        self, symbols: tuple[str, ...]
    ) -> tuple[dict[str, SymbolBars], dict[str, list[dict[str, Any]]]]:
        klines_map = await self._gather_klines(symbols)
        bars_map = {
            sym: self._bars_from_klines(sym, klines) for sym, klines in klines_map.items()
        }
        return bars_map, klines_map

    async def _gather_bars(self, symbols: tuple[str, ...]) -> dict[str, SymbolBars]:
        sem = asyncio.Semaphore(self._gather_concurrency)

        async def _one(sym: str) -> tuple[str, SymbolBars | None]:
            async with sem:
                try:
                    return sym, await self._symbol_bars(sym)
                except Exception as exc:
                    logger.warning("alpaca_bot.bars_failed symbol=%s error=%s", sym, exc)
                    return sym, None

        results = await asyncio.gather(*[_one(s) for s in symbols])
        return {sym: bars for sym, bars in results if bars is not None}
