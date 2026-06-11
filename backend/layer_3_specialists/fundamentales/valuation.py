"""Valuation Engine — pure-math fundamental and DCF valuation."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .statements import (
    BalanceSheet,
    CashFlowStatement,
    FinancialStatements,
    IncomeStatement,
    OptionsChainSnapshot,
    ValuationMetrics,
)

REALTIME_API_ENV_KEYS: tuple[str, ...] = (
    "FMP_KEY_STATEMENTS",
    "FMP_KEY_MARKET",
    "MASSIVE_KEY_FINANCIALS",
)


# ─────────────────────────────────────────────────────────────────────────────
# MACRO ASSUMPTIONS
# ─────────────────────────────────────────────────────────────────────────────

# FundamentalValuation (V3 WACC)
WACC_RF_RATE: float = 0.042
WACC_ERP: float = 0.055
WACC_DEFAULT_BETA: float = 1.0
WACC_DEFAULT_RD: float = 0.05
ROIC_DEFAULT_TAX: float = 0.21
WACC_RD_MIN: float = 0.005
WACC_RD_MAX: float = 0.20
TAX_RATE_MAX: float = 0.60

# DCFValuationModel capital market constants
_RF: float = 0.04
_ERP: float = 0.05
_DEFAULT_BETA: float = 1.1
_DEFAULT_TAX: float = 0.21
_DEFAULT_D_RATIO: float = 0.30
_CREDIT_SPREAD: float = 0.02
_MIN_WACC: float = 0.04
_MAX_WACC: float = 0.20
_G_TERMINAL: float = 0.025
_MAX_G1: float = 0.15
_MIN_G1: float = 0.00

_SECTOR_DEFAULTS: dict[str, float] = {
    "Technology": 0.10,
    "Healthcare": 0.08,
    "Consumer Cyclical": 0.07,
    "Communication Services": 0.07,
    "Consumer Defensive": 0.05,
    "Financial Services": 0.06,
    "Industrials": 0.06,
    "Real Estate": 0.05,
    "Energy": 0.04,
    "Basic Materials": 0.04,
    "Utilities": 0.03,
}
_DEFAULT_G1: float = 0.06

_MOS_BULLISH: float = 20.0
_MOS_BULLISH_WATCH: float = 5.0
_MOS_BEARISH_WATCH: float = -5.0
_MOS_BEARISH: float = -20.0

# IV / PCR thresholds
IV_THRESHOLDS: list[tuple[float, str]] = [
    (0.20, "LOW"),
    (0.40, "NORMAL"),
    (0.60, "ELEVATED"),
]
IV_LABEL_HIGH = "HIGH"
PCR_THRESHOLDS: list[tuple[float, str]] = [
    (0.70, "BULLISH"),
    (1.00, "NEUTRAL"),
    (1.30, "BEARISH"),
]
PCR_LABEL_EXTREME = "EXTREME_FEAR"
IV_ANOMALY_CAP = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# RESULT DATACLASSES
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ValueCreationResult:
    roic: float | None
    wacc: float | None
    economic_spread: float | None
    value_creation_label: str
    nopat: float | None
    invested_capital: float | None
    tax_rate_used: float
    re_used: float
    rd_used: float


@dataclass
class OptionsAnalysisResult:
    iv_avg: float | None
    iv_label: str
    put_call_ratio_vol: float | None
    put_call_ratio_oi: float | None
    pcr_label: str


@dataclass
class FundamentalScoreResult:
    score: float
    sesgo: str
    valuation_contribution: float
    profitability_contribution: float
    financial_strength_contribution: float
    options_contribution: float


@dataclass
class DCFOutput:
    ticker: str
    intrinsic_value: float | None
    margin_of_safety_pct: float | None
    bias: str
    wacc_used: float
    g1_used: float
    pv_fcf: float
    pv_terminal: float
    equity_value: float | None
    graham_number: float | None
    cedear_iv_ars: float | None


# ─────────────────────────────────────────────────────────────────────────────
# MATH HELPERS
# ─────────────────────────────────────────────────────────────────────────────


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None:
        return None
    if abs(den) < 1e-10:
        return None
    return num / den


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ─────────────────────────────────────────────────────────────────────────────
# FundamentalValuation
# ─────────────────────────────────────────────────────────────────────────────


class FundamentalValuation:
    """Fundamental valuation engine. All methods are pure static."""

    @staticmethod
    def value_creation(
        fs: FinancialStatements,
        market_cap: float | None,
        beta: float | None = None,
    ) -> ValueCreationResult:
        """ROIC / WACC / Economic Spread."""
        is_t = fs.income[0] if fs.income else IncomeStatement()
        bs_t = fs.balance[0] if fs.balance else BalanceSheet()
        cf_t = fs.cashflow[0] if fs.cashflow else CashFlowStatement()

        # ── Tax rate ──────────────────────────────────────────────────────────
        ebt = (
            _safe_div(is_t.net_income, (1.0 - ROIC_DEFAULT_TAX))
            if is_t.net_income is not None
            else None
        )
        if ebt is not None and ebt != 0 and is_t.net_income is not None:
            raw_tax = 1.0 - is_t.net_income / ebt
            tax_rate = _clamp(raw_tax, 0.0, TAX_RATE_MAX)
        else:
            tax_rate = ROIC_DEFAULT_TAX

        # ── WACC ──────────────────────────────────────────────────────────────
        beta_val = beta if beta is not None else WACC_DEFAULT_BETA
        re = WACC_RF_RATE + beta_val * WACC_ERP

        total_debt = (bs_t.long_term_debt or 0.0) + (bs_t.current_liabilities or 0.0)
        total_equity = bs_t.book_value_equity or market_cap or 0.0
        total_cap = total_debt + total_equity

        if is_t.interest_expense is not None and total_debt > 0:
            rd_raw = _safe_div(is_t.interest_expense, total_debt) or WACC_DEFAULT_RD
            rd = _clamp(rd_raw, WACC_RD_MIN, WACC_RD_MAX)
        else:
            rd = WACC_DEFAULT_RD

        if total_cap > 0:
            w_e = total_equity / total_cap
            w_d = total_debt / total_cap
        else:
            w_e, w_d = 0.7, 0.3

        rd_at = rd * (1.0 - tax_rate)
        wacc = _clamp(w_e * re + w_d * rd_at, WACC_RD_MIN, WACC_RD_MAX)

        # ── NOPAT ─────────────────────────────────────────────────────────────
        ebit = is_t.ebit if is_t.ebit is not None else is_t.operating_income
        nopat = (ebit * (1.0 - tax_rate)) if ebit is not None else None

        # ── Invested Capital ───────────────────────────────────────────────────
        invested_capital = None
        ta = bs_t.total_assets
        if ta is not None:
            cash = 0.0  # simplified; no cash field on BalanceSheet
            invested_capital = total_debt + total_equity - cash

        # ── ROIC ──────────────────────────────────────────────────────────────
        roic = _safe_div(nopat, invested_capital)
        spread = (roic - wacc) if roic is not None else None

        if spread is None:
            label = "N/D"
        elif spread > 0:
            label = "VALUE_CREATOR"
        else:
            label = "VALUE_DESTROYER"

        return ValueCreationResult(
            roic=round(roic, 4) if roic else None,
            wacc=round(wacc, 4),
            economic_spread=round(spread, 4) if spread else None,
            value_creation_label=label,
            nopat=round(nopat, 2) if nopat else None,
            invested_capital=round(invested_capital, 2) if invested_capital else None,
            tax_rate_used=tax_rate,
            re_used=re,
            rd_used=rd,
        )

    @staticmethod
    def options_analysis(opts: OptionsChainSnapshot) -> OptionsAnalysisResult:
        """IV weighted average and Put/Call ratio analysis."""
        # ── IV average weighted by OI ─────────────────────────────────────────
        iv_avg = None
        if opts.call_iv and opts.call_oi:
            weights = [oi for oi in opts.call_oi if oi > 0]
            ivs = [iv for iv in opts.call_iv if 0 < iv < IV_ANOMALY_CAP]
            if weights and ivs and len(weights) == len(ivs):
                total_w = sum(weights)
                iv_avg = (
                    sum(iv * w for iv, w in zip(ivs, weights, strict=False)) / total_w if total_w > 0 else None
                )

        iv_label = "N/D"
        if iv_avg is not None:
            for thresh, lbl in IV_THRESHOLDS:
                if iv_avg < thresh:
                    iv_label = lbl
                    break
            else:
                iv_label = IV_LABEL_HIGH

        # ── Put/Call ratio ────────────────────────────────────────────────────
        pcr_vol = None
        if opts.put_volume is not None and opts.call_volume is not None and opts.call_volume > 0:
            pcr_vol = opts.put_volume / opts.call_volume

        pcr_oi = None
        if opts.put_oi and opts.call_oi:
            total_put_oi = sum(v for v in opts.put_oi if v > 0)
            total_call_oi = sum(v for v in opts.call_oi if v > 0)
            pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else None

        pcr_ref = pcr_oi if pcr_oi is not None else pcr_vol
        pcr_label = "N/D"
        if pcr_ref is not None:
            for thresh, lbl in PCR_THRESHOLDS:
                if pcr_ref < thresh:
                    pcr_label = lbl
                    break
            else:
                pcr_label = PCR_LABEL_EXTREME

        return OptionsAnalysisResult(
            iv_avg=round(iv_avg, 4) if iv_avg else None,
            iv_label=iv_label,
            put_call_ratio_vol=round(pcr_vol, 4) if pcr_vol else None,
            put_call_ratio_oi=round(pcr_oi, 4) if pcr_oi else None,
            pcr_label=pcr_label,
        )

    @staticmethod
    def fundamental_score(
        fs: FinancialStatements,
        vm: ValuationMetrics,
        opts: OptionsChainSnapshot | None = None,
        market_cap: float | None = None,
        beta: float | None = None,
    ) -> FundamentalScoreResult:
        """Composite fundamental score in [-1.0, +1.0]."""
        is_t = fs.income[0] if fs.income else IncomeStatement()
        bs_t = fs.balance[0] if fs.balance else BalanceSheet()

        scores: list[float] = []

        # ── Valuation component ───────────────────────────────────────────────
        val_score = 0.0
        if vm.pe_ratio is not None:
            val_score += _clamp(-0.5 * (vm.pe_ratio - 20.0) / 20.0, -0.5, 0.5)
        if vm.pb_ratio is not None:
            val_score += _clamp(-0.3 * (vm.pb_ratio - 3.0) / 3.0, -0.3, 0.3)
        val_score = _clamp(val_score, -1.0, 1.0)
        scores.append(val_score)

        # ── Profitability component ───────────────────────────────────────────
        roa = _safe_div(is_t.net_income, bs_t.total_assets)
        profit_score = 0.0
        if roa is not None:
            profit_score = _clamp(roa * 10.0, -1.0, 1.0)  # 10% ROA → +1
        if (
            is_t.gross_profit is not None
            and is_t.total_revenue is not None
            and is_t.total_revenue > 0
        ):
            gm = is_t.gross_profit / is_t.total_revenue
            profit_score += _clamp(gm * 2.0 - 1.0, -0.5, 0.5)  # 75% GM → +0.5
        profit_score = _clamp(profit_score, -1.0, 1.0)
        scores.append(profit_score)

        # ── Financial strength component ──────────────────────────────────────
        strength_score = 0.0
        cr = _safe_div(bs_t.current_assets, bs_t.current_liabilities)
        if cr is not None:
            strength_score += _clamp((cr - 1.0), -0.5, 0.5)  # CR=2 → +0.5
        if (
            bs_t.long_term_debt is not None
            and bs_t.total_assets is not None
            and bs_t.total_assets > 0
        ):
            d_ratio = bs_t.long_term_debt / bs_t.total_assets
            strength_score += _clamp(0.5 - d_ratio, -0.5, 0.5)  # 0% debt → +0.5
        strength_score = _clamp(strength_score, -1.0, 1.0)
        scores.append(strength_score)

        # ── Options component ─────────────────────────────────────────────────
        opt_score = 0.0
        if opts is not None:
            opt_result = FundamentalValuation.options_analysis(opts)
            if opt_result.pcr_label == "BULLISH":
                opt_score = 0.5
            elif opt_result.pcr_label == "BEARISH":
                opt_score = -0.5
            elif opt_result.pcr_label == "EXTREME_FEAR":
                opt_score = -1.0
        scores.append(opt_score)

        composite = sum(scores) / len(scores)
        composite = _clamp(composite, -1.0, 1.0)

        if composite > 0.3:
            sesgo = "BULLISH"
        elif composite > 0.1:
            sesgo = "BULLISH_WATCH"
        elif composite < -0.3:
            sesgo = "BEARISH"
        elif composite < -0.1:
            sesgo = "BEARISH_WATCH"
        else:
            sesgo = "NEUTRAL"

        return FundamentalScoreResult(
            score=round(composite, 4),
            sesgo=sesgo,
            valuation_contribution=round(val_score, 4),
            profitability_contribution=round(profit_score, 4),
            financial_strength_contribution=round(strength_score, 4),
            options_contribution=round(opt_score, 4),
        )

    @staticmethod
    def graham_number(eps: float, bvps: float) -> float:
        """Graham Number = √(22.5 × EPS × BVPS). NaN when EPS ≤ 0 or BVPS ≤ 0."""
        if eps <= 0 or bvps <= 0:
            return float("nan")
        return math.sqrt(22.5 * eps * bvps)


# ─────────────────────────────────────────────────────────────────────────────
# DCFValuationModel
# ─────────────────────────────────────────────────────────────────────────────


def _compute_wacc(
    beta: float | None = None,
    tax_rate: float | None = None,
    debt_ratio: float = _DEFAULT_D_RATIO,
) -> float:
    beta_ = beta if beta is not None else _DEFAULT_BETA
    tax_ = tax_rate if tax_rate is not None else _DEFAULT_TAX
    debt_w = _clamp(debt_ratio, 0.0, 0.9)
    equity_w = 1.0 - debt_w
    ke = _RF + beta_ * _ERP
    kd = (_RF + _CREDIT_SPREAD) * (1.0 - tax_)
    return _clamp(equity_w * ke + debt_w * kd, _MIN_WACC, _MAX_WACC)


def _project_dcf(fcf_base: float, g1: float, wacc: float) -> tuple[float, float, float]:
    g1 = _clamp(g1, _MIN_G1, _MAX_G1)
    pv_fcf = 0.0
    fcf = fcf_base

    for t in range(1, 6):
        fcf = fcf * (1.0 + g1)
        pv_fcf += fcf / (1.0 + wacc) ** t

    g_fade = g1
    for t in range(6, 11):
        step = (g1 - _G_TERMINAL) / 5.0
        g_fade = g1 - step * (t - 5)
        fcf = fcf * (1.0 + _clamp(g_fade, _G_TERMINAL, _MAX_G1))
        pv_fcf += fcf / (1.0 + wacc) ** t

    if wacc <= _G_TERMINAL:
        wacc_tv = _G_TERMINAL + 0.001
    else:
        wacc_tv = wacc

    terminal = fcf * (1.0 + _G_TERMINAL) / (wacc_tv - _G_TERMINAL)
    pv_terminal = terminal / (1.0 + wacc) ** 10

    return pv_fcf, pv_terminal, fcf


class DCFValuationModel:
    """
    3-Phase DCF valuation model. All methods are pure static.
    """

    RF: float = _RF
    ERP: float = _ERP
    G_TERMINAL: float = _G_TERMINAL

    @staticmethod
    def wacc(
        beta: float | None = None,
        tax_rate: float | None = None,
        debt_ratio: float = _DEFAULT_D_RATIO,
    ) -> float:
        """WACC = w_E × Ke + w_D × Kd_at. Clipped to [4%, 20%]."""
        return _compute_wacc(beta, tax_rate, debt_ratio)

    @staticmethod
    def graham_number(eps: float, bvps: float) -> float:
        """Graham Number = √(22.5 × EPS × BVPS)."""
        if eps <= 0 or bvps <= 0:
            return float("nan")
        return math.sqrt(22.5 * eps * bvps)

    @staticmethod
    def terminal_value(fcf_year_10: float, wacc: float) -> float:
        """Gordon Growth Model terminal value."""
        effective_wacc = max(wacc, _G_TERMINAL + 0.001)
        return fcf_year_10 * (1.0 + _G_TERMINAL) / (effective_wacc - _G_TERMINAL)

    @staticmethod
    def margin_of_safety(intrinsic_value: float, current_price: float) -> float | None:
        """MoS % = (IV − Price) / IV × 100."""
        if intrinsic_value <= 0:
            return None
        return (intrinsic_value - current_price) / intrinsic_value * 100.0

    @staticmethod
    def dcf_intrinsic_value(
        fcf: float,
        shares: float,
        net_debt: float = 0.0,
        beta: float | None = None,
        tax_rate: float | None = None,
        debt_ratio: float = _DEFAULT_D_RATIO,
        sector: str | None = None,
        g1_override: float | None = None,
    ) -> tuple[float, float, float]:
        """
        Compute DCF intrinsic value per share.

        Returns:
            (intrinsic_value_per_share, wacc_used, g1_used)
        """
        wacc_val = _compute_wacc(beta, tax_rate, debt_ratio)
        g1 = (
            g1_override
            if g1_override is not None
            else _SECTOR_DEFAULTS.get(sector or "", _DEFAULT_G1)
        )

        pv_fcf, pv_tv, _ = _project_dcf(fcf, g1, wacc_val)
        equity_value = pv_fcf + pv_tv - net_debt
        iv_per_share = equity_value / shares if shares > 0 else float("nan")

        return iv_per_share, wacc_val, g1

    @staticmethod
    def value(
        ticker: str,
        current_price: float,
        fcf: float,
        shares: float,
        net_debt: float = 0.0,
        eps: float | None = None,
        bvps: float | None = None,
        beta: float | None = None,
        tax_rate: float | None = None,
        debt_ratio: float = _DEFAULT_D_RATIO,
        sector: str | None = None,
        cedear_ratio: int | None = None,
        ccl_rate: float | None = None,
    ) -> DCFOutput:
        """Full DCF valuation with Graham Number and optional CEDEAR ARS conversion."""
        wacc_val = _compute_wacc(beta, tax_rate, debt_ratio)
        g1 = _SECTOR_DEFAULTS.get(sector or "", _DEFAULT_G1)
        pv_fcf, pv_tv, fcf_10 = _project_dcf(fcf, g1, wacc_val)
        equity_value = pv_fcf + pv_tv - net_debt
        iv_per_share = equity_value / shares if shares > 0 else float("nan")

        mos = None
        if math.isfinite(iv_per_share) and iv_per_share > 0:
            mos = (iv_per_share - current_price) / iv_per_share * 100.0

        bias = "N/D"
        if mos is not None:
            if mos >= _MOS_BULLISH:
                bias = "BULLISH"
            elif mos >= _MOS_BULLISH_WATCH:
                bias = "BULLISH_WATCH"
            elif mos <= _MOS_BEARISH:
                bias = "BEARISH"
            elif mos <= _MOS_BEARISH_WATCH:
                bias = "BEARISH_WATCH"
            else:
                bias = "NEUTRAL"

        gn = DCFValuationModel.graham_number(eps or 0.0, bvps or 0.0)

        cedear_iv_ars = None
        if cedear_ratio and ccl_rate and math.isfinite(iv_per_share) and iv_per_share > 0:
            cedear_iv_ars = (iv_per_share / cedear_ratio) * ccl_rate

        return DCFOutput(
            ticker=ticker,
            intrinsic_value=round(iv_per_share, 4) if math.isfinite(iv_per_share) else None,
            margin_of_safety_pct=round(mos, 4) if mos is not None else None,
            bias=bias,
            wacc_used=round(wacc_val, 6),
            g1_used=round(g1, 6),
            pv_fcf=round(pv_fcf, 2),
            pv_terminal=round(pv_tv, 2),
            equity_value=round(equity_value, 2),
            graham_number=round(gn, 4) if math.isfinite(gn) else None,
            cedear_iv_ars=round(cedear_iv_ars, 4) if cedear_iv_ars else None,
        )

    @staticmethod
    def analyze(fundamentals: dict) -> object:
        """
        Orchestrator-compatible entry point.

        Converts a yfinance fundamentals dict and returns a SimpleNamespace
        whose attributes match what the Orchestrator extracts via getattr():
          .fair_value, .current_price, .margin_of_safety, .upside_pct,
          .bear_value, .base_value, .bull_value, .terminal_value_pv,
          .pv_fcfs, .wacc, .verdict, .ev_ebitda, .p_fcf, .p_e
        """
        from types import SimpleNamespace

        _NEUTRAL = SimpleNamespace(
            fair_value=0.0,
            current_price=0.0,
            margin_of_safety=0.0,
            upside_pct=0.0,
            bear_value=0.0,
            base_value=0.0,
            bull_value=0.0,
            terminal_value_pv=0.0,
            pv_fcfs=[],
            wacc=0.10,
            verdict="N/A",
            ev_ebitda=None,
            p_fcf=None,
            p_e=None,
        )

        if not fundamentals:
            return _NEUTRAL

        current_price = fundamentals.get("current_price") or 0.0
        fcf = fundamentals.get("free_cash_flow")
        shares = fundamentals.get("shares_outstanding")

        if not fcf or not shares or not current_price or float(current_price) <= 0:
            _NEUTRAL.current_price = float(current_price)
            return _NEUTRAL

        try:
            out = DCFValuationModel.value(
                ticker=str(fundamentals.get("ticker", "N/A")),
                current_price=float(current_price),
                fcf=float(fcf),
                shares=float(shares),
                beta=fundamentals.get("beta"),
                sector=fundamentals.get("sector"),
                eps=fundamentals.get("eps_ttm"),
                bvps=fundamentals.get("book_value"),
            )
        except Exception:
            _NEUTRAL.current_price = float(current_price)
            return _NEUTRAL

        iv = out.intrinsic_value or 0.0
        mos_pct = out.margin_of_safety_pct or 0.0
        mos_dec = mos_pct / 100.0

        ebitda = fundamentals.get("ebitda")
        market_cap = fundamentals.get("market_cap")
        eps_ttm = fundamentals.get("eps_ttm")

        ev_ebitda = None
        if ebitda and market_cap and float(ebitda) > 0:
            ev_ebitda = float(market_cap) / float(ebitda)

        pe = None
        if eps_ttm and float(eps_ttm) > 0 and current_price:
            pe = float(current_price) / float(eps_ttm)

        pfcf = None
        fcf_per_share = float(fcf) / float(shares) if float(shares) > 0 else 0.0
        if fcf_per_share > 0 and float(current_price) > 0:
            pfcf = float(current_price) / fcf_per_share

        return SimpleNamespace(
            fair_value=float(iv),
            current_price=float(current_price),
            margin_of_safety=mos_dec,
            upside_pct=max(0.0, mos_dec),
            bear_value=iv * 0.70,
            base_value=iv,
            bull_value=iv * 1.50,
            terminal_value_pv=out.pv_terminal,
            pv_fcfs=[out.pv_fcf],
            wacc=out.wacc_used,
            verdict=out.bias,
            ev_ebitda=ev_ebitda,
            p_fcf=pfcf,
            p_e=pe,
        )

    # ════════════════════════════════
    @staticmethod
    def analyze_financial_statements(
        statements: FinancialStatements,
        current_price: float,
        beta: float | None = None,
        sector: str | None = None,
    ) -> object | None:
        """
        Additive adapter: run valuation using full FinancialStatements
        contracts instead of reduced fundamentals dict proxies.
        """
        from types import SimpleNamespace

        try:
            price = float(current_price)
            if not math.isfinite(price) or price <= 0.0:
                return None

            is_t = statements.income[0] if statements.income else IncomeStatement()
            bs_t = statements.balance[0] if statements.balance else BalanceSheet()
            cf_t = statements.cashflow[0] if statements.cashflow else CashFlowStatement()

            fcf = cf_t.free_cash_flow
            if fcf is None and cf_t.operating_cash_flow is not None and cf_t.capex is not None:
                fcf = cf_t.operating_cash_flow - abs(cf_t.capex)

            shares = is_t.shares_outstanding
            if fcf is None or shares is None:
                return None

            fcf_val = float(fcf)
            shares_val = float(shares)
            if not math.isfinite(fcf_val) or not math.isfinite(shares_val) or shares_val <= 0.0:
                return None

            eps = None
            if is_t.net_income is not None:
                net_income = float(is_t.net_income)
                if math.isfinite(net_income):
                    eps = net_income / shares_val

            bvps = None
            if bs_t.book_value_equity is not None:
                book_value_equity = float(bs_t.book_value_equity)
                if math.isfinite(book_value_equity):
                    bvps = book_value_equity / shares_val

            debt_current = float(bs_t.current_liabilities or 0.0)
            debt_long = float(bs_t.long_term_debt or 0.0)
            cash = float(bs_t.cash or 0.0)
            net_debt = (debt_current + debt_long) - cash

            out = DCFValuationModel.value(
                ticker=statements.ticker or "N/A",
                current_price=price,
                fcf=fcf_val,
                shares=shares_val,
                net_debt=net_debt,
                eps=eps,
                bvps=bvps,
                beta=beta,
                sector=sector,
            )

            iv = out.intrinsic_value or 0.0
            mos_pct = out.margin_of_safety_pct or 0.0
            mos_dec = mos_pct / 100.0

            ebitda_proxy = is_t.ebit if is_t.ebit is not None else is_t.operating_income
            market_cap = bs_t.market_cap
            ev_ebitda = None
            if ebitda_proxy is not None and market_cap is not None:
                ebitda_proxy_val = float(ebitda_proxy)
                market_cap_val = float(market_cap)
                if (
                    ebitda_proxy_val > 0.0
                    and math.isfinite(ebitda_proxy_val)
                    and math.isfinite(market_cap_val)
                ):
                    ev_ebitda = market_cap_val / ebitda_proxy_val

            pe = None
            if eps is not None and eps > 0.0:
                pe = price / eps

            pfcf = None
            fcf_per_share = fcf_val / shares_val
            if fcf_per_share > 0.0:
                pfcf = price / fcf_per_share

            return SimpleNamespace(
                fair_value=float(iv),
                current_price=price,
                margin_of_safety=mos_dec,
                upside_pct=max(0.0, mos_dec),
                bear_value=iv * 0.70,
                base_value=iv,
                bull_value=iv * 1.50,
                terminal_value_pv=out.pv_terminal,
                pv_fcfs=[out.pv_fcf],
                wacc=out.wacc_used,
                verdict=out.bias,
                ev_ebitda=ev_ebitda,
                p_fcf=pfcf,
                p_e=pe,
            )
        except Exception:
            return None


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: valuation.py
# Eliminado: encabezado y referencias de import del sistema anterior
# Preservado: DCF bear/base/bull, fair value, margin of safety, Graham Number y scoring fundamental
# Pendientes: ninguno
# ─────────────────────────────────────────────────
