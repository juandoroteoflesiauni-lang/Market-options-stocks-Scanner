from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# MIGRATION: import pendiente -> from quantumbeta import constants
# Resolver vinculación de constantes de thresholds en el nuevo sistema.
# Para validación, se asumen valores por defecto si el import falla o se comentan validaciones dependientes.


class SystemHealthStatus(str, Enum):
    """Health states for execution-quality diagnostics."""

    OPTIMAL = "OPTIMAL"
    DEGRADED = "DEGRADED"
    CRITICAL_FAILURE = "CRITICAL_FAILURE"


class StrategyKPIResult(BaseModel):
    """Immutable KPI envelope for strategy health evaluation."""

    model_config = ConfigDict(frozen=True)

    kpi_win_rate: float = Field(ge=0.0, le=1.0)
    kpi_profit_factor: float = Field(ge=0.0)
    kpi_avg_slippage_bps: float
    kpi_capital_utilization: float = Field(ge=0.0)
    kpi_signal_decay_ms: float = Field(ge=0.0)
    system_health_status: SystemHealthStatus
    is_statistically_significant: bool
    trade_sample_size: int = Field(ge=0)
    evaluation_timestamp: datetime
    diagnostic_notes: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("kpi_profit_factor")
    @classmethod
    def _validate_profit_factor(cls, value: float) -> float:
        # Check for NaN manually as Pydantic ge=0.0 might not catch it depending on version/config
        if value != value:
            raise ValueError("kpi_profit_factor cannot be NaN")
        return value

    @model_validator(mode="after")
    def _validate_health_consistency(self) -> StrategyKPIResult:
        if self.system_health_status == SystemHealthStatus.CRITICAL_FAILURE:
            # MIGRATION: Los thresholds se han hardcodeado temporalmente para mantener la lógica
            # de integridad del contrato hasta que se reconecte el archivo de constantes.
            PF_CRITICAL_THRESHOLD = 0.8
            WR_CRITICAL_THRESHOLD = 0.35
            SLIPPAGE_DEGRADED_THRESHOLD = 5.0

            pf_critical = self.kpi_profit_factor < PF_CRITICAL_THRESHOLD
            wr_critical = self.kpi_win_rate < WR_CRITICAL_THRESHOLD
            slippage_extreme = self.kpi_avg_slippage_bps > (SLIPPAGE_DEGRADED_THRESHOLD * 3.0)

            if not ((pf_critical and wr_critical) or slippage_extreme):
                raise ValueError("CRITICAL_FAILURE requires critical PF/WR or extreme slippage")
        return self


# ────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: PORTAFOLIO
# Archivo         : strategy_kpi_models.py
# Sub-capa        : Modelo
# Solver/Optimizer: N/A
# Eliminado       : Import de quantumbeta.constants (pendiente reconexión)
# Preservado      : Lógica de validación de salud de estrategia, SystemHealthStatus, StrategyKPIResult
# Pendientes      : Reconectar constants.py con thresholds institucionales
# ────────────────────────────────────────────────────────────────────
