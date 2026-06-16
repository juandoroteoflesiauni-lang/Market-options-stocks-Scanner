from __future__ import annotations
"""Standalone ROIC/WACC/economic-spread calculator."""


from .fundamental_models import ValueCreationInput, ValueCreationResult

REALTIME_API_ENV_KEYS: tuple[str, ...] = (
    "FMP_KEY_STATEMENTS",
    "MASSIVE_KEY_FINANCIALS",
)


_RD_MIN = 0.005
_RD_MAX = 0.20
_TAX_MIN = 0.0
_TAX_MAX = 0.60
_BETA_MIN = 0.0
_BETA_MAX = 5.0


class ValueCreationCalculator:
    """Pure standalone ROIC/WACC calculator."""

    @staticmethod
    def calculate(inp: ValueCreationInput) -> ValueCreationResult:
        try:
            tax_rate = inp.default_tax_rate
            if (
                inp.tax_provision is not None
                and inp.pretax_income is not None
                and inp.pretax_income > 0
            ):
                effective_tax = inp.tax_provision / inp.pretax_income
                if _TAX_MIN <= effective_tax <= _TAX_MAX:
                    tax_rate = effective_tax

            ebit = inp.ebit
            nopat: float | None = None
            if ebit is not None:
                nopat = ebit * (1.0 - tax_rate)

            invested_capital: float | None = None
            if inp.total_debt is not None and inp.total_equity is not None:
                raw_capital = inp.total_debt + inp.total_equity - (inp.cash or 0.0)
                if raw_capital > 0:
                    invested_capital = raw_capital

            roic: float | None = None
            if nopat is not None and invested_capital is not None:
                roic = nopat / invested_capital

            beta = inp.beta
            beta_used = (
                beta if beta is not None and _BETA_MIN < beta < _BETA_MAX else inp.default_beta
            )
            re_used = inp.risk_free_rate + beta_used * inp.equity_risk_premium

            rd_used = inp.default_rd
            if (
                inp.interest_expense is not None
                and inp.total_debt is not None
                and inp.total_debt > 0
            ):
                rd_calc = abs(inp.interest_expense) / inp.total_debt
                if _RD_MIN <= rd_calc <= _RD_MAX:
                    rd_used = rd_calc

            wacc: float | None = None
            if inp.market_cap is not None and inp.market_cap > 0:
                equity_value = inp.market_cap
                debt_value = inp.total_debt or 0.0
                total_value = equity_value + debt_value
                if total_value > 0:
                    weight_equity = equity_value / total_value
                    weight_debt = debt_value / total_value
                    wacc = (weight_equity * re_used) + (weight_debt * rd_used * (1.0 - tax_rate))

            spread: float | None = None
            label = "N/D"
            if roic is not None and wacc is not None:
                spread = roic - wacc
                label = "CREADOR DE VALOR" if spread > 0 else "DESTRUCTOR DE VALOR"

            return ValueCreationResult(
                ok=True,
                roic=round(roic, 4) if roic is not None else None,
                wacc=round(wacc, 4) if wacc is not None else None,
                economic_spread=round(spread, 4) if spread is not None else None,
                value_creation_label=label,
                nopat=round(nopat, 0) if nopat is not None else None,
                invested_capital=(
                    round(invested_capital, 0) if invested_capital is not None else None
                ),
                tax_rate_used=round(tax_rate, 4),
                rd_used=round(rd_used, 4),
                re_used=round(re_used, 4),
            )
        except Exception as exc:
            return ValueCreationResult(ok=False, error=f"Internal error: {exc}")

    @staticmethod
    def with_ebit_reconstruction(
        net_income: float | None,
        interest_expense: float | None,
        tax_provision: float | None,
        **kwargs: object,
    ) -> ValueCreationResult:
        ebit: float | None = None
        if net_income is not None and interest_expense is not None and tax_provision is not None:
            ebit = net_income + abs(interest_expense) + abs(tax_provision)

        inp = ValueCreationInput(
            ebit=ebit,
            interest_expense=interest_expense,
            tax_provision=tax_provision,
            **kwargs,
        )
        return ValueCreationCalculator.calculate(inp)


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: value_creation.py
# Eliminado: encabezado con referencia a sistema anterior
# Preservado: fórmulas ROIC/WACC/NOPAT/economic_spread/invested_capital y firmas públicas
# Pendientes: ninguno
# ─────────────────────────────────────────────────
