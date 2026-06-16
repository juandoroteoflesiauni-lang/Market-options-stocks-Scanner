from __future__ import annotations
from typing import Any
"""
backend/domain/primary_models.py
════════════════════════════════════════════════════════════════════════════════
Domain contracts for Primary (Matba Rofex) API (Sector: DATA).
════════════════════════════════════════════════════════════════════════════════
"""



from pydantic import BaseModel, ConfigDict, Field


class PrimaryAuthResponse(BaseModel):
    """Response from login endpoint."""

    model_config = ConfigDict(extra="ignore")
    token: str = Field(alias="X-Auth-Token")


class PrimaryInstrument(BaseModel):
    """Instrument definition from Primary."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    market_id: str = Field(alias="marketId")
    symbol: str = Field(alias="symbol")
    low_limit_price: float | None = Field(None, alias="lowLimitPrice")
    high_limit_price: float | None = Field(None, alias="highLimitPrice")
    min_price_increment: float | None = Field(None, alias="minPriceIncrement")


class PrimaryMarketDataEntry(BaseModel):
    """Single entry in market data (bid, ask, trade)."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    price: float
    size: float


class PrimaryMarketData(BaseModel):
    """Real-time market data message for an instrument."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    symbol: str
    market_id: str = Field(alias="marketId")
    bids: list[PrimaryMarketDataEntry] = Field(default_factory=list, alias="BI")
    asks: list[PrimaryMarketDataEntry] = Field(default_factory=list, alias="OF")
    last: PrimaryMarketDataEntry | None = Field(None, alias="LA")
    volume: float | None = Field(None, alias="VO")


class PrimaryWSMessage(BaseModel):
    """Generic WebSocket message from Primary."""

    model_config = ConfigDict(extra="ignore")
    type: str
    status: str = "OK"
    data: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : primary_models.py
# Sub-capa         : Modelo (Domain)
# Enfoque          : Contratos para la API Primary (Matba Rofex).
# ─────────────────────────────────────────────────────────────────────
