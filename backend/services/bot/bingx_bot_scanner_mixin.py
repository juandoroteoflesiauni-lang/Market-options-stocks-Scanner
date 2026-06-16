from __future__ import annotations

from backend.services.bot.bingx_bot_types import *

"""Mixin class for BingX Bot Scanner."""

import asyncio

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    pass


class BingXBotScannerMixin:
    pass

    async def _snapshot_symbols(self, symbols: tuple[str, ...]) -> list[BingXMarketSnapshot]:
        from backend.services.bingx_bot_service import _empty_snapshot

        tasks = [self._snapshot_symbol(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[BingXMarketSnapshot] = []
        for sym, result in zip(symbols, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("bingx_bot.snapshot_failed symbol=%s error=%s", sym, result)
                out.append(_empty_snapshot(sym, self._scan_interval))
                continue
            out.append(result)
        return out

    async def _snapshot_symbol(self, symbol: str) -> BingXMarketSnapshot:
        from backend.services.bingx_bot_service import _features_from_klines

        klines = await self._fetch_klines_prefer_perp(
            symbol,
            interval=self._scan_interval,
            limit=self._klines_per_symbol,
        )
        return _features_from_klines(symbol, self._scan_interval, klines)

    async def _fetch_klines_prefer_perp(
        self,
        symbol: str,
        *,
        interval: VALID_KLINE_INTERVAL,
        limit: int,
    ) -> list[BingXKline]:
        try:
            return await self._client.fetch_klines_perp(symbol, interval=interval, limit=limit)
        except Exception as exc:
            logger.debug("bingx_bot.klines_perp_fallback symbol=%s error=%s", symbol, exc)
            if is_perp_symbol(symbol):
                raise
            return await self._client.fetch_klines(symbol, interval=interval, limit=limit)

    def _snapshot_to_signal(
        self,
        snap: BingXMarketSnapshot,
        lob_analysis: LOBDynamicsAnalysis | None = None,
    ) -> BingXSignal:
        reason_codes: list[str] = []
        direction: Literal[LONG, SHORT, FLAT] = "FLAT"
        score = 0.0

        # Legacy VSA volume check bypassed to let the canonical decision engine prevail
        pos = snap.close_position_in_range or 0.5
        if pos >= 0.6:
            direction = "LONG"
        elif pos <= 0.4:
            direction = "SHORT"
        score = round(
            min(1.0, (snap.volume_z_score or 0.0) / max(self._volume_z_threshold, 1e-9)), 4
        )

        from backend.services.bingx_bot_service import _utc_iso_now

        return BingXSignal(
            symbol=snap.symbol,
            direction=direction,
            score=score,
            horizon=self._horizon,
            reason_codes=tuple(reason_codes),
            snapshot=snap,
            timestamp=_utc_iso_now(),
            lob_analysis=lob_analysis,
        )
