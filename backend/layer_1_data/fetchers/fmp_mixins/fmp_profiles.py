# ruff: noqa: F403, F405
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.config.logger_setup import get_logger
from backend.domain.fmp_models import *

logger = get_logger(__name__)

if TYPE_CHECKING:
    pass


class FMPProfilesMixin:
    """Mixin for FMP Client."""

    async def get_mutual_fund_holders(
        self,
        symbol: str,
    ) -> list[FMPMutualFundHolder]:
        """
        Fetch mutual fund holders for symbol.

        FMP endpoint: GET /mutual-fund-holder/{symbol}
        https://financialmodelingprep.com/api/v3/mutual-fund-holder/{symbol}
        """
        data = await self._get(
            f"/mutual-fund-holder/{symbol.upper()}", module="13F", ttl_secs=259200.0
        )
        return self._parse_list(data, FMPMutualFundHolder)

    async def get_profile(self, symbol: str) -> FMPProfile | None:
        """Fetch company profile. FMP endpoint: GET /v3/profile/{symbol}."""
        data = await self._get(f"/profile/{symbol.upper()}", module="PROFILES", ttl_secs=86400.0)
        if isinstance(data, list) and data:
            try:
                return FMPProfile(**data[0])
            except Exception as exc:
                logger.debug("FMP profile parse error: %s", exc)
        return None

    async def get_analyst_estimates(self, symbol: str, limit: int = 6) -> list[FMPAnalystEstimate]:
        """Fetch analyst consensus estimates. GET /v3/analyst-estimates/{symbol}."""
        data = await self._get(
            f"/analyst-estimates/{symbol.upper()}",
            module="ANALYST",
            params={"limit": limit, "period": "annual"},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPAnalystEstimate)

    async def get_stock_recommendations(self, symbol: str) -> FMPStockRecommendation | None:
        """Fetch analyst buy/hold/sell breakdown. GET /v3/analyst-stock-recommendations/{symbol}."""
        data = await self._get(
            f"/analyst-stock-recommendations/{symbol.upper()}",
            module="ANALYST",
            params={"limit": 1},
            ttl_secs=86400.0,
        )
        if isinstance(data, list) and data:
            try:
                return FMPStockRecommendation(**data[0])
            except Exception as exc:
                logger.debug("FMP stock recommendation parse error: %s", exc)
        return None

    async def get_price_target_consensus(self, symbol: str) -> FMPPriceTargetConsensus | None:
        """Fetch price target consensus. GET /v4/price-target-consensus?symbol={symbol}."""
        data = await self._get(
            "/v4/price-target-consensus",
            module="ANALYST",
            params={"symbol": symbol.upper()},
            ttl_secs=86400.0,
        )
        if isinstance(data, list) and data:
            try:
                return FMPPriceTargetConsensus(**data[0])
            except Exception as exc:
                logger.debug("FMP price target parse error: %s", exc)
        return None

    async def get_transcript_list(self, symbol: str) -> list[FMPTranscriptListItem]:
        """List available earnings call transcripts. GET /v4/earning_call_transcript?symbol={symbol}."""
        data = await self._get(
            "/v4/earning_call_transcript",
            module="TRANSCRIPTS",
            params={"symbol": symbol.upper()},
            ttl_secs=86400.0,
        )
        # Note: This endpoint returns [ [quarter, year, date], ... ]
        if isinstance(data, list) and data:
            results = []
            for item in data:
                if isinstance(item, list) and len(item) >= 3:
                    try:
                        results.append(
                            FMPTranscriptListItem(
                                symbol=symbol.upper(),
                                quarter=int(item[0]),
                                year=int(item[1]),
                                date=str(item[2]),
                            )
                        )
                    except (ValueError, TypeError) as exc:
                        logger.debug("FMP transcript list item parse error: %s", exc)
            return results
        return []

    async def get_transcript(self, symbol: str, year: int, quarter: int) -> FMPTranscript | None:
        """Fetch specific earnings call transcript. GET /v3/earning_call_transcript/{symbol}."""
        data = await self._get(
            f"/earning_call_transcript/{symbol.upper()}",
            module="TRANSCRIPTS",
            params={"year": year, "quarter": quarter},
            ttl_secs=86400.0,
        )
        if isinstance(data, list) and data:
            try:
                return FMPTranscript(**data[0])
            except Exception as exc:
                logger.debug("FMP transcript parse error: %s", exc)
        return None

    async def get_insider_trades(self, symbol: str, limit: int = 100) -> list[FMPInsiderTrade]:
        """Fetch insider trading data. GET /v4/insider-trading."""
        data = await self._get(
            "/v4/insider-trading",
            module="MARKET",
            params={"symbol": symbol.upper(), "limit": limit},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPInsiderTrade)

    async def get_esg_data(self, symbol: str) -> list[FMPESGData]:
        """Fetch ESG environmental, social and governance data. GET /v4/esg-environmental-social-governance-data."""
        data = await self._get(
            "/v4/esg-environmental-social-governance-data",
            module="PROFILES",
            params={"symbol": symbol.upper()},
            ttl_secs=604800.0,  # 1 week cache
        )
        return self._parse_list(data, FMPESGData)

    async def get_etf_exposure(self, symbol: str) -> list[FMPETFExposure]:
        """Fetch ETF exposure (which ETFs hold a specific stock). GET /v4/etf-stock-exposure."""
        data = await self._get(
            "/v4/etf-stock-exposure",
            module="ETF",
            params={"symbol": symbol.upper()},
            ttl_secs=604800.0,  # 1 week cache
        )
        return self._parse_list(data, FMPETFExposure)

    async def get_price_target_history(self, symbol: str) -> list[FMPPriceTargetDetail]:
        """Fetch historical price target updates. GET /v4/price-target."""
        data = await self._get(
            "/v4/price-target",
            module="ANALYST",
            params={"symbol": symbol.upper()},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPPriceTargetDetail)

    async def get_institutional_ownership_history(
        self, symbol: str
    ) -> list[FMPInstitutionalOwnershipPercent]:
        """
        Institutional ownership % — try v4 percent series, v4 symbol-ownership, then stable positions summary.
        """
        from datetime import datetime

        sym = symbol.upper()
        data = await self._get(
            "/v4/institutional-ownership/symbol-ownership-percent",
            module="13F",
            params={"symbol": sym},
            ttl_secs=604800.0,
        )
        parsed = self._parse_list(data, FMPInstitutionalOwnershipPercent)
        if parsed:
            return parsed

        own_series = await self._get(
            "/v4/institutional-ownership/symbol-ownership",
            module="13F",
            params={"symbol": sym, "includeCurrentQuarter": "true"},
            ttl_secs=604800.0,
        )
        if isinstance(own_series, list) and own_series:
            out: list[FMPInstitutionalOwnershipPercent] = []
            for item in own_series:
                if not isinstance(item, dict):
                    continue
                pct = (
                    item.get("institutionalOwnershipPercentage")
                    or item.get("ownershipPercent")
                    or item.get("institutionalOwnership")
                )
                if pct is None:
                    continue
                d_raw = item.get("date") or item.get("filingDate")
                try:
                    out.append(
                        FMPInstitutionalOwnershipPercent(
                            date=str(d_raw) if d_raw else None,
                            symbol=str(item.get("symbol") or sym),
                            institutionalOwnershipPercentage=float(pct),
                        )
                    )
                except (TypeError, ValueError):
                    continue
            if out:
                return out

        y = datetime.now().year
        cq = (datetime.now().month - 1) // 3 + 1
        yy, qq = y, cq
        for _ in range(8):
            qq -= 1
            if qq < 1:
                yy -= 1
                qq = 4
            pdata = await self._get_stable(
                "/institutional-ownership/symbol-positions-summary",
                module="13F",
                params={"symbol": sym, "year": yy, "quarter": qq},
                ttl_secs=86400.0,
            )
            row: dict[str, Any] | None = None
            if isinstance(pdata, list) and pdata and isinstance(pdata[0], dict):
                row = pdata[0]
            elif isinstance(pdata, dict):
                row = pdata
            if not row:
                continue
            pct = row.get("ownershipPercent")
            if pct is None:
                continue
            try:
                return [
                    FMPInstitutionalOwnershipPercent(
                        date=f"{yy}-Q{qq}",
                        symbol=sym,
                        institutionalOwnershipPercentage=float(pct),
                    )
                ]
            except (TypeError, ValueError):
                continue
        return []

    async def get_institutional_holders(self, symbol: str) -> list[FMPInstitutionalHolderDetail]:
        """Fetch top institutional holders. GET /v3/institutional-holder/{symbol}."""
        data = await self._get(
            f"/institutional-holder/{symbol.upper()}",
            module="13F",
            ttl_secs=604800.0,  # 1 week cache
        )
        return self._parse_list(data, FMPInstitutionalHolderDetail)

    async def get_stock_peers(self, symbol: str) -> list[FMPStockPeer]:
        """Fetch stock peers. GET /v4/stock_peers?symbol={symbol}."""
        data = await self._get(
            "/v4/stock_peers",
            module="PROFILES",
            params={"symbol": symbol.upper()},
            ttl_secs=604800.0,
        )
        return self._parse_list(data, FMPStockPeer)
