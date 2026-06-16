"""Contratos de Respuesta de FMP (Financial Modeling Prep) — Sector Opciones/GEX.

Define los modelos Pydantic V2 para las respuestas de la API de FMP,
incluyendo estados financieros, ratios, ratings, noticias y calendarios.
Estos modelos son compartidos con los sectores de Fundamentales y Orquestación.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ══════════════════════════════════════════════════════════════════════════════
# §1  FUNDAMENTAL STATEMENTS
# ══════════════════════════════════════════════════════════════════════════════


class FMPIncomeStatement(BaseModel):
    """Respuesta de FMP /income-statement."""

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
    generalAndAdministrativeExpenses: float | None = None
    operatingExpenses: float | None = None
    operatingIncome: float | None = None
    operatingIncomeRatio: float | None = None
    totalOtherIncomeExpensesNet: float | None = None
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
    """Respuesta de FMP /balance-sheet-statement."""

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
    """Respuesta de FMP /cash-flow-statement."""

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


# ══════════════════════════════════════════════════════════════════════════════
# §2  GROWTH & RATIOS
# ══════════════════════════════════════════════════════════════════════════════


class FMPIncomeStatementGrowth(BaseModel):
    """Respuesta de FMP /income-statement-growth."""

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
    """Respuesta de FMP /financial-growth."""

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
    """Respuesta de FMP /key-metrics."""

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
    """Respuesta de FMP /key-metrics-ttm."""

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
    """Respuesta de FMP /enterprise-values."""

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
    """Respuesta de FMP /discounted-cash-flow."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    dcf: float | None = None
    stock_price: float | None = Field(default=None, alias="Stock Price")


class FMPRating(BaseModel):
    """Respuesta de FMP /rating."""

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
    ratingDetailsPERecommendation: int | None = None
    ratingDetailsPBScore: int | None = None
    ratingDetailsPBRecommendation: str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §5  REAL-TIME QUOTE
# ══════════════════════════════════════════════════════════════════════════════


class FMPQuote(BaseModel):
    """Respuesta de FMP /quote."""

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
    """Respuesta de FMP /technical_indicator."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
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
    """Respuesta de FMP /stock_news."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    publishedDate: str | None = None
    title: str | None = None
    image: str | None = None
    site: str | None = None
    text: str | None = None
    url: str | None = None


class FMPPressRelease(BaseModel):
    """Respuesta de FMP /press-releases."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str | None = None
    date: str | None = None
    title: str | None = None
    text: str | None = None


# ══════════════════════════════════════════════════════════════════════════════
# §8  INSTITUTIONAL OWNERSHIP
# ══════════════════════════════════════════════════════════════════════════════


class FMPInstitutionalHolder(BaseModel):
    """Respuesta de FMP /institutional-holder."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    holder: str | None = None
    shares: float | None = None
    dateReported: str | None = None
    change: float | None = None


class FMPMutualFundHolder(BaseModel):
    """Respuesta de FMP /mutual-fund-holder."""

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
    """Respuesta de FMP /earning_calendar."""

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
    """Respuesta de FMP /ipo_calendar."""

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
    """Respuesta de FMP /stock_dividend_calendar."""

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
    """Respuesta de FMP /economic_calendar."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    impact: str | None = None


class FMPShortVolume(BaseModel):
    """Respuesta de FMP /v4/short-volume."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    shortVolume: float | None = None
    shortExemptVolume: float | None = None
    totalVolume: float | None = None
    market: str | None = None


class FMPShortInterest(BaseModel):
    """Respuesta de FMP /v4/short-interest."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    date: str | None = None
    symbol: str | None = None
    shortInterest: float | None = None
    shortInterestRatio: float | None = None
    floatPercent: float | None = None
    daysToCover: float | None = None


# ─────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: OPCIONES
# Archivo      : fmp_models.py
# Sub-capa     : Modelo (Shared FMP Models)
# Eliminado    : Referencias QuantumBeta V1 / Header legacy.
# Preservado   : 22 modelos de FMP (Income, Growth, Metrics, Ratings, etc.).
# Pendientes   : Ninguno. Todos los modelos son compatibles con Pydantic V2.
# ─────────────────────────────────────────────────────────────
