# ruff: noqa: F403, F405
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from backend.config.logger_setup import get_logger
from backend.domain.fmp_models import *

logger = get_logger(__name__)

if TYPE_CHECKING:
    pass


class FMPStatementsMixin:
    """Mixin for FMP Client."""

    async def get_income_statements(
        self,
        symbol: str,
        limit: int = 4,
        period: str = "annual",
    ) -> list[FMPIncomeStatement]:
        """
        Fetch income statements for symbol.

        FMP endpoint: GET /income-statement/{symbol}
        https://financialmodelingprep.com/api/v3/income-statement/{symbol}

        Parameters
        ----------
        symbol : Ticker (e.g. 'AAPL')
        limit  : Number of periods to return (default 4 = last 4 quarters/years)
        period : 'annual' | 'quarter'
        """
        data = await self._get(
            f"/income-statement/{symbol.upper()}",
            module="STATEMENTS",
            params={"limit": limit, "period": period},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPIncomeStatement)

    async def get_balance_sheets(
        self,
        symbol: str,
        limit: int = 4,
        period: str = "annual",
    ) -> list[FMPBalanceSheet]:
        """
        Fetch balance sheet statements for symbol.

        FMP endpoint: GET /balance-sheet-statement/{symbol}
        https://financialmodelingprep.com/api/v3/balance-sheet-statement/{symbol}
        """
        data = await self._get(
            f"/balance-sheet-statement/{symbol.upper()}",
            module="STATEMENTS",
            params={"limit": limit, "period": period},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPBalanceSheet)

    async def get_cash_flow_statements(
        self,
        symbol: str,
        limit: int = 4,
        period: str = "annual",
    ) -> list[FMPCashFlowStatement]:
        """
        Fetch cash flow statements for symbol.

        FMP endpoint: GET /cash-flow-statement/{symbol}
        https://financialmodelingprep.com/api/v3/cash-flow-statement/{symbol}
        """
        data = await self._get(
            f"/cash-flow-statement/{symbol.upper()}",
            module="STATEMENTS",
            params={"limit": limit, "period": period},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPCashFlowStatement)

    async def get_income_statement_growth(
        self,
        symbol: str,
        limit: int = 4,
    ) -> list[FMPIncomeStatementGrowth]:
        """
        Fetch income statement growth metrics for symbol.

        FMP endpoint: GET /income-statement-growth/{symbol}
        https://financialmodelingprep.com/api/v3/income-statement-growth/{symbol}
        """
        data = await self._get(
            f"/income-statement-growth/{symbol.upper()}",
            module="STATEMENTS",
            params={"limit": limit},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPIncomeStatementGrowth)

    async def get_financial_growth(
        self,
        symbol: str,
        limit: int = 4,
    ) -> list[FMPFinancialGrowth]:
        """
        Fetch multi-metric financial growth for symbol.

        FMP endpoint: GET /financial-growth/{symbol}
        https://financialmodelingprep.com/api/v3/financial-growth/{symbol}
        """
        data = await self._get(
            f"/financial-growth/{symbol.upper()}",
            module="STATEMENTS",
            params={"limit": limit},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPFinancialGrowth)

    async def get_key_metrics(
        self,
        symbol: str,
        limit: int = 4,
        period: str = "annual",
    ) -> list[FMPKeyMetrics]:
        """
        Fetch per-share and valuation key metrics for symbol.

        FMP endpoint: GET /key-metrics/{symbol}
        https://financialmodelingprep.com/api/v3/key-metrics/{symbol}
        """
        data = await self._get(
            f"/key-metrics/{symbol.upper()}",
            module="STATEMENTS",
            params={"limit": limit, "period": period},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPKeyMetrics)

    async def get_key_metrics_ttm(
        self,
        symbol: str,
    ) -> FMPKeyMetricsTTM | None:
        """
        Fetch trailing-12-month key metrics for symbol.

        FMP endpoint: GET /key-metrics-ttm/{symbol}
        https://financialmodelingprep.com/api/v3/key-metrics-ttm/{symbol}

        Returns a single FMPKeyMetricsTTM or None.
        """
        data = await self._get(
            f"/key-metrics-ttm/{symbol.upper()}", module="STATEMENTS", ttl_secs=86400.0
        )
        if isinstance(data, list) and data:
            try:
                return FMPKeyMetricsTTM(**data[0])
            except Exception as exc:
                logger.debug("FMP key-metrics-ttm parse error: %s", exc)
        elif isinstance(data, dict) and data:
            try:
                return FMPKeyMetricsTTM(**data)
            except Exception as exc:
                logger.debug("FMP key-metrics-ttm parse error: %s", exc)
        return None

    async def get_enterprise_values(
        self,
        symbol: str,
        limit: int = 4,
    ) -> list[FMPEnterpriseValue]:
        """
        Fetch enterprise value decomposition for symbol.

        FMP endpoint: GET /enterprise-values/{symbol}
        https://financialmodelingprep.com/api/v3/enterprise-values/{symbol}
        """
        data = await self._get(
            f"/enterprise-values/{symbol.upper()}",
            module="STATEMENTS",
            params={"limit": limit},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPEnterpriseValue)

    async def get_dcf_valuation(
        self,
        symbol: str,
    ) -> FMPDCFValuation | None:
        """
        Fetch DCF intrinsic value vs current stock price for symbol.

        FMP endpoint: GET /discounted-cash-flow/{symbol}
        https://financialmodelingprep.com/api/v3/discounted-cash-flow/{symbol}

        Returns FMPDCFValuation or None.
        Note: The 'Stock Price' key (with space) is mapped to stock_price field.
        """
        data = await self._get(
            f"/discounted-cash-flow/{symbol.upper()}", module="STATEMENTS", ttl_secs=86400.0
        )
        if isinstance(data, list) and data:
            item = data[0]
        elif isinstance(data, dict):
            item = data
        else:
            return None

        if not item:
            return None

        try:
            # Map "Stock Price" (with space) → stock_price
            mapped = {
                "symbol": item.get("symbol"),
                "date": item.get("date"),
                "dcf": item.get("dcf"),
                "stock_price": item.get("Stock Price"),
            }
            return FMPDCFValuation(**mapped)
        except Exception as exc:
            logger.debug("FMP DCF parse error: %s", exc)
            return None

    async def get_rating(
        self,
        symbol: str,
    ) -> FMPRating | None:
        """
        Fetch analyst composite rating for symbol.

        FMP endpoint: GET /rating/{symbol}
        https://financialmodelingprep.com/api/v3/rating/{symbol}

        Returns FMPRating or None.
        """
        data = await self._get(f"/rating/{symbol.upper()}", module="ANALYST", ttl_secs=86400.0)
        if isinstance(data, list) and data:
            try:
                return FMPRating(**data[0])
            except Exception as exc:
                logger.debug("FMP rating parse error: %s", exc)
        return None

    async def get_ratios_ttm(self, symbol: str) -> FMPRatiosTTM | None:
        """Fetch TTM ratios. FMP endpoint: GET /v3/ratios-ttm/{symbol}."""
        data = await self._get(
            f"/ratios-ttm/{symbol.upper()}", module="STATEMENTS", ttl_secs=3600.0
        )
        if isinstance(data, list) and data:
            try:
                return FMPRatiosTTM(**data[0])
            except Exception as exc:
                logger.debug("FMP ratios-ttm parse error: %s", exc)
        return None

    async def get_ratios_annual(self, symbol: str, limit: int = 20) -> list[FMPRatiosAnnual]:
        """Fetch annual ratios for historical charts. GET /v3/ratios/{symbol}."""
        data = await self._get(
            f"/ratios/{symbol.upper()}",
            module="STATEMENTS",
            params={"limit": limit, "period": "annual"},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPRatiosAnnual)

    async def get_ratios_quarterly(self, symbol: str, limit: int = 40) -> list[FMPRatiosAnnual]:
        """Fetch quarterly ratios for high-resolution historical charts. GET /v3/ratios/{symbol}."""
        data = await self._get(
            f"/ratios/{symbol.upper()}",
            module="STATEMENTS",
            params={"limit": limit, "period": "quarter"},
            ttl_secs=86400.0,
        )
        return self._parse_list(data, FMPRatiosAnnual)

    async def get_fundamentals_enrichment(
        self,
        symbol: str,
    ) -> dict[str, Any]:
        """
        Fetch a composite fundamentals enrichment dict for symbol.

        This is the primary DataLake integration point. Returns a flat dict
        that the DataLake.get_fundamentals() result can be updated with.
        Now fetches all 12 modules concurrently.
        """
        result: dict[str, Any] = {}

        async def fetch_dcf():
            try:
                dcf = await self.get_dcf_valuation(symbol)
                if dcf:
                    result["fmp_dcf"] = dcf.dcf
                    result["fmp_stock_price"] = dcf.stock_price
                    if dcf.dcf and dcf.stock_price and dcf.stock_price > 0:
                        mos = (dcf.dcf - dcf.stock_price) / dcf.stock_price
                        result["fmp_dcf_margin_of_safety"] = round(mos, 4)
            except Exception as exc:
                logger.debug("FMP enrichment: DCF failed for %s: %s", symbol, exc)

        async def fetch_rating():
            try:
                rating = await self.get_rating(symbol)
                if rating:
                    result["fmp_rating"] = rating.rating
                    result["fmp_rating_score"] = rating.ratingScore
                    result["fmp_rating_recommendation"] = rating.ratingRecommendation
            except Exception as exc:
                logger.debug("FMP enrichment: rating failed for %s: %s", symbol, exc)

        async def fetch_ttm():
            try:
                ttm = await self.get_key_metrics_ttm(symbol)
                if ttm:
                    result["fmp_pe_ratio_ttm"] = ttm.peRatioTTM
                    result["fmp_ev_ebitda_ttm"] = ttm.enterpriseValueOverEBITDATTM
                    result["fmp_roic_ttm"] = ttm.roicTTM
                    result["fmp_debt_to_equity_ttm"] = ttm.debtToEquityTTM
                    result["fmp_dividend_yield_ttm"] = ttm.dividendYieldPercentageTTM
            except Exception as exc:
                logger.debug("FMP enrichment: TTM metrics failed for %s: %s", symbol, exc)

        async def fetch_growth():
            try:
                growth_list = await self.get_income_statement_growth(symbol, limit=1)
                if growth_list:
                    g = growth_list[0]
                    result["fmp_revenue_growth"] = g.growthRevenue
                    result["fmp_net_income_growth"] = g.growthNetIncome
                    result["fmp_eps_growth"] = g.growthEPS
            except Exception as exc:
                logger.debug("FMP enrichment: growth failed for %s: %s", symbol, exc)

        async def fetch_cf():
            try:
                cf_list = await self.get_cash_flow_statements(symbol, limit=1)
                if cf_list:
                    cf = cf_list[0]
                    result["fmp_free_cash_flow"] = cf.freeCashFlow
                    result["fmp_operating_cash_flow"] = cf.operatingCashFlow
            except Exception as exc:
                logger.debug("FMP enrichment: cash flow failed for %s: %s", symbol, exc)

        async def fetch_bs():
            try:
                bs_list = await self.get_balance_sheets(symbol, limit=1)
                if bs_list:
                    bs = bs_list[0]
                    result["fmp_total_debt"] = bs.totalDebt
                    result["fmp_cash"] = bs.cashAndCashEquivalents
            except Exception as exc:
                logger.debug("FMP enrichment: balance sheet failed for %s: %s", symbol, exc)

        async def fetch_quote():
            try:
                quote = await self.get_quote(symbol)
                if quote:
                    result["fmp_quote_price"] = quote.price
                    result["fmp_quote_change_pct"] = quote.changesPercentage
            except Exception as exc:
                logger.debug("FMP enrichment: quote failed for %s: %s", symbol, exc)

        async def fetch_news():
            try:
                news = await self.get_stock_news(symbol, limit=5)
                if news:
                    result["fmp_news"] = [
                        {"title": n.title, "url": n.url, "publishedDate": n.publishedDate}
                        for n in news
                    ]
            except Exception as exc:
                logger.debug("FMP enrichment: news failed for %s: %s", symbol, exc)

        async def fetch_institutional():
            try:
                holders = await self.get_institutional_holders(symbol)
                if holders:
                    result["fmp_institutional_holders"] = sum(h.shares for h in holders if h.shares)
            except Exception as exc:
                logger.debug("FMP enrichment: institutional failed for %s: %s", symbol, exc)

        async def fetch_technicals():
            try:
                rsi = await self.get_rsi(symbol, period=14, limit=1)
                if rsi:
                    result["fmp_rsi_14"] = rsi[0].rsi
                ema = await self.get_ema(symbol, period=20, limit=1)
                if ema:
                    result["fmp_ema_20"] = ema[0].ema
            except Exception as exc:
                logger.debug("FMP enrichment: technicals failed for %s: %s", symbol, exc)

        async def fetch_short_interest():
            try:
                si = await self.get_short_interest(symbol)
                if si:
                    result["fmp_short_interest"] = si[0].shortInterest
            except Exception as exc:
                logger.debug("FMP enrichment: short interest failed for %s: %s", symbol, exc)

        await asyncio.gather(
            fetch_dcf(),
            fetch_rating(),
            fetch_ttm(),
            fetch_growth(),
            fetch_cf(),
            fetch_bs(),
            fetch_quote(),
            fetch_news(),
            fetch_institutional(),
            fetch_technicals(),
            fetch_short_interest(),
        )

        return result

    async def get_full_fundamental_analysis(self, symbol: str) -> dict[str, Any]:
        """
        Fires parallel requests across all 13 FMP modules and returns a massive
        consolidated object as specified in the technical report section 8.3.
        """
        from datetime import datetime, timedelta

        sym = symbol.upper()
        today = datetime.now().strftime("%Y-%m-%d")
        five_years_ago = (datetime.now() - timedelta(days=5 * 365)).strftime("%Y-%m-%d")
        ninety_days = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")

        # Fire all requests in parallel
        (
            profile,
            quote,
            ratios_ttm,
            key_metrics_ttm,
            ratios_annual,
            key_metrics_annual,
            income_annual,
            income_quarterly,
            balance_annual,
            cashflow_annual,
            financial_growth,
            dcf,
            earnings_surprises,
            historical_earnings,
            analyst_estimates,
            recommendations,
            price_target,
            historical_dividends,
            dividend_calendar,
            rsi_data,
            sma50_data,
            sma200_data,
            historical_prices_5y,
            transcript_list,
            earnings_calendar,
        ) = await asyncio.gather(
            self.get_profile(sym),
            self.get_quote(sym),
            self.get_ratios_ttm(sym),
            self.get_key_metrics_ttm(sym),
            self.get_ratios_annual(sym, limit=20),
            self.get_key_metrics(sym, limit=11, period="annual"),
            self.get_income_statements(sym, limit=15, period="annual"),
            self.get_income_statements(sym, limit=40, period="quarter"),
            self.get_balance_sheets(sym, limit=10, period="annual"),
            self.get_cash_flow_statements(sym, limit=10, period="annual"),
            self.get_financial_growth(sym, limit=10),
            self.get_dcf_valuation(sym),
            self.get_earnings_surprises(sym),
            self.get_historical_earnings(sym, limit=20),
            self.get_analyst_estimates(sym, limit=6),
            self.get_stock_recommendations(sym),
            self.get_price_target_consensus(sym),
            self.get_historical_dividends(sym),
            self.get_dividend_calendar(today, ninety_days),
            self.get_rsi(sym, period=14, limit=1),
            self.get_sma(sym, period=50, limit=1),
            self.get_sma(sym, period=200, limit=1),
            self.get_historical_prices(sym, date_from=five_years_ago, date_to=today),
            self.get_transcript_list(sym),
            self.get_earnings_calendar(today, ninety_days),
        )

        result: dict[str, Any] = {"symbol": sym, "ticker": sym, "timestamp": today}

        # ── Profile ──
        if profile:
            result["profile"] = profile.model_dump()

        # ── Quote (fallback to profile price) ──
        if quote:
            result["quote"] = {
                "price": quote.price,
                "change": quote.change,
                "changePercent": quote.changesPercentage,
                "marketCap": quote.marketCap,
                "volume": quote.volume,
                "yearHigh": quote.yearHigh,
                "yearLow": quote.yearLow,
                "avgVolume": quote.avgVolume,
            }
        elif profile and profile.price:
            result["quote"] = {"price": profile.price, "change": None, "changePercent": None}

        # ── Valuation ──
        val_ttm: dict[str, Any] = {}
        if ratios_ttm:
            val_ttm = {
                "peRatio": ratios_ttm.peRatioTTM,
                "pegRatio": ratios_ttm.pegRatioTTM,
                "pbRatio": ratios_ttm.priceToBookRatioTTM,
                "psRatio": ratios_ttm.priceToSalesRatioTTM,
                "evEbitda": ratios_ttm.enterpriseValueMultipleTTM,
                "evSales": ratios_ttm.evToSalesTTM,
                "evFcf": ratios_ttm.evToFreeCashFlowTTM,
                "pFcf": ratios_ttm.priceToFreeCashFlowsRatioTTM,
            }
        if key_metrics_ttm:
            if val_ttm.get("evSales") is None and key_metrics_ttm.evToSalesTTM is not None:
                val_ttm["evSales"] = key_metrics_ttm.evToSalesTTM
            if val_ttm.get("evFcf") is None and key_metrics_ttm.evToFreeCashFlowTTM is not None:
                val_ttm["evFcf"] = key_metrics_ttm.evToFreeCashFlowTTM
        if dcf:
            val_ttm["dcf"] = dcf.dcf
            val_ttm["dcfStockPrice"] = dcf.stock_price
            if dcf.dcf and dcf.stock_price and dcf.stock_price > 0:
                val_ttm["marginOfSafety"] = round((dcf.dcf - dcf.stock_price) / dcf.stock_price, 4)
        val_hist = [
            {
                "year": r.calendarYear,
                # Using prioritized fields from our aliased model
                "pe": r.priceEarningsRatio,
                "pb": r.priceToBookRatio,
                "ps": r.priceToSalesRatio,
                "evEbitda": r.enterpriseValueMultiple,
            }
            for r in ratios_annual
        ]
        result["valuation"] = {"ttm": val_ttm, "historico": val_hist}

        # ── Profitability ──
        prof_ttm: dict[str, Any] = {}
        if ratios_ttm:
            prof_ttm = {
                "roe": ratios_ttm.returnOnEquityTTM,
                "roa": ratios_ttm.returnOnAssetsTTM,
                "roce": ratios_ttm.returnOnCapitalEmployedTTM,
                "grossMargin": ratios_ttm.grossProfitMarginTTM,
                "operatingMargin": ratios_ttm.operatingProfitMarginTTM,
                "netMargin": ratios_ttm.netProfitMarginTTM,
            }
        if key_metrics_ttm:
            prof_ttm["roic"] = key_metrics_ttm.roicTTM
        prof_hist = [
            {
                "year": r.calendarYear,
                "roe": r.returnOnEquity,
                "roa": r.returnOnAssets,
                "roce": r.returnOnCapitalEmployed,
                "grossMargin": r.grossProfitMargin,
                "operatingMargin": r.operatingProfitMargin,
                "netMargin": r.netProfitMargin,
            }
            for r in ratios_annual
        ]
        result["profitability"] = {"ttm": prof_ttm, "historico": prof_hist}

        # ── Debt ──
        # Initialize debt_ttm with latest balance sheet as primary source for absolute values
        if balance_annual:
            latest = balance_annual[0]
            debt_ttm = {
                "totalDebt": latest.totalDebt,
                "netDebt": (
                    latest.netDebt
                    if latest.netDebt is not None
                    else ((latest.totalDebt or 0) - (latest.cashAndCashEquivalents or 0))
                ),
                "cash": latest.cashAndCashEquivalents,
                "totalAssets": latest.totalAssets,
                "totalEquity": latest.totalStockholdersEquity,
                "workingCapital": (
                    latest.totalCurrentAssets - latest.totalCurrentLiabilities
                    if latest.totalCurrentAssets and latest.totalCurrentLiabilities
                    else None
                ),
            }

        if ratios_ttm:
            debt_ttm.update(
                {
                    "debtEquity": ratios_ttm.debtEquityRatioTTM,
                    "currentRatio": ratios_ttm.currentRatioTTM,
                    "quickRatio": ratios_ttm.quickRatioTTM,
                    "interestCoverage": ratios_ttm.interestCoverageTTM,
                }
            )
        if key_metrics_ttm:
            debt_ttm["netDebtToEbitda"] = key_metrics_ttm.netDebtToEBITDATTM
            debt_ttm["cashPerShare"] = key_metrics_ttm.cashPerShareTTM
        debt_hist = []
        for bs in balance_annual:
            entry: dict[str, Any] = {"year": bs.calendarYear}
            entry["totalDebt"] = bs.totalDebt
            entry["netDebt"] = bs.netDebt
            entry["cash"] = bs.cashAndCashEquivalents
            entry["totalEquity"] = bs.totalStockholdersEquity
            entry["totalAssets"] = bs.totalAssets
            if bs.totalCurrentAssets and bs.totalCurrentLiabilities:
                entry["workingCapital"] = bs.totalCurrentAssets - bs.totalCurrentLiabilities

            # Match income statement for EBITDA and Interest Coverage calculation
            matching_inc = next(
                (i for i in income_annual if i.calendarYear == bs.calendarYear), None
            )
            if matching_inc:
                entry["ebitda"] = matching_inc.ebitda
                if matching_inc.ebitda and bs.netDebt is not None:
                    entry["netDebtToEbitda"] = (
                        bs.netDebt / matching_inc.ebitda if matching_inc.ebitda > 0 else None
                    )
                entry["operatingIncome"] = matching_inc.operatingIncome

            entry["longTermDebt"] = bs.longTermDebt
            entry["shortTermDebt"] = bs.shortTermDebt

            debt_hist.append(entry)

        result["debt"] = {"ttm": debt_ttm, "historico": debt_hist}

        # ── Growth ──
        growth_yoy: dict[str, Any] = {}
        if financial_growth:
            fg = financial_growth[0]
            growth_yoy = {
                "revenueGrowth": fg.revenueGrowth,
                "netIncomeGrowth": fg.netIncomeGrowth,
                "epsGrowth": fg.epsgrowth,
                "fcfGrowth": fg.freeCashFlowGrowth,
            }
        # CAGR calculations
        growth_cagr: dict[str, Any] = {}
        if len(income_annual) >= 6:
            curr_eps = income_annual[0].epsDiluted or income_annual[0].eps
            past_eps = income_annual[4].epsDiluted or income_annual[4].eps
            if curr_eps and past_eps and past_eps > 0 and curr_eps > 0:
                growth_cagr["epsCagr5y"] = round((curr_eps / past_eps) ** 0.2 - 1, 4)
            curr_rev = income_annual[0].revenue
            past_rev = income_annual[4].revenue
            if curr_rev and past_rev and past_rev > 0:
                growth_cagr["revenueCagr5y"] = round((curr_rev / past_rev) ** 0.2 - 1, 4)
        if len(income_annual) >= 11:
            curr_eps = income_annual[0].epsDiluted or income_annual[0].eps
            past_eps = income_annual[9].epsDiluted or income_annual[9].eps
            if curr_eps and past_eps and past_eps > 0 and curr_eps > 0:
                growth_cagr["epsCagr10y"] = round((curr_eps / past_eps) ** 0.1 - 1, 4)
            curr_rev = income_annual[0].revenue
            past_rev = income_annual[9].revenue
            if curr_rev and past_rev and past_rev > 0:
                growth_cagr["revenueCagr10y"] = round((curr_rev / past_rev) ** 0.1 - 1, 4)
        # Consecutive quarters growing
        consec = 0
        if len(income_quarterly) >= 5:
            for i in range(len(income_quarterly) - 4):
                curr_q = income_quarterly[i].revenue
                yago_q = income_quarterly[i + 4].revenue
                if curr_q and yago_q and curr_q > yago_q:
                    consec += 1
                else:
                    break
        result["growth"] = {
            "yoy": growth_yoy,
            "cagr": growth_cagr,
            "trimestresConsecutivos": consec,
            "pegRatio": ratios_ttm.pegRatioTTM if ratios_ttm else None,
        }

        # ── Dividends ──
        div_metrics: dict[str, Any] = {}
        if ratios_ttm:
            dy = ratios_ttm.dividendYieldPercentageTTM or ratios_ttm.dividendYieldTTM
            div_metrics["yield"] = dy
            div_metrics["dividendYield"] = dy
            div_metrics["payoutRatio"] = ratios_ttm.payoutRatioTTM
        if key_metrics_ttm and div_metrics.get("yield") is None:
            dy_km = key_metrics_ttm.dividendYieldPercentageTTM or key_metrics_ttm.dividendYieldTTM
            if dy_km is not None:
                div_metrics["yield"] = dy_km
                div_metrics["dividendYield"] = dy_km
            if (
                div_metrics.get("payoutRatio") is None
                and key_metrics_ttm.payoutRatioTTM is not None
            ):
                div_metrics["payoutRatio"] = key_metrics_ttm.payoutRatioTTM
        div_hist_list = []
        for d in historical_dividends:
            div_hist_list.append(
                {
                    "date": d.date,
                    "dividend": d.adjDividend or d.dividend,
                    "label": d.label,
                }
            )
        # Geraldine Weiss
        weiss: dict[str, Any] = {}
        yield_hist = [
            r.dividendYield for r in ratios_annual if r.dividendYield and r.dividendYield > 0
        ]
        if yield_hist and div_hist_list:
            last_4 = [d["dividend"] for d in div_hist_list[:4] if d.get("dividend")]
            dps_actual = sum(last_4) if last_4 else 0
            if dps_actual > 0:
                y_max = max(yield_hist)
                y_min = min(yield_hist)
                y_med = statistics.median(yield_hist)
                weiss["floor"] = round(dps_actual / y_max, 2) if y_max > 0 else None
                weiss["ceiling"] = round(dps_actual / y_min, 2) if y_min > 0 else None
                weiss["fairValue"] = round(dps_actual / y_med, 2) if y_med > 0 else None
                weiss["dpsActual"] = round(dps_actual, 4)
        # DGI cagr
        dgi_cagr = None
        if len(div_hist_list) > 20:
            recent_year = sum(d["dividend"] for d in div_hist_list[:4] if d.get("dividend"))
            past_year = sum(d["dividend"] for d in div_hist_list[16:20] if d.get("dividend"))
            if recent_year and past_year and past_year > 0:
                dgi_cagr = round((recent_year / past_year) ** 0.2 - 1, 4)
        if div_metrics.get("yield") is None and div_hist_list:
            price_ref = None
            if quote and quote.price:
                price_ref = float(quote.price)
            elif profile and profile.price:
                price_ref = float(profile.price)
            if price_ref and price_ref > 0:
                window = div_hist_list[:12]
                dps_ttm = sum(float(d["dividend"]) for d in window if d.get("dividend"))
                if dps_ttm > 0:
                    implied_pct = (dps_ttm / price_ref) * 100.0
                    div_metrics["yield"] = implied_pct
                    div_metrics["dividendYield"] = implied_pct
                    div_metrics["yieldSource"] = "implied_ttm_from_payments"
        result["dividends"] = {
            "metricas": div_metrics,
            "historial": div_hist_list[:50],
            "weiss": weiss,
            "dgiCagr5y": dgi_cagr,
        }

        # ── Technical ──
        tech: dict[str, Any] = {}
        if rsi_data:
            tech["rsi"] = rsi_data[0].rsi
        if sma50_data:
            tech["sma50"] = sma50_data[0].sma
        if sma200_data:
            tech["sma200"] = sma200_data[0].sma
        if sma50_data and sma200_data and sma50_data[0].sma and sma200_data[0].sma:
            tech["goldenCross"] = sma50_data[0].sma > sma200_data[0].sma
            tech["tendencia"] = "ALCISTA" if sma50_data[0].sma > sma200_data[0].sma else "BAJISTA"
        # Bollinger from last 20 closes
        if historical_prices_5y and len(historical_prices_5y) >= 20:
            closes_20 = [p.close for p in historical_prices_5y[:20] if p.close]
            if len(closes_20) == 20:
                sma20 = statistics.mean(closes_20)
                std20 = statistics.stdev(closes_20)
                tech["bollingerUpper"] = round(sma20 + 2 * std20, 2)
                tech["bollingerLower"] = round(sma20 - 2 * std20, 2)
                tech["bollingerSma"] = round(sma20, 2)
        result["technical"] = tech

        # ── Earnings ──
        earn_hist = []
        for e in (earnings_surprises or [])[:20]:
            earn_hist.append(
                {
                    "date": e.date,
                    "epsActual": e.actualEarningResult,
                    "epsEstimated": e.estimatedEarning,
                    "beat": (
                        (e.actualEarningResult or 0) > (e.estimatedEarning or 0)
                        if e.actualEarningResult is not None
                        else None
                    ),
                }
            )
        earn_next = None
        filtered_cal = [
            e for e in (earnings_calendar or []) if e.symbol and e.symbol.upper() == sym
        ]
        if filtered_cal:
            ec = filtered_cal[0]
            earn_next = {
                "date": ec.date,
                "epsEstimated": ec.epsEstimated,
                "revenueEstimated": ec.revenueEstimated,
            }
        # Also check historical earnings for revenue data
        earn_detailed = []
        for e in historical_earnings or []:
            earn_detailed.append(
                {
                    "date": e.date,
                    "eps": e.eps,
                    "epsEstimated": e.epsEstimated,
                    "revenue": e.revenue,
                    "revenueEstimated": e.revenueEstimated,
                    "doubleBeat": (
                        (
                            e.eps is not None
                            and e.epsEstimated is not None
                            and e.eps > e.epsEstimated
                        )
                        and (
                            e.revenue is not None
                            and e.revenueEstimated is not None
                            and e.revenue > e.revenueEstimated
                        )
                    ),
                }
            )
        result["earnings"] = {
            "proxima": earn_next,
            "historial": earn_hist,
            "detallado": earn_detailed,
        }

        # ── Estimates ──
        est_list = []
        for ae in analyst_estimates or []:
            est_list.append(
                {
                    "date": ae.date,
                    "epsAvg": ae.estimatedEpsAvg,
                    "epsLow": ae.estimatedEpsLow,
                    "epsHigh": ae.estimatedEpsHigh,
                    "revenueAvg": ae.estimatedRevenueAvg,
                    "revenueLow": ae.estimatedRevenueLow,
                    "revenueHigh": ae.estimatedRevenueHigh,
                    "ebitdaAvg": ae.estimatedEbitdaAvg,
                    "netIncomeAvg": ae.estimatedNetIncomeAvg,
                    "numAnalysts": ae.numberAnalystEstimatedRevenue,
                }
            )
        est_consenso: dict[str, Any] = {}
        if recommendations:
            est_consenso["buy"] = recommendations.analystRatingsbuy
            est_consenso["hold"] = recommendations.analystRatingsHold
            est_consenso["sell"] = recommendations.analystRatingsSell
            est_consenso["strongBuy"] = recommendations.analystRatingsStrongBuy
            est_consenso["strongSell"] = recommendations.analystRatingsStrongSell
        if price_target:
            est_consenso["targetHigh"] = price_target.targetHigh
            est_consenso["targetLow"] = price_target.targetLow
            est_consenso["targetConsensus"] = price_target.targetConsensus
            est_consenso["targetMedian"] = price_target.targetMedian
        result["estimates"] = {"consenso": est_consenso, "porAnio": est_list}

        # ── Financials (raw statements) ──
        result["financials"] = {
            "incomeStatement": [s.model_dump() for s in income_annual[:10]],
            "balanceSheet": [s.model_dump() for s in balance_annual[:10]],
            "cashFlow": [s.model_dump() for s in cashflow_annual[:10]],
        }

        # ── Transcripts (list only, detail fetched on demand) ──
        result["transcripts"] = [
            {"year": t.year, "quarter": t.quarter, "date": t.date}
            for t in (transcript_list or [])[:30]
        ]

        # ── Drawdown ──
        dd_data: dict[str, Any] = {}
        if historical_prices_5y:
            prices = [p.close for p in reversed(historical_prices_5y) if p.close]
            dates = [p.date for p in reversed(historical_prices_5y) if p.close]
            if prices:
                ath = max(prices)
                ath_idx = prices.index(ath)
                dd_data["ath"] = ath
                dd_data["fechaATH"] = dates[ath_idx] if ath_idx < len(dates) else None
                dd_data["precioActual"] = prices[-1]
                dd_data["ddActual"] = round((prices[-1] - ath) / ath * 100, 2) if ath > 0 else 0
                running_max = 0.0
                drawdowns = []
                for p in prices:
                    running_max = max(running_max, p)
                    drawdowns.append(
                        (p - running_max) / running_max * 100 if running_max > 0 else 0
                    )
                dd_data["maxDrawdown"] = round(min(drawdowns), 2) if drawdowns else 0
                dd_data["ddMedio"] = round(statistics.mean(drawdowns), 2) if drawdowns else 0
        # ── Intelligence (v4 Expansion) ──
        social_data = await self.get_social_sentiment(sym, limit=10)
        insider_data = await self.get_insider_trades(sym, limit=50)
        esg_list = await self.get_esg_data(sym)
        etf_list = await self.get_etf_exposure(sym)

        # Scoring helper for debt
        def _score_debt(k: str, v: float | None) -> dict[str, Any]:
            if v is None:
                return {"score": 0, "label": "N/A"}
            if k == "debtEquity":
                if v < 0.5:
                    return {"score": 95, "label": "Conservador"}
                if v < 1.5:
                    return {"score": 55, "label": "Ajustado"}
                return {"score": 25, "label": "Elevado"}
            if k == "currentRatio" or k == "quickRatio":
                if v > 1.5:
                    return {"score": 90, "label": "Excelente"}
                if v > 1.0:
                    return {"score": 55, "label": "Ajustada"}
                return {"score": 25, "label": "Riesgo"}
            if k == "netDebtToEbitda":
                if v < 1.0:
                    return {"score": 95, "label": "Muy baja"}
                if v < 3.0:
                    return {"score": 60, "label": "Moderada"}
                return {"score": 20, "label": "Elevada"}
            if k == "interestCoverage":
                if v > 10:
                    return {"score": 95, "label": "Excelente"}
                if v > 3:
                    return {"score": 55, "label": "Segura"}
                return {"score": 15, "label": "Peligrosa"}
            return {"score": 50, "label": "Normal"}

        # Add scores to debt_ttm
        debt_data = result.get("debt", {}).get("ttm", {})
        for k in [
            "debtEquity",
            "currentRatio",
            "quickRatio",
            "netDebtToEbitda",
            "interestCoverage",
        ]:
            res = _score_debt(k, debt_data.get(k))
            debt_data[f"{k}Score"] = res["score"]
            debt_data[f"{k}Label"] = res["label"]

        # ── Intelligence (v4 Expansion: Phase 4) ──
        pt_history = await self.get_price_target_history(sym)
        inst_ownership = await self.get_institutional_ownership_history(sym)
        inst_holders = await self.get_institutional_holders(sym)

        # Macro Indicators (Phase 4 Ext)
        gdp_list = await self.get_economic_indicator("GDP")
        cpi_list = await self.get_economic_indicator("CPI")
        unemp_list = await self.get_economic_indicator("unemploymentRate")

        from backend.services.fundamental_intelligence_signals import (
            build_social_insider_transcript_signals,
        )

        (
            social_signal,
            insider_signal,
            transcript_data,
        ) = await build_social_insider_transcript_signals(
            self,
            sym,
            social_data,
            insider_data,
            transcript_list or [],
        )

        result["intelligence_v4"] = {
            "sentiment": social_signal.__dict__ if social_signal else None,
            "insider": insider_signal.__dict__ if insider_signal else None,
            "esg": [e.model_dump() for e in esg_list[:1]] if esg_list else None,
            "etfExposure": [e.model_dump() for e in etf_list[:5]] if etf_list else [],
            "priceTargetHistory": (
                [pt.model_dump() for pt in pt_history[:100]] if pt_history else []
            ),
            "ownershipHistory": (
                [oh.model_dump() for oh in inst_ownership[:20]] if inst_ownership else []
            ),
            "institutionalHolders": (
                [ih.model_dump() for ih in inst_holders[:10]] if inst_holders else []
            ),
            "macro": {
                "gdp": [g.model_dump() for g in gdp_list[:4]] if gdp_list else [],
                "cpi": [c.model_dump() for c in cpi_list[:4]] if cpi_list else [],
                "unemployment": [u.model_dump() for u in unemp_list[:4]] if unemp_list else [],
            },
            "transcript_analysis": transcript_data.__dict__ if transcript_data else None,
        }

        # ── Intelligence (v4 Expansion: Phase 5) ──
        peers_data = await self.get_stock_peers(sym)
        peers_list: list[str] = (
            list(peers_data[0].peersList) if peers_data and peers_data[0].peersList else []
        )

        competitors: list[dict[str, Any]] = []
        if peers_list:
            top_peers = peers_list[:4]
            peer_quotes = await asyncio.gather(*(self.get_quote(p_sym) for p_sym in top_peers))
            for p_quote in peer_quotes:
                if p_quote:
                    competitors.append(p_quote.model_dump())

        def _sort_segment_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if not rows:
                return rows
            return sorted(
                rows,
                key=lambda r: str(
                    r.get("date") or r.get("calendarYear") or r.get("fiscalYear") or ""
                ),
                reverse=True,
            )

        rev_product = _sort_segment_rows(await self.get_revenue_segments(sym, "product"))
        rev_geo = _sort_segment_rows(await self.get_revenue_segments(sym, "geo"))
        health_scores = await self.get_financial_scores(sym)

        # Build normalized health scores for frontend
        health_payload = {}
        if health_scores:
            h = health_scores[0]
            health_payload = {
                "altmanZScore": h.altmanZScore,
                "piotroskiScore": h.piotroskiScore,
                "workingCapital": h.workingCapital,
                "ebit": h.ebit,
            }

        result["intelligence_v4"].update(
            {
                "competitors": competitors,
                "segments": {"product": rev_product, "geographic": rev_geo},
                "health_scores": health_payload if health_payload else None,
            }
        )

        result["drawdown"] = dd_data

        return result

    async def get_revenue_segments(
        self, symbol: str, segment_type: str = "product"
    ) -> list[dict[str, Any]]:
        """
        Revenue segmentation — FMP documents stable host (not /api/v4/...).

        product | geo → revenue-geographic-segmentation
        """
        sym = symbol.upper()
        is_geo = segment_type in {"geo", "geographic"}
        stable_path = (
            "/revenue-geographic-segmentation" if is_geo else "/revenue-product-segmentation"
        )
        data = await self._get_stable(
            stable_path,
            module="STATEMENTS",
            params={"symbol": sym},
            ttl_secs=604800.0,
        )
        rows = data if isinstance(data, list) else []
        if not rows:
            kind = "geographic" if is_geo else "product"
            legacy = await self._get(
                f"/v4/revenue-{kind}-segmentation",
                module="STATEMENTS",
                params={"symbol": sym},
                ttl_secs=604800.0,
            )
            rows = legacy if isinstance(legacy, list) else []

        flattened: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if "data" in row and isinstance(row["data"], dict):
                merged = {k: v for k, v in row.items() if k != "data"}
                merged.update(row["data"])
                flattened.append(merged)
            else:
                flattened.append(row)
        return flattened

    async def get_financial_scores(self, symbol: str) -> list[FMPFinancialScores]:
        """Fetch financial scores (Altman Z, Piotroski). GET /v4/score."""
        data = await self._get(
            "/v4/score", module="STATEMENTS", params={"symbol": symbol.upper()}, ttl_secs=86400.0
        )
        if isinstance(data, dict):
            data = [data]
        return self._parse_list(data, FMPFinancialScores)
