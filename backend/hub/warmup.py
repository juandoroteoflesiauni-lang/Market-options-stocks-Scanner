"""Historical intraday cache warm-up via MarketDataHub. # [PD-3][TH]"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.hub.market_data_ttl_cache import intraday_cache_key, put_intraday_bars

logger = logging.getLogger(__name__)


class CacheWarmUp:
    """Pre-populate TTL intraday cache for active universe on boot."""

    @staticmethod
    async def warm(
        hub: Any,
        tickers: list[str],
        *,
        periods: int = 60,
        interval: str = "1min",
        concurrency: int = 3,
    ) -> dict[str, int]:
        """Fetch intraday candles per ticker; isolate failures."""
        if not tickers:
            return {"ok": 0, "failed": 0, "skipped": 0}

        sem = asyncio.Semaphore(max(concurrency, 1))
        ok = 0
        failed = 0

        async def _one(ticker: str) -> None:
            nonlocal ok, failed
            async with sem:
                try:
                    result = await hub.get_intraday_candles(ticker, limit=periods)
                    if result.is_failure:
                        failed += 1
                        logger.warning(
                            "cache_warmup.miss ticker=%s reason=%s", ticker, result.reason
                        )
                        return
                    candles = result.unwrap()
                    key = intraday_cache_key(
                        ticker,
                        interval,
                        max_bars=periods,
                        lookback_days=None,
                        accept_stale=False,
                    )
                    put_intraday_bars(
                        key,
                        {"candles": candles, "ticker": ticker.upper(), "interval": interval},
                    )
                    ok += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("cache_warmup.failed ticker=%s error=%s", ticker, exc)

        await asyncio.gather(*[_one(t) for t in tickers])
        logger.info("cache_warmup.complete ok=%d failed=%d total=%d", ok, failed, len(tickers))
        return {"ok": ok, "failed": failed, "skipped": 0}


__all__ = ["CacheWarmUp"]
