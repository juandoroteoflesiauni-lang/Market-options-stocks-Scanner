from __future__ import annotations
"""
Domain contracts for value-creation analytics.
"""


from pydantic import BaseModel, ConfigDict


class ValueCreationInput(BaseModel):
    """Standalone ROIC/WACC calculator input."""

    model_config = ConfigDict(frozen=True)

    ebit: float | None = None
    tax_provision: float | None = None
    pretax_income: float | None = None
    interest_expense: float | None = None
    total_debt: float | None = None
    total_equity: float | None = None
    cash: float | None = None
    market_cap: float | None = None
    beta: float | None = None
    risk_free_rate: float = 0.042
    equity_risk_premium: float = 0.055
    default_beta: float = 1.0
    default_rd: float = 0.05
    default_tax_rate: float = 0.21


class ValueCreationResult(BaseModel):
    """Standalone ROIC/WACC calculator output."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    roic: float | None = None
    wacc: float | None = None
    economic_spread: float | None = None
    value_creation_label: str = "N/D"
    nopat: float | None = None
    invested_capital: float | None = None
    tax_rate_used: float | None = None
    rd_used: float | None = None
    re_used: float | None = None


__all__ = ["ValueCreationInput", "ValueCreationResult"]


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: fundamental_models.py
# Eliminado: referencias de ruta/módulo legado en docstring de encabezado
# Preservado: contratos ROIC/WACC completos (campos, defaults, tipos)
# Pendientes: ninguno
# ─────────────────────────────────────────────────
