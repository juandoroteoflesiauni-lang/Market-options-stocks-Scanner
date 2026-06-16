from __future__ import annotations
"""
Domain contracts for forensic accounting models.
"""


from pydantic import BaseModel, ConfigDict, Field


class AltmanForensicInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_assets: float
    current_liabilities: float
    retained_earnings: float
    ebit: float
    market_cap: float
    total_liabilities: float
    revenue: float
    total_assets: float


class AltmanForensicResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    z_score: float | None = None
    label: str | None = None
    x1: float | None = None
    x2: float | None = None
    x3: float | None = None
    x4: float | None = None
    x5: float | None = None


class PiotroskiForensicInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    net_income: float | None = None
    net_income_prev: float | None = None
    total_assets: float | None = None
    total_assets_prev: float | None = None
    operating_cash_flow: float | None = None
    revenue: float | None = None
    revenue_prev: float | None = None
    gross_profit: float | None = None
    gross_profit_prev: float | None = None
    long_term_debt: float | None = None
    long_term_debt_prev: float | None = None
    current_assets: float | None = None
    current_assets_prev: float | None = None
    current_liabilities: float | None = None
    current_liabilities_prev: float | None = None
    shares_outstanding: float | None = None
    shares_outstanding_prev: float | None = None


class PiotroskiForensicResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    f_score: int | None = None
    label: str = "N/D"
    flags: dict[str, int | None] = Field(default_factory=dict)


class BeneishForensicInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    revenue_t: float | None = None
    revenue_t1: float | None = None
    receivables_t: float | None = None
    receivables_t1: float | None = None
    gross_profit_t: float | None = None
    gross_profit_t1: float | None = None
    total_assets_t: float | None = None
    total_assets_t1: float | None = None
    current_assets_t: float | None = None
    current_assets_t1: float | None = None
    ppe_t: float | None = None
    ppe_t1: float | None = None
    depreciation_t: float | None = None
    depreciation_t1: float | None = None
    sga_t: float | None = None
    sga_t1: float | None = None
    long_term_debt_t: float | None = None
    long_term_debt_t1: float | None = None
    current_liab_t: float | None = None
    current_liab_t1: float | None = None
    net_income_t: float | None = None
    operating_cash_flow_t: float | None = None


class BeneishForensicResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    m_score: float | None = None
    label: str = "N/D"
    is_full_model: bool = False
    indices: dict[str, float | None] = Field(default_factory=dict)


class ForensicAuditEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    altman: AltmanForensicResult
    piotroski: PiotroskiForensicResult
    beneish: BeneishForensicResult
    composite_bias: str


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: forensic_models.py
# Eliminado: referencias de módulo legado en encabezado
# Preservado: contratos Altman/Piotroski/Beneish completos e inmutables
# Pendientes: ninguno
# ─────────────────────────────────────────────────
