from __future__ import annotations
"""
Financial Statement Domain Models.

PRINCIPLE: These models represent the balance sheet and statement contracts
that quant engines receive as input. Pure data structures — no business logic,
no I/O, no persistence.

All fields are Optional[float] because a missing value (None) is semantically
distinct from a zero value:
    None → "data not available" → criterion using it is skipped
    0.0  → "data exists and equals zero" → criterion computed with 0
"""


from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────────
# INCOME STATEMENT (P&L)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class IncomeStatement:
    """
    Fiscal period income statement.
    Sign convention: all values in reporting currency.
    Costs and expenses stored as POSITIVE values (e.g. cogs = 500, not -500).
    """

    fiscal_year: int | None = None  # e.g. 2024

    # ── Revenue ───────────────────────────────────────────────────────────────
    total_revenue: float | None = None  # Net total revenue
    gross_profit: float | None = None  # Gross profit (Rev - COGS)
    cost_of_revenue: float | None = None  # Cost of goods sold (COGS)

    # ── Operations ────────────────────────────────────────────────────────────
    operating_income: float | None = None  # EBIT proxy if ebit absent
    ebit: float | None = None  # Earnings Before Interest & Tax
    sga_expense: float | None = None  # SG&A (Selling, General & Admin)
    depreciation: float | None = None  # D&A (may be negative in source)

    # ── Net result ────────────────────────────────────────────────────────────
    net_income: float | None = None  # Net income

    # ── Taxes and debt costs ──────────────────────────────────────────────────
    interest_expense: float | None = None  # Financial expenses (abs value)
    tax_provision: float | None = None  # Income tax provision
    pretax_income: float | None = None  # EBT (Earnings Before Tax)

    # ── Shares ────────────────────────────────────────────────────────────────
    shares_outstanding: float | None = None  # Shares outstanding


# ─────────────────────────────────────────────────────────────────────────────
# BALANCE SHEET
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class BalanceSheet:
    """Balance sheet snapshot at fiscal period close."""

    fiscal_year: int | None = None

    # ── Assets ────────────────────────────────────────────────────────────────
    total_assets: float | None = None
    current_assets: float | None = None
    cash: float | None = None  # Cash & equivalents

    # Net PP&E (Property, Plant & Equipment after accumulated depreciation)
    ppe_net: float | None = None
    accounts_receivable: float | None = None  # Net accounts receivable
    retained_earnings: float | None = None  # Cumulative retained earnings

    # ── Liabilities ───────────────────────────────────────────────────────────
    total_liabilities: float | None = None
    current_liabilities: float | None = None
    long_term_debt: float | None = None  # Long-term debt

    # ── Equity ────────────────────────────────────────────────────────────────
    total_equity: float | None = None  # Total net equity
    book_value_equity: float | None = None  # BV Equity (= Common Stock Equity)

    # ── Market metrics (attached for convenience) ─────────────────────────────
    market_cap: float | None = None  # Market capitalisation


# ─────────────────────────────────────────────────────────────────────────────
# CASH FLOW STATEMENT
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CashFlowStatement:
    """Cash flows for the period."""

    fiscal_year: int | None = None

    operating_cash_flow: float | None = None  # CFO — operating cash flow
    capex: float | None = None  # Capital expenditures (negative)
    free_cash_flow: float | None = None  # FCF = CFO - |CapEx|


# ─────────────────────────────────────────────────────────────────────────────
# CONSOLIDATED FINANCIAL STATEMENTS ENVELOPE
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FinancialStatements:
    """
    Envelope grouping the three financial statements for an issuer.

    Ordering convention: newest-first in all lists.
        income[0]   = period T (most recent)
        income[1]   = period T-1
        income[2]   = period T-2 (if available)

    Minimum required for delta calculations (Piotroski, Beneish):
        income   >= 2 periods
        balance  >= 2 periods
        cashflow >= 1 period
    """

    ticker: str
    income: list[IncomeStatement] = field(default_factory=list)
    balance: list[BalanceSheet] = field(default_factory=list)
    cashflow: list[CashFlowStatement] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONS CHAIN SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class OptionsChainSnapshot:
    """
    Summary of the nearest options chain expiration (>= 7 days).
    All fields pre-computed by the acquisition layer before entering the
    scoring engine — this model receives already-aggregated values.
    """

    expiry: str | None = None  # "YYYY-MM-DD"

    # OI-weighted average IV
    iv_avg_calls: float | None = None  # Call IV (decimal: 0.30 = 30%)
    iv_avg_puts: float | None = None  # Put IV
    iv_avg: float | None = None  # Combined average IV

    # Put/Call Ratios
    put_call_ratio_vol: float | None = None  # Σvol_puts / Σvol_calls
    put_call_ratio_oi: float | None = None  # ΣOI_puts  / ΣOI_calls

    # Options structure levels (for forensic engine V6)
    call_wall: float | None = None  # Strike with highest call OI
    zero_gamma: float | None = None  # Level where net GEX = 0


# ─────────────────────────────────────────────────────────────────────────────
# DIRECT VALUATION METRICS (pre-computed by data provider)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ValuationMetrics:
    """
    Relative valuation ratios provided directly by the data provider.
    Used in fundamental scoring when not computable from scratch.
    """

    pe_trailing: float | None = None  # P/E Trailing (last 12 months)
    pe_forward: float | None = None  # P/E Forward (estimated next 12m)
    price_to_book: float | None = None  # Price / Book Value
    ev_to_ebitda: float | None = None  # EV / EBITDA

    roe: float | None = None  # Return on Equity (decimal)
    profit_margin: float | None = None  # Net margin (decimal)
    revenue_growth: float | None = None  # YoY revenue growth (decimal)
    earnings_growth: float | None = None  # YoY EPS growth (decimal)

    debt_to_equity: float | None = None  # D/E in % (150 = 1.5x)
    current_ratio: float | None = None  # Current assets / current liabilities
    free_cash_flow: float | None = None  # Absolute FCF (ticker currency)

    beta: float | None = None  # Beta vs. benchmark index
    enterprise_value: float | None = None  # Total EV
    market_cap: float | None = None


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: statements.py
# Eliminado: referencias de ruta/procedencia del sistema anterior
# Preservado: contratos de IncomeStatement/BalanceSheet/CashFlow/FinancialStatements/Options/ValuationMetrics
# Pendientes: ninguno
# ─────────────────────────────────────────────────
