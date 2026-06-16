from __future__ import annotations
# ruff: noqa: F403, F405

from typing import TYPE_CHECKING

from backend.config.logger_setup import get_logger
from backend.domain.fmp_models import *

logger = get_logger(__name__)

if TYPE_CHECKING:
    pass


class FMPNewsMixin:
    """Mixin for FMP Client."""

    async def get_stock_news(
        self,
        symbol: str,
        limit: int = 10,
    ) -> list[FMPNewsItem]:
        """
        Fetch recent news articles for symbol.

        FMP stable endpoint: GET /stable/news/stock?symbols={symbol}
        Falls back to legacy GET /stock_news?tickers={symbol}.

        Parameters
        ----------
        symbol : Ticker filter (e.g. 'AAPL')
        limit  : Maximum number of articles to return
        """
        data = await self._get_stable(
            "/news/stock",
            module="NEWS",
            params={"symbols": symbol.upper(), "limit": limit, "page": 0},
            ttl_secs=60.0,
        )
        if not isinstance(data, list) or not data:
            data = await self._get(
                "/stock_news",
                module="NEWS",
                params={"tickers": symbol.upper(), "limit": limit},
                ttl_secs=60.0,
            )
        return self._parse_list(data, FMPNewsItem)

    async def get_latest_stock_news(self, limit: int = 20) -> list[FMPNewsItem]:
        """Fetch latest broad stock-market headlines. GET /stable/news/stock-latest."""
        data = await self._get_stable(
            "/news/stock-latest",
            module="NEWS",
            params={"page": 0, "limit": limit},
            ttl_secs=60.0,
        )
        return self._parse_list(data, FMPNewsItem)

    async def get_press_releases(
        self,
        symbol: str,
        limit: int = 5,
    ) -> list[FMPPressRelease]:
        """
        Fetch SEC press releases for symbol.

        FMP endpoint: GET /press-releases/{symbol}
        https://financialmodelingprep.com/api/v3/press-releases/{symbol}
        """
        data = await self._get(
            f"/press-releases/{symbol.upper()}",
            module="NEWS",
            params={"limit": limit},
            ttl_secs=3600.0,
        )
        return self._parse_list(data, FMPPressRelease)

    async def get_social_sentiment(self, symbol: str, limit: int = 100) -> list[FMPSocialSentiment]:
        """Fetch social sentiment (Twitter/Stocktwits). GET /v4/social-sentiment."""
        data = await self._get(
            "/v4/social-sentiment",
            module="ANALYST",
            params={"symbol": symbol.upper(), "limit": limit},
            ttl_secs=3600.0,  # 1h cache
        )
        return self._parse_list(data, FMPSocialSentiment)
