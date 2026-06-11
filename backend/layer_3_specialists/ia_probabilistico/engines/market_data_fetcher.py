"""
backend/layer_3_specialists/ia_probabilistico/engines/market_data_fetcher.py
════════════════════════════════════════════════════════════════════════════════
Market Data Fetcher — Real-time market data for Fear & Greed calculation.

Fetches required market data from FMP API (CNN Fear & Greed–style multi-factor index):
- VIX (volatility index)
- SPY (S&P 500 momentum vs MA125)
- Market breadth proxy (NYSE composite + SPY vs MA50, mapped to 0–100)
- Put–call ratio: FMP /v4/economic series when available; else VIX-regime proxy
- Credit proxy (JNK/TLT), gold, USD (safe haven)
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, cast

logger = logging.getLogger(__name__)

# FMP /v4/economic names to try for equity-style put/call (plan-dependent).
_PUT_CALL_ECONOMIC_CANDIDATES: list[str] = [
    "put-call-ratio",
    "putCallRatio",
    "CboeEquityPutCallRatio",
    "equityPutCallRatio",
    "PCUSEQTR",
]


def _breadth_pct_from_closes(prices: list[float]) -> float | None:
    """Map index level vs 50d MA to a 0–100 proxy for 'strength' (CNN-style highs factor)."""
    if len(prices) < 11:
        return None
    current = prices[0]
    ma50 = sum(prices[: min(50, len(prices))]) / min(50, len(prices))
    if ma50 <= 0:
        return None
    breadth_ratio = current / ma50
    nyse_highs_pct = (breadth_ratio - 0.85) / 0.30 * 100.0
    return max(0.0, min(100.0, nyse_highs_pct))


def _put_call_proxy_from_vix(vix_current: float, vix_ma50: float) -> float:
    """
    Synthetic put/call when CBOE series is unavailable.
    VIX above MA50 ~ more hedging ~ higher effective PCR in [~0.45, 1.5].
    """
    r = max(1e-9, float(vix_current)) / max(1e-9, float(vix_ma50))
    pcr = 0.75 + (r - 1.0) * 0.65
    return float(min(1.5, max(0.45, pcr)))


class MarketDataFetcher:
    """
    Fetches real-time market data for Fear & Greed calculation.
    Uses FMP client as primary data source.
    """

    VIX_SYMBOL = "^VIX"
    SPY_SYMBOL = "SPY"
    GOLD_SYMBOL = "GCY"
    USD_INDEX = "DXY"
    HIGH_YIELD_ETF = "JNK"
    TREASURY_ETF = "TLT"

    def __init__(self, fmp_client: Any) -> None:
        self.fmp = fmp_client
        self._cache: dict[str, tuple[Any, ...]] = {}
        self._cache_ttl = timedelta(minutes=5)

    async def fetch_fear_greed_data(self) -> dict[str, Any]:
        """
        Fetch all required market data for Fear & Greed calculation.

        Returns dict with spx_price, spx_ma125, vix_current, vix_ma50, nyse_highs_pct,
        put_call_ratio (and optional put_call_ratio_source), credit_spread, gold, usd.
        """
        result: dict[str, Any] = {}

        try:
            vix_data = await self._fetch_with_cache("vix", self.VIX_SYMBOL)
            if vix_data:
                result["vix_current"] = vix_data["price"]
                result["vix_ma50"] = vix_data["ma50"]

            spy_data = await self._fetch_with_cache("spy", self.SPY_SYMBOL)
            if spy_data:
                result["spx_price"] = spy_data["price"]
                result["spx_ma125"] = spy_data["ma125"]

            gold_data = await self._fetch_with_cache("gold", self.GOLD_SYMBOL)
            if gold_data:
                result["gold_price"] = gold_data["price"]
                result["gold_ma50"] = gold_data["ma50"]

            usd_data = await self._fetch_with_cache("usd", self.USD_INDEX)
            if usd_data:
                result["usd_index"] = usd_data["price"]
                result["usd_ma50"] = usd_data["ma50"]

            jnk_data = await self._fetch_with_cache("jnk", self.HIGH_YIELD_ETF)
            tlt_data = await self._fetch_with_cache("tlt", self.TREASURY_ETF)
            if jnk_data and tlt_data:
                spread_ratio = jnk_data["price"] / tlt_data["price"]
                result["credit_spread"] = spread_ratio * 1000.0

            breadth_data = await self._fetch_market_breadth()
            if breadth_data:
                result.update(breadth_data)

            pc_ratio, pc_src = await self._fetch_put_call_ratio()
            if pc_ratio is not None:
                result["put_call_ratio"] = pc_ratio
                result["put_call_ratio_source"] = pc_src

            if "put_call_ratio" not in result:
                vc = result.get("vix_current")
                vm = result.get("vix_ma50")
                if isinstance(vc, int | float) and isinstance(vm, int | float) and vm and vm > 0:
                    result["put_call_ratio"] = _put_call_proxy_from_vix(float(vc), float(vm))
                    result["put_call_ratio_source"] = "vix_proxy"

        except Exception as e:
            logger.error("Error fetching market data: %s", e)

        return result

    async def _fetch_with_cache(self, key: str, symbol: str) -> dict[str, float] | None:
        now = datetime.now()
        cache_key = f"{key}_{symbol}"

        if cache_key in self._cache:
            cached_data, cache_time = self._cache[cache_key]
            if now - cache_time < self._cache_ttl:
                return cast(dict[str, float] | None, cached_data)

        try:
            historical = await self.fmp.get_historical_prices(
                symbol,
                date_from=(datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d"),
                date_to=datetime.now().strftime("%Y-%m-%d"),
            )

            if not historical:
                return None

            prices = [p.close for p in historical if p.close is not None]
            if len(prices) < 2:
                return None

            current_price = prices[0]
            ma50 = sum(prices[: min(50, len(prices))]) / min(50, len(prices))
            ma125 = sum(prices[: min(125, len(prices))]) / min(125, len(prices))

            out = {
                "price": current_price,
                "ma50": ma50,
                "ma125": ma125,
            }
            self._cache[cache_key] = (out, now)
            return out

        except Exception as e:
            logger.warning("Failed to fetch %s: %s", symbol, e)
            return None

    async def _index_breadth_proxy(self, symbol: str) -> float | None:
        try:
            nyse_historical = await self.fmp.get_historical_prices(
                symbol,
                date_from=(datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d"),
                date_to=datetime.now().strftime("%Y-%m-%d"),
            )
            if not nyse_historical:
                return None
            prices = [p.close for p in nyse_historical if p.close is not None]
            return _breadth_pct_from_closes(prices)
        except Exception as e:
            logger.debug("breadth proxy %s: %s", symbol, e)
            return None

    async def _fetch_market_breadth(self) -> dict[str, float] | None:
        """
        Blend NYSE composite (NYA) and SPY vs 50d MA as CNN-style strength/breadth proxy.
        """
        nya = await self._index_breadth_proxy("NYA")
        spy_b = await self._index_breadth_proxy(self.SPY_SYMBOL)
        vals = [v for v in (nya, spy_b) if v is not None]
        if not vals:
            return None
        blended = sum(vals) / len(vals)
        return {"nyse_highs_pct": blended}

    async def _fetch_put_call_ratio(self) -> tuple[float | None, str]:
        """
        CBOE-style equity put/call from FMP macro when possible; otherwise caller may use VIX proxy.
        """
        fetcher = getattr(self.fmp, "fetch_latest_economic_value", None)
        if callable(fetcher):
            try:
                got = await fetcher(_PUT_CALL_ECONOMIC_CANDIDATES)
            except Exception as e:
                logger.debug("FMP economic put/call fetch failed: %s", e)
                got = None
            if got and len(got) >= 2:
                val, used_name = got[0], got[1]
                if isinstance(val, int | float) and 0.1 < float(val) < 5.0:
                    return (float(val), f"fmp_economic:{used_name}")

        return (None, "unavailable")

    def get_cached_data(self) -> dict[str, tuple[Any, ...]]:
        return self._cache.copy()

    def clear_cache(self) -> None:
        self._cache.clear()
        logger.info("Market data cache cleared")


async def fetch_market_data_for_fg(fmp_client: Any) -> dict[str, Any]:
    fetcher = MarketDataFetcher(fmp_client)
    return await fetcher.fetch_fear_greed_data()
