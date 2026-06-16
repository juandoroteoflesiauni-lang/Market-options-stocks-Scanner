from __future__ import annotations
"""Modelo de datos inmutable para contratos de opciones.

Define el esquema Pydantic v2 frozen para OptionContract, utilizado como
contrato de datos entre Phase C (Derivatives Engine) y el resto del pipeline.
"""


from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.market_snapshot import DataLineage


class OptionContract(BaseModel):
    """Contrato de opciones individual e inmutable.

    Representa un contrato específico con strike, expiry, tipo y Greeks calculados.
    Todos los campos son validados al momento de la construcción.
    """

    model_config = ConfigDict(frozen=True)

    # Identificación
    underlying_ticker: str
    contract_symbol: str
    strike: Decimal = Field(gt=Decimal("0"))
    expiry: date
    option_type: Literal["CALL", "PUT"]

    # Datos de mercado
    bid: Decimal = Field(ge=Decimal("0"))
    ask: Decimal = Field(ge=Decimal("0"))
    last_price: Decimal = Field(ge=Decimal("0"), default=Decimal("0"))
    volume: int = Field(ge=0)
    open_interest: int = Field(ge=0)
    implied_volatility: float = Field(ge=0.0)

    # Greeks (primer orden)
    delta: float = Field(ge=-1.0, le=1.0)
    gamma: float = Field(ge=0.0)
    theta: float
    vega: float = Field(ge=0.0)
    rho: float

    # Greeks (segundo orden)
    vanna: float = Field(default=0.0)
    charm: float = Field(default=0.0)

    # Métricas derivadas
    mid_price: Decimal = Field(default=Decimal("0"))
    spread: Decimal = Field(default=Decimal("0"))
    spread_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    moneyness: float = Field(default=0.0)
    dte: int = Field(default=0, ge=0)

    # Score compuesto de Phase C
    composite_score: float = Field(default=0.0, ge=0.0, le=100.0)

    # Linaje de datos
    data_lineage: DataLineage

    @field_validator("underlying_ticker")
    @classmethod
    def ticker_uppercase(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("underlying_ticker no puede estar vacío")
        return v.upper().strip()

    @field_validator("contract_symbol")
    @classmethod
    def symbol_uppercase(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("contract_symbol no puede estar vacío")
        return v.upper().strip()

    @property
    def is_call(self) -> bool:
        return self.option_type == "CALL"

    @property
    def is_put(self) -> bool:
        return self.option_type == "PUT"

    @property
    def is_itm(self) -> bool:
        """In-the-money check (requiere underlying_price en contexto)."""
        return False  # Se calcula externamente con spot

    @property
    def has_liquidity(self) -> bool:
        """Check básico de liquidez: volumen > 0 y spread razonable."""
        return self.volume > 0 and self.spread_pct < 0.50


class OptionChainSnapshot(BaseModel):
    """Snapshot completo de una cadena de opciones para un ticker.

    Agrupa todos los contratos disponibles para un underlying específico,
    junto con métricas agregadas de la cadena.
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    spot_price: Decimal = Field(gt=Decimal("0"))
    contracts: list[OptionContract] = Field(default_factory=list)
    total_call_volume: int = Field(default=0, ge=0)
    total_put_volume: int = Field(default=0, ge=0)
    total_call_oi: int = Field(default=0, ge=0)
    total_put_oi: int = Field(default=0, ge=0)
    put_call_ratio_volume: float = Field(default=0.0, ge=0.0)
    put_call_ratio_oi: float = Field(default=0.0, ge=0.0)
    fetch_timestamp: datetime | None = None

    @property
    def has_data(self) -> bool:
        return len(self.contracts) > 0

    @property
    def calls(self) -> list[OptionContract]:
        return [c for c in self.contracts if c.is_call]

    @property
    def puts(self) -> list[OptionContract]:
        return [c for c in self.contracts if c.is_put]


class TopOptionSelection(BaseModel):
    """Resultado de la selección de los Top N contratos por Phase C."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    selected_contracts: list[OptionContract] = Field(default_factory=list)
    selection_criteria: dict[str, float] = Field(default_factory=dict)
    engine_scores: dict[str, float] = Field(default_factory=dict)
    regime: str = "NEUTRAL"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def count(self) -> int:
        return len(self.selected_contracts)

    @property
    def has_selection(self) -> bool:
        return len(self.selected_contracts) > 0
