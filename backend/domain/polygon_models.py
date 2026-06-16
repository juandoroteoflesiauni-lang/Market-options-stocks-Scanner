from __future__ import annotations
from typing import Any
"""
backend/domain/polygon_models.py
════════════════════════════════════════════════════════════════════════════════
Domain models for Polygon.io integration.
Compatible with Pydantic V2.
════════════════════════════════════════════════════════════════════════════════
"""



from pydantic import BaseModel, Field


class PolygonExchangeStatus(BaseModel):
    nyse: str | None = None
    nasdaq: str | None = None
    otc: str | None = None


class PolygonCurrencyStatus(BaseModel):
    crypto: str | None = None
    fx: str | None = None


class PolygonMarketStatus(BaseModel):
    market: str = Field(
        ..., description="Overall market status (e.g. open, closed, extended-hours)"
    )
    serverTime: str | None = None
    exchanges: PolygonExchangeStatus | None = None
    currencies: PolygonCurrencyStatus | None = None
    earlyHours: bool | None = None
    afterHours: bool | None = None


class PolygonSnapshotTicker(BaseModel):
    ticker: str
    todaysChangePerc: float | None = None
    todaysChange: float | None = None
    updated: int | None = None
    # Nested dicts for flexibility, though specific models could be defined for OHLCV
    day: dict[str, Any] | None = None
    min: dict[str, Any] | None = None
    prevDay: dict[str, Any] | None = None


class PolygonSnapshotResponse(BaseModel):
    status: str | None = None
    ticker: PolygonSnapshotTicker | None = None


class PolygonQuote(BaseModel):
    """
    Standardized internal Quote format for real-time market snapshots.
    """

    symbol: str
    price: float
    change_pct: float | None = None
    volume: int | None = None
    timestamp: int | None = None


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : polygon_models.py
# Sub-capa         : Domain / Contracts
# Enfoque          : Contratos Pydantic V2 para datos de mercado Polygon.
# ─────────────────────────────────────────────────────────────────────
