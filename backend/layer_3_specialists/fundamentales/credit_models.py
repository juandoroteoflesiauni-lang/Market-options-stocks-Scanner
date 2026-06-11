"""
Domain contracts for fixed income and structural credit risk.
"""

from __future__ import annotations

from enum import Enum as _Enum

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ConfigDict as _ConfigDict
from pydantic import model_validator as _model_validator


class MertonResult(BaseModel):
    """Structural Credit Model results (Merton)."""

    model_config = ConfigDict(frozen=True)

    asset_value: float = Field(0.0, description="Inferred total asset value (V)")
    asset_vol: float = Field(0.0, description="Inferred asset volatility (sigma_V)")
    distance_to_default: float = Field(
        0.0,
        description="Number of standard deviations from default (DD)",
    )
    prob_default: float = Field(0.0, description="Probability of default (PD) in [0, 1]")
    implied_spread_bps: float = Field(
        0.0,
        description="Neutral credit spread in basis points",
    )
    debt_face_value: float = Field(0.0, description="Face value of debt used as strike (D)")


class DTSResult(BaseModel):
    """Duration Times Spread (DTS) metrics."""

    model_config = ConfigDict(frozen=True)

    oas_spread: float = Field(0.0, description="Option Adjusted Spread in bps")
    duration_oas: float = Field(0.0, description="Spread duration (D_OAS)")
    dts_exposure: float = Field(0.0, description="Exposure = D_OAS * OAS")
    risk_contribution: dict[str, float] = Field(
        default_factory=dict,
        description="Factor decomposition",
    )


class FixedIncomeResult(BaseModel):
    """Consolidated Fixed Income result envelope."""

    model_config = ConfigDict(frozen=True)

    merton: MertonResult | None = None
    dts: DTSResult | None = None
    composite_risk_score: float = Field(
        0.0, description="0-100 normalized risk score (lower is safer)"
    )
    verdict: str = Field("N/A", description="Institutional credit assessment")
    is_high_risk: bool = Field(False, description="PD > threshold or extreme DTS")
    error: str | None = None


class CreditRegime(str, _Enum):
    """
    Clasificación cualitativa del régimen de crédito basada en el Z-Score
    del Yield Spread histórico (corp − treasury).
    """

    NORMAL = "NORMAL"
    STRESSED = "STRESSED"
    DISTRESSED = "DISTRESSED"


class CreditRiskResult(BaseModel):
    """
    Resultado inmutable de la evaluación de riesgo de crédito por Yield Spread Z-Score.

    Complementa FixedIncomeResult (que usa el modelo estructural Merton/DTS) con
    una lectura de mercado directa del spread corp − treasury.

    Campos
    ------
    has_credit_data:
        False → el activo no tiene bonos cotizados. Todos los campos tienen
        valores neutros y credit_veto es siempre False.
    current_spread_bps:
        Spread actual (corp_yield − treasury_yield) en basis points.
    spread_z_score:
        Z = (spread_actual − μ_histórica) / σ_histórica.
        0.0 si σ ≈ 0 (spread históricamente constante).
    credit_regime:
        Clasificación cuantitativa: NORMAL | STRESSED | DISTRESSED.
    credit_veto:
        True si Z-Score > CREDIT_DISTRESSED_Z_THRESHOLD (3.0).
        Señal de VETO ABSOLUTO para el Orquestador.
    """

    model_config = _ConfigDict(frozen=True)

    has_credit_data: bool = Field(
        description="True si existen bonos corporativos cotizados para este emisor."
    )
    current_spread_bps: float = Field(
        description="Yield spread actual en basis points (corp − treasury × 10.000)."
    )
    spread_z_score: float = Field(
        description="Z-Score del spread actual respecto a la ventana histórica móvil."
    )
    credit_regime: CreditRegime = Field(
        description="Régimen de crédito: NORMAL | STRESSED | DISTRESSED."
    )
    credit_veto: bool = Field(
        description="True si Z-Score > 3.0 → veto absoluto sobre la operatoria."
    )

    @_model_validator(mode="after")
    def _validate_consistency(self: CreditRiskResult) -> CreditRiskResult:
        """
        Invariante de consistencia lógica:
        Si has_credit_data=False, credit_veto debe ser False y
        credit_regime debe ser NORMAL. Un activo sin datos de bonos
        nunca puede generar un veto.
        """

        if not self.has_credit_data:
            if self.credit_veto:
                raise ValueError("Inconsistencia: credit_veto=True requiere has_credit_data=True.")
            if self.credit_regime != CreditRegime.NORMAL:
                raise ValueError(
                    f"Inconsistencia: has_credit_data=False debe implicar "
                    f"credit_regime=NORMAL, recibido '{self.credit_regime}'."
                )
        return self


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: credit_models.py
# Eliminado: bloques de comentarios de procedencia de sistema previo
# Preservado: contratos Merton/DTS/CreditRegime/CreditRiskResult completos
# Pendientes: ninguno
# ─────────────────────────────────────────────────
