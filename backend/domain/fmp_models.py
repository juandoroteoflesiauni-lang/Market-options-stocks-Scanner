"""
backend/domain/fmp_models.py
════════════════════════════════════════════════════════════════════════════════
Domain models for Financial Modeling Prep (FMP) integration.
Compatible with Pydantic V2.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

# ══════════════════════════════════════════════════════════════════════════════
# §1  FUNDAMENTAL STATEMENTS
# ══════════════════════════════════════════════════════════════════════════════


class FMPIncomeStatement(BaseModel):
    """FMP /income-statement response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    reportedCurrency: str | None = None
    period: str | None = None
    calendarYear: str | None = None
    revenue: float | None = None
    costOfRevenue: float | None = None
    grossProfit: float | None = None
    grossProfitRatio: float | None = None
    researchAndDevelopmentExpenses: float | None = None
    sellingGeneralAndAdministrativeExpenses: float | None = None
    generalAndAdministrativeExpenses: float | None = None
    operatingExpenses: float | None = None
    operatingIncome: float | None = None
    operatingIncomeRatio: float | None = None
    totalOtherIncomeExpensesNet: float | None = None
    interestExpense: float | None = None
    incomeBeforeTax: float | None = None
    incomeBeforeTaxRatio: float | None = None
    incomeTaxExpense: float | None = None
    netIncome: float | None = None
    netIncomeRatio: float | None = None
    eps: float | None = None
    epsDiluted: float | None = None
    ebitda: float | None = None
    ebitdaratio: float | None = None
    weightedAverageShsOut: float | None = None
    weightedAverageShsOutDil: float | None = None


class FMPBalanceSheet(BaseModel):
    """FMP /balance-sheet-statement response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    period: str | None = None
    calendarYear: str | None = None
    cashAndCashEquivalents: float | None = None
    shortTermInvestments: float | None = None
    cashAndShortTermInvestments: float | None = None
    netReceivables: float | None = None
    inventory: float | None = None
    totalCurrentAssets: float | None = None
    propertyPlantEquipmentNet: float | None = None
    goodwill: float | None = None
    intangibleAssets: float | None = None
    longTermInvestments: float | None = None
    otherNonCurrentAssets: float | None = None
    totalNonCurrentAssets: float | None = None
    totalAssets: float | None = None
    accountPayables: float | None = None
    shortTermDebt: float | None = None
    totalCurrentLiabilities: float | None = None
    longTermDebt: float | None = None
    totalNonCurrentLiabilities: float | None = None
    totalLiabilities: float | None = None
    commonStock: float | None = None
    retainedEarnings: float | None = None
    totalStockholdersEquity: float | None = None
    totalEquity: float | None = None
    totalDebt: float | None = None
    netDebt: float | None = None


class FMPCashFlowStatement(BaseModel):
    """FMP /cash-flow-statement response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    period: str | None = None
    calendarYear: str | None = None
    netIncome: float | None = None
    depreciationAndAmortization: float | None = None
    stockBasedCompensation: float | None = None
    changeInWorkingCapital: float | None = None
    accountsReceivables: float | None = None
    inventory: float | None = None
    accountsPayables: float | None = None
    operatingCashFlow: float | None = None
    capitalExpenditure: float | None = None
    acquisitionsNet: float | None = None
    freeCashFlow: float | None = None
    cashAtEndOfPeriod: float | None = None
    cashAtBeginningOfPeriod: float | None = None
    netCashUsedForInvestingActivities: float | None = None
    netCashUsedProvidedByFinancingActivities: float | None = None
    dividendsPaid: float | None = None
    commonStockRepurchased: float | None = None
    debtRepayment: float | None = None
    netChangeInCash: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §2  GROWTH & RATIOS
# ══════════════════════════════════════════════════════════════════════════════


class FMPIncomeStatementGrowth(BaseModel):
    """FMP /income-statement-growth response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    period: str | None = None
    calendarYear: str | None = None
    growthRevenue: float | None = None
    growthCostOfRevenue: float | None = None
    growthGrossProfit: float | None = None
    growthGrossProfitRatio: float | None = None
    growthEBITDA: float | None = None
    growthOperatingIncome: float | None = None
    growthNetIncome: float | None = None
    growthEPS: float | None = None
    growthEPSDiluted: float | None = None
    growthOperatingCashFlow: float | None = None
    growthFreeCashFlow: float | None = None


class FMPFinancialGrowth(BaseModel):
    """FMP /financial-growth response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    period: str | None = None
    calendarYear: str | None = None
    revenueGrowth: float | None = None
    grossProfitGrowth: float | None = None
    ebitgrowth: float | None = None
    operatingIncomeGrowth: float | None = None
    netIncomeGrowth: float | None = None
    epsgrowth: float | None = None
    epsDilutedGrowth: float | None = None
    freeCashFlowGrowth: float | None = None
    assetGrowth: float | None = None
    bookValueperShareGrowth: float | None = None
    debtGrowth: float | None = None
    rdexpenseGrowth: float | None = None
    sgaexpensesGrowth: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §3  KEY METRICS & ENTERPRISE VALUE
# ══════════════════════════════════════════════════════════════════════════════


class FMPKeyMetrics(BaseModel):
    """FMP /key-metrics response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    period: str | None = None
    calendarYear: str | None = None
    revenuePerShare: float | None = None
    netIncomePerShare: float | None = None
    operatingCashFlowPerShare: float | None = None
    freeCashFlowPerShare: float | None = None
    cashPerShare: float | None = None
    bookValuePerShare: float | None = None
    tangibleBookValuePerShare: float | None = None
    shareholdersEquityPerShare: float | None = None
    interestDebtPerShare: float | None = None
    marketCap: float | None = None
    enterpriseValue: float | None = None
    peRatio: float | None = None
    priceToSalesRatio: float | None = None
    pocfratio: float | None = None
    pfcfRatio: float | None = None
    pbRatio: float | None = None
    ptbRatio: float | None = None
    evToSales: float | None = None
    enterpriseValueOverEBITDA: float | None = None
    evToOperatingCashFlow: float | None = None
    evToFreeCashFlow: float | None = None
    earningsYield: float | None = None
    freeCashFlowYield: float | None = None
    debtToEquity: float | None = None
    debtToAssets: float | None = None
    netDebtToEBITDA: float | None = None
    currentRatio: float | None = None
    interestCoverage: float | None = None
    incomeQuality: float | None = None
    dividendYield: float | None = None
    payoutRatio: float | None = None
    returnOnAssets: float | None = None
    returnOnEquity: float | None = None
    returnOnInvestedCapital: float | None = None
    grahamNumber: float | None = None
    roic: float | None = None
    returnOnTangibleAssets: float | None = None
    grahamNetNet: float | None = None
    workingCapital: float | None = None
    tangibleAssetValue: float | None = None
    netCurrentAssetValue: float | None = None
    investedCapital: float | None = None
    averageReceivables: float | None = None
    averagePayables: float | None = None
    averageInventory: float | None = None
    daysSalesOutstanding: float | None = None
    daysPayablesOutstanding: float | None = None
    daysOfInventoryOnHand: float | None = None
    receivablesTurnover: float | None = None
    payablesTurnover: float | None = None
    inventoryTurnover: float | None = None
    capexPerShare: float | None = None


class FMPKeyMetricsTTM(BaseModel):
    """FMP /key-metrics-ttm response (single object, not list)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    revenuePerShareTTM: float | None = None
    netIncomePerShareTTM: float | None = None
    operatingCashFlowPerShareTTM: float | None = None
    freeCashFlowPerShareTTM: float | None = None
    cashPerShareTTM: float | None = None
    bookValuePerShareTTM: float | None = None
    tangibleBookValuePerShareTTM: float | None = None
    shareholdersEquityPerShareTTM: float | None = None
    interestDebtPerShareTTM: float | None = None
    marketCapTTM: float | None = None
    enterpriseValueTTM: float | None = None
    peRatioTTM: float | None = None
    priceToSalesRatioTTM: float | None = None
    pocfratioTTM: float | None = None
    pfcfRatioTTM: float | None = None
    pbRatioTTM: float | None = None
    ptbRatioTTM: float | None = None
    evToSalesTTM: float | None = None
    enterpriseValueOverEBITDATTM: float | None = None
    evToOperatingCashFlowTTM: float | None = None
    evToFreeCashFlowTTM: float | None = None
    earningsYieldTTM: float | None = None
    freeCashFlowYieldTTM: float | None = None
    debtToEquityTTM: float | None = None
    debtToAssetsTTM: float | None = None
    netDebtToEBITDATTM: float | None = None
    currentRatioTTM: float | None = None
    interestCoverageTTM: float | None = None
    incomeQualityTTM: float | None = None
    dividendYieldTTM: float | None = None
    dividendYieldPercentageTTM: float | None = None
    payoutRatioTTM: float | None = None
    returnOnAssetsTTM: float | None = None
    returnOnEquityTTM: float | None = None
    returnOnInvestedCapitalTTM: float | None = None
    roicTTM: float | None = None
    capexPerShareTTM: float | None = None


class FMPEnterpriseValue(BaseModel):
    """FMP /enterprise-values response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    stockPrice: float | None = None
    numberOfShares: float | None = None
    marketCapitalization: float | None = None
    minusCashAndCashEquivalents: float | None = None
    addTotalDebt: float | None = None
    enterpriseValue: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §4  VALUATION & RATING
# ══════════════════════════════════════════════════════════════════════════════


class FMPDCFValuation(BaseModel):
    """FMP /discounted-cash-flow response."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    dcf: float | None = None
    # Note: FMP returns the key as "Stock Price" (with space)
    stock_price: float | None = None


class FMPRating(BaseModel):
    """FMP /rating response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    rating: str | None = None
    ratingScore: int | None = None
    ratingRecommendation: str | None = None
    ratingDetailsDCFScore: int | None = None
    ratingDetailsDCFRecommendation: str | None = None
    ratingDetailsROEScore: int | None = None
    ratingDetailsROERecommendation: str | None = None
    ratingDetailsROAScore: int | None = None
    ratingDetailsROARecommendation: str | None = None
    ratingDetailsDEScore: int | None = None
    ratingDetailsDERecommendation: str | None = None
    ratingDetailsPEScore: int | None = None
    ratingDetailsPERecommendation: str | None = None
    ratingDetailsPBScore: int | None = None
    ratingDetailsPBRecommendation: str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §5  REAL-TIME QUOTE
# ══════════════════════════════════════════════════════════════════════════════


class FMPQuote(BaseModel):
    """FMP /quote response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    name: str | None = None
    price: float | None = None
    changesPercentage: float | None = None
    change: float | None = None
    dayLow: float | None = None
    dayHigh: float | None = None
    yearHigh: float | None = None
    yearLow: float | None = None
    marketCap: float | None = None
    priceAvg50: float | None = None
    priceAvg200: float | None = None
    exchange: str | None = None
    volume: float | None = None
    avgVolume: float | None = None
    open: float | None = None
    previousClose: float | None = None
    eps: float | None = None
    pe: float | None = None
    earningsAnnouncement: str | None = None
    sharesOutstanding: float | None = None
    timestamp: int | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §6  TECHNICAL INDICATORS
# ══════════════════════════════════════════════════════════════════════════════


class FMPTechnicalIndicator(BaseModel):
    """FMP /technical_indicator response item (EMA, RSI, etc.)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    # Indicator-specific value fields (present depending on type)
    ema: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macdSignal: float | None = None
    macdHistogram: float | None = None
    sma: float | None = None
    williams: float | None = None
    adx: float | None = None
    standardDeviation: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §7  NEWS
# ══════════════════════════════════════════════════════════════════════════════


class FMPNewsItem(BaseModel):
    """FMP /stock_news response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    publishedDate: str | None = None
    title: str | None = None
    image: str | None = None
    site: str | None = None
    text: str | None = None
    url: str | None = None


class FMPPressRelease(BaseModel):
    """FMP /press-releases response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    title: str | None = None
    text: str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §8  INSTITUTIONAL OWNERSHIP
# ══════════════════════════════════════════════════════════════════════════════


class FMPInstitutionalHolder(BaseModel):
    """FMP /institutional-holder response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    holder: str | None = None
    shares: float | None = None
    dateReported: str | None = None
    change: float | None = None


class FMPMutualFundHolder(BaseModel):
    """FMP /mutual-fund-holder response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    holder: str | None = None
    shares: float | None = None
    dateReported: str | None = None
    change: float | None = None
    weightPercent: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §9  CALENDARS
# ══════════════════════════════════════════════════════════════════════════════


class FMPEarningsCalendarItem(BaseModel):
    """FMP /earning_calendar response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    eps: float | None = None
    epsEstimated: float | None = None
    time: str | None = None
    revenue: float | None = None
    revenueEstimated: float | None = None
    updatedFromDate: str | None = None
    fiscalDateEnding: str | None = None


class FMPIPOCalendarItem(BaseModel):
    """FMP /ipo_calendar response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    company: str | None = None
    symbol: str | None = None
    exchange: str | None = None
    actions: str | None = None
    shares: float | None = None
    priceRange: str | None = None
    marketCap: float | None = None


class FMPDividendCalendarItem(BaseModel):
    """FMP /stock_dividend_calendar response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    label: str | None = None
    adjDividend: float | None = None
    symbol: str | None = None
    dividend: float | None = None
    recordDate: str | None = None
    paymentDate: str | None = None
    declarationDate: str | None = None


class FMPEconomicCalendarItem(BaseModel):
    """FMP /economic_calendar response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    event: str | None = None
    country: str | None = None
    impact: str | None = None


class FMPShortVolume(BaseModel):
    """FMP /v4/short-volume response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    shortVolume: float | None = None
    shortExemptVolume: float | None = None
    totalVolume: float | None = None
    market: str | None = None


class FMPShortInterest(BaseModel):
    """FMP /v4/short-interest response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    shortInterest: float | None = None
    shortInterestRatio: float | None = None
    floatPercent: float | None = None
    daysToCover: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §10  COMPANY PROFILE
# ══════════════════════════════════════════════════════════════════════════════


class FMPProfile(BaseModel):
    """FMP /v3/profile/{symbol} response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    companyName: str | None = None
    exchangeShortName: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    currency: str | None = None
    ipoDate: str | None = None
    fullTimeEmployees: int | None = None
    ceo: str | None = None
    website: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    phone: str | None = None
    isin: str | None = None
    cusip: str | None = None
    cik: str | None = None
    beta: float | None = None
    mktCap: float | None = None
    price: float | None = None
    range: str | None = None
    volAvg: float | None = None
    description: str | None = None
    image: str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §11  RATIOS TTM
# ══════════════════════════════════════════════════════════════════════════════


class FMPRatiosTTM(BaseModel):
    """FMP /v3/ratios-ttm/{symbol} response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    peRatioTTM: float | None = Field(
        None, validation_alias=AliasChoices("peRatioTTM", "peTTM", "peRatio")
    )
    pegRatioTTM: float | None = Field(
        None, validation_alias=AliasChoices("pegRatioTTM", "pegTTM", "pegRatio")
    )
    priceToBookRatioTTM: float | None = Field(
        None, validation_alias=AliasChoices("priceToBookRatioTTM", "pbTTM", "priceToBookRatio")
    )
    priceToSalesRatioTTM: float | None = Field(
        None, validation_alias=AliasChoices("priceToSalesRatioTTM", "psTTM", "priceToSalesRatio")
    )
    enterpriseValueMultipleTTM: float | None = Field(
        None,
        validation_alias=AliasChoices(
            "enterpriseValueMultipleTTM", "evToEbitdaTTM", "enterpriseValueMultiple"
        ),
    )
    evToSalesTTM: float | None = Field(
        None,
        validation_alias=AliasChoices(
            "evToSalesTTM", "evToSales", "evSalesTTM", "enterpriseValueOverSalesTTM"
        ),
    )
    evToFreeCashFlowTTM: float | None = Field(
        None,
        validation_alias=AliasChoices(
            "evToFreeCashFlowTTM",
            "evToFreeCashFlow",
            "evFcfTTM",
            "enterpriseValueOverFreeCashFlowTTM",
        ),
    )
    priceToFreeCashFlowsRatioTTM: float | None = None
    returnOnEquityTTM: float | None = None
    returnOnAssetsTTM: float | None = None
    returnOnCapitalEmployedTTM: float | None = None
    grossProfitMarginTTM: float | None = None
    operatingProfitMarginTTM: float | None = None
    netProfitMarginTTM: float | None = None
    debtEquityRatioTTM: float | None = None
    currentRatioTTM: float | None = None
    quickRatioTTM: float | None = None
    interestCoverageTTM: float | None = None
    dividendYieldTTM: float | None = None
    dividendYieldPercentageTTM: float | None = None
    payoutRatioTTM: float | None = None
    priceEarningsToGrowthRatioTTM: float | None = None
    freeCashFlowPerShareTTM: float | None = None
    operatingCashFlowPerShareTTM: float | None = None
    cashPerShareTTM: float | None = None


class FMPRatiosAnnual(BaseModel):
    """FMP /v3/ratios/{symbol} response item — annual ratios for historical charts."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    calendarYear: str | None = None
    period: str | None = None
    priceEarningsRatio: float | None = Field(
        None, validation_alias=AliasChoices("priceEarningsRatio", "peRatio", "pe")
    )
    priceEarningsToGrowthRatio: float | None = Field(
        None, validation_alias=AliasChoices("priceEarningsToGrowthRatio", "pegRatio", "peg")
    )

    priceToBookRatio: float | None = Field(
        None, validation_alias=AliasChoices("priceToBookRatio", "pbRatio", "pb")
    )
    priceToSalesRatio: float | None = Field(
        None, validation_alias=AliasChoices("priceToSalesRatio", "psRatio", "ps")
    )
    enterpriseValueMultiple: float | None = Field(
        None, validation_alias=AliasChoices("enterpriseValueMultiple", "evToEbitda", "evEbitda")
    )

    returnOnEquity: float | None = None
    returnOnAssets: float | None = None
    returnOnCapitalEmployed: float | None = None
    grossProfitMargin: float | None = None
    operatingProfitMargin: float | None = None
    netProfitMargin: float | None = None
    debtEquityRatio: float | None = None
    currentRatio: float | None = None
    quickRatio: float | None = None
    interestCoverage: float | None = None
    dividendYield: float | None = None
    payoutRatio: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §12  ANALYST ESTIMATES & RECOMMENDATIONS
# ══════════════════════════════════════════════════════════════════════════════


class FMPAnalystEstimate(BaseModel):
    """FMP /v3/analyst-estimates/{symbol} response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    estimatedRevenueAvg: float | None = None
    estimatedRevenueLow: float | None = None
    estimatedRevenueHigh: float | None = None
    estimatedEpsAvg: float | None = None
    estimatedEpsLow: float | None = None
    estimatedEpsHigh: float | None = None
    estimatedEbitdaAvg: float | None = None
    estimatedEbitdaLow: float | None = None
    estimatedEbitdaHigh: float | None = None
    estimatedNetIncomeAvg: float | None = None
    estimatedNetIncomeLow: float | None = None
    estimatedNetIncomeHigh: float | None = None
    numberAnalystEstimatedRevenue: int | None = None
    numberAnalystsEstimatedEps: int | None = None


class FMPStockRecommendation(BaseModel):
    """FMP /v3/analyst-stock-recommendations/{symbol} response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    analystRatingsbuy: int | None = None
    analystRatingsHold: int | None = None
    analystRatingsSell: int | None = None
    analystRatingsStrongBuy: int | None = None
    analystRatingsStrongSell: int | None = None


class FMPPriceTargetConsensus(BaseModel):
    """FMP /v3/price-target-consensus/{symbol} response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    targetHigh: float | None = None
    targetLow: float | None = None
    targetConsensus: float | None = None
    targetMedian: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §13  TRANSCRIPTS
# ══════════════════════════════════════════════════════════════════════════════


class FMPTranscriptListItem(BaseModel):
    """FMP /v4/batch_earning_call_transcript/{symbol} response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    quarter: int | None = None
    year: int | None = None
    date: str | None = None


class FMPTranscript(BaseModel):
    """FMP /v3/earning_call_transcript/{symbol} response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    quarter: int | None = None
    year: int | None = None
    date: str | None = None
    content: str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §14  HISTORICAL PRICES
# ══════════════════════════════════════════════════════════════════════════════


class FMPHistoricalPrice(BaseModel):
    """FMP /v3/historical-price-full/{symbol} → historical[] item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    adjClose: float | None = None
    volume: float | None = None
    changePercent: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §15  EARNINGS SURPRISES
# ══════════════════════════════════════════════════════════════════════════════


class FMPEarningsSurprise(BaseModel):
    """FMP /v3/earnings-surprises/{symbol} response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    actualEarningResult: float | None = None
    estimatedEarning: float | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §14  EXPANSION MODULES (v4)
# ══════════════════════════════════════════════════════════════════════════════


class FMPInsiderTrade(BaseModel):
    """FMP /v4/insider-trading response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    filingDate: str | None = None
    transactionDate: str | None = None
    reportingCik: str | None = None
    companyCik: str | None = None
    transactionType: str | None = None
    securitiesOwned: float | None = None
    reportingName: str | None = None
    typeOfOwner: str | None = None
    acquisitionOrDisposition: str | None = None
    directOrIndirect: str | None = None
    formType: str | None = None
    securitiesTransacted: float | None = None
    price: float | None = None
    securityName: str | None = None
    url: str | None = None


class FMPSocialSentiment(BaseModel):
    """FMP /v4/social-sentiment response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    stocktwitsPosts: int | None = None
    stocktwitsComments: int | None = None
    stocktwitsLikes: int | None = None
    stocktwitsImpressions: int | None = None
    stocktwitsSentiment: float | None = None
    twitterPosts: int | None = None
    twitterComments: int | None = None
    twitterLikes: int | None = None
    twitterImpressions: int | None = None
    twitterSentiment: float | None = None


class FMPESGData(BaseModel):
    """FMP /v4/esg-environmental-social-governance-data response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    acceptedDate: str | None = None
    cik: str | None = None
    companyName: str | None = None
    formType: str | None = None
    environmentalScore: float | None = None
    socialScore: float | None = None
    governanceScore: float | None = None
    ESGScore: float | None = None
    url: str | None = None


class FMPETFExposure(BaseModel):
    """FMP /v4/etf-stock-exposure response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    asset: str | None = None
    sharesNumber: float | None = None
    weightPercentage: float | None = None
    marketValue: float | None = None


class FMPTreasuryRate(BaseModel):
    """FMP /v4/treasury response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    month1: float | None = Field(None, alias="month1")
    month3: float | None = Field(None, alias="month3")
    month6: float | None = Field(None, alias="month6")
    year1: float | None = Field(None, alias="year1")
    year2: float | None = Field(None, alias="year2")
    year3: float | None = Field(None, alias="year3")
    year5: float | None = Field(None, alias="year5")
    year7: float | None = Field(None, alias="year7")
    year10: float | None = Field(None, alias="year10")
    year20: float | None = Field(None, alias="year20")
    year30: float | None = Field(None, alias="year30")


# ── Phase 4 Institutional Models ───────────────────────────────────────────


class FMPPriceTargetDetail(BaseModel):
    """FMP /v4/price-target detailed analyst update."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    publishedDate: str | None = None
    analystName: str | None = None
    analystCompany: str | None = None
    priceTarget: float | None = None
    adjPriceTarget: float | None = None
    newsSource: str | None = None


class FMPInstitutionalOwnershipPercent(BaseModel):
    """FMP institutional ownership percent (v4 legacy + stable summary aliases)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    institutionalOwnershipPercentage: float | None = Field(
        None,
        validation_alias=AliasChoices(
            "institutionalOwnershipPercentage",
            "ownershipPercent",
            "institutionalOwnership",
        ),
    )


class FMPInstitutionalHolderDetail(BaseModel):
    """FMP /v3/institutional-holder item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    holder: str | None = None
    shares: int | None = None
    dateReported: str | None = None
    change: int | None = None


class FMPStockPeer(BaseModel):
    """FMP /v4/stock-peers item."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    symbol: str
    peersList: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("peersList", "peers", "peerList"),
    )


class FMPSegmentData(BaseModel):
    """FMP /v4/revenue-product-segmentation or geographic item."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    date: str
    symbol: str


class FMPFinancialScores(BaseModel):
    """FMP /v4/score (and legacy financial-scores) item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    altmanZScore: float | None = Field(
        None,
        validation_alias=AliasChoices("altmanZScore", "altman_z", "altmanZ"),
    )
    piotroskiScore: int | None = Field(
        None,
        validation_alias=AliasChoices("piotroskiScore", "piotroski", "piotroskiFScore"),
    )
    workingCapital: float | None = None
    retainedEarnings: float | None = None
    ebit: float | None = None
    marketCap: float | None = None
    totalAssets: float | None = None
    revenue: float | None = None


class FMPEconomicIndicator(BaseModel):
    """FMP /v4/economic item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str
    value: float


class FMPOptionsIVHistorical(BaseModel):
    """FMP /v4/options-iv-historical response item."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    impliedVolatility: float | None = None
    putIv: float | None = None
    callIv: float | None = None


__all__ = [
    "FMPAnalystEstimate",
    "FMPBalanceSheet",
    "FMPCashFlowStatement",
    "FMPDCFValuation",
    "FMPDividendCalendarItem",
    "FMPESGData",
    "FMPETFExposure",
    "FMPEarningsCalendarItem",
    "FMPEarningsSurprise",
    "FMPEconomicCalendarItem",
    "FMPEconomicIndicator",
    "FMPEnterpriseValue",
    "FMPFinancialGrowth",
    "FMPFinancialScores",
    "FMPHistoricalPrice",
    "FMPIPOCalendarItem",
    "FMPIncomeStatement",
    "FMPIncomeStatementGrowth",
    "FMPInsiderTrade",
    "FMPInstitutionalHolder",
    "FMPInstitutionalHolderDetail",
    "FMPInstitutionalOwnershipPercent",
    "FMPKeyMetrics",
    "FMPKeyMetricsTTM",
    "FMPMutualFundHolder",
    "FMPNewsItem",
    "FMPOptionsIVHistorical",
    "FMPPressRelease",
    "FMPPriceTargetConsensus",
    "FMPPriceTargetDetail",
    "FMPProfile",
    "FMPQuote",
    "FMPRating",
    "FMPRatiosAnnual",
    "FMPRatiosTTM",
    "FMPSegmentData",
    "FMPShortInterest",
    "FMPShortVolume",
    "FMPSocialSentiment",
    "FMPStockPeer",
    "FMPStockRecommendation",
    "FMPTechnicalIndicator",
    "FMPTranscript",
    "FMPTranscriptListItem",
    "FMPTreasuryRate",
]

# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : fmp_models.py
# Sub-capa         : Domain / Contracts
# Enfoque          : Modelos FMP completos normalizados (Pydantic V2).
# ─────────────────────────────────────────────────────────────────────
