from __future__ import annotations
"""
backend/domain/data912_models.py
════════════════════════════════════════════════════════════════════════════════
Domain contracts for Data912 financial data (Sector: DATA).
════════════════════════════════════════════════════════════════════════════════
"""


import datetime as _dt

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Data912LiveQuote(BaseModel):
    """
    Live quote for an asset (Stock, Bond, Cedear) in the Argentine market.
    Includes MEP/CCL implied rates if applicable.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str = Field(validation_alias=AliasChoices("ticker", "symbol", "ticker_ar"))
    ticker_usa: str | None = Field(None, validation_alias="ticker_usa")
    bid: float | None = Field(None, validation_alias=AliasChoices("bid", "px_bid", "CCL_bid"))
    ask: float | None = Field(None, validation_alias=AliasChoices("ask", "px_ask", "CCL_ask"))
    close: float | None = Field(
        None, validation_alias=AliasChoices("close", "c", "mark", "CCL_mark", "CCL_close")
    )
    high: float | None = Field(
        None, validation_alias=AliasChoices("high", "h", "max", "day_high", "hi")
    )
    low: float | None = Field(
        None, validation_alias=AliasChoices("low", "l", "min", "day_low", "lo")
    )
    volume: float | None = Field(
        None, validation_alias=AliasChoices("v_ars", "v", "v_usd", "ars_volume")
    )
    price_ars: float | None = Field(None, validation_alias="price_ars")
    price_usd: float | None = Field(None, validation_alias="price_usd")
    pct_change: float | None = Field(None, validation_alias="pct_change")
    panel: str | None = Field(None, validation_alias="panel")
    last_update: str | None = Field(None, validation_alias="last_update")


class Data912HistoricalPoint(BaseModel):
    """
    Historical OHLCV point.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    fecha: _dt.date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None


class Data912VolatilityMetrics(BaseModel):
    """
    EOD Volatility analytics for a specific ticker.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    vol_hist_20: float | None = None
    vol_hist_40: float | None = None
    vol_hist_60: float | None = None
    implied_vol: float | None = None


class Data912OptionChainItem(BaseModel):
    """
    Individual option contract data in an option chain.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str
    kind: str  # CALL / PUT
    strike: float
    expiration: _dt.date
    bid: float | None = None
    ask: float | None = None
    close: float | None = None
    volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


class Data912WSMessage(BaseModel):
    """
    Real-time message from Data912 WebSocket.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str = Field(alias="s")
    price: float | None = Field(None, alias="p")
    size: float | None = Field(None, alias="q")
    side: str | None = Field(None, alias="side")
    timestamp: int | None = Field(None, alias="t")
    type: str = Field(alias="e")  # 'trade', 'quote', etc.


class Data912CorporateAction(BaseModel):
    """
    EOD Corporate Action (splits, mergers, etc).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    date: _dt.date
    description: str
    ratio: float | None = None


class Data912Dividend(BaseModel):
    """
    EOD Dividend data.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    ex_date: _dt.date
    payment_date: _dt.date | None = None
    amount: float
    currency: str


class Data912Earnings(BaseModel):
    """
    EOD Earnings data.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    date: _dt.date
    eps_actual: float | None = None
    eps_estimate: float | None = None
    revenue_actual: float | None = None
    revenue_estimate: float | None = None


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : data912_models.py
# Sub-capa         : Modelo (Domain)
# Enfoque          : Contratos para la API Data912 (Live & EOD).
# ─────────────────────────────────────────────────────────────────────
