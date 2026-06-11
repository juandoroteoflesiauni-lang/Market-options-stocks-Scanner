"""
backend/domain/market_models.py
════════════════════════════════════════════════════════════════════════════════
General domain models for Market Data.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


class OHLCVBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @field_validator("timestamp_utc", mode="before")
    @classmethod
    def _ensure_utc(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        raise ValueError("timestamp_utc must be datetime")


class FundamentalMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticker: str
    market_cap: Decimal | None = None
    pe_ratio: float | None = None
    eps_ttm: Decimal | None = None
    revenue_ttm: Decimal | None = None
    ebitda: Decimal | None = None
    book_value: Decimal | None = None
    debt_to_equity: float | None = None
    dividend_yield: float | None = None
    beta: float | None = None
    sector: str | None = None
    industry: str | None = None
    currency: str = "USD"
    current_price: Decimal | None = None


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    price: Decimal
    change_pct: float
    volume: Decimal
    timestamp_utc: datetime
    status: str = "open"


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : market_models.py
# Sub-capa         : Domain / Contracts
# Enfoque          : Modelos base para OHLCV y Fundamentales.
# ─────────────────────────────────────────────────────────────────────
