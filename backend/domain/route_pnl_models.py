"""Modelos PnL agregado por ruta (R1 / R2 / BingX / Options R1). # [IM][TH]"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RouteBucket = Literal["R1", "R2", "BINGX", "OPTIONS_R1"]


class RoutePnLBucket(BaseModel):
    """Métricas por bucket de ruta."""

    model_config = ConfigDict(frozen=True)

    route: RouteBucket
    trade_count: int = 0
    execution_count: int = 0
    realized_pnl_usd: float = 0.0
    notional_usd: float = 0.0
    win_count: int = 0
    loss_count: int = 0


class RoutePnLDailyPoint(BaseModel):
    """Punto diario de equity EOD por venue."""

    model_config = ConfigDict(frozen=True)

    date: str
    alpaca_equity_usd: float | None = None
    bingx_equity_usdt: float | None = None
    bingx_unrealized_usdt: float | None = None


class RoutePnLDashboardResponse(BaseModel):
    """Respuesta del dashboard PnL por ruta."""

    model_config = ConfigDict(frozen=True)

    generated_at: str
    buckets: tuple[RoutePnLBucket, ...]
    daily: tuple[RoutePnLDailyPoint, ...] = ()
    notes: tuple[str, ...] = Field(default_factory=tuple)
