"""Contratos de Dominio para el Motor de Dinámica LOB (Limit Order Book) — Sector Técnico.

Define las enumeraciones, configuraciones, instantáneas, eventos y resultados
para el análisis de microestructura y detección de spoofing en tiempo real.
"""

from __future__ import annotations

from enum import IntEnum
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LOBSide(IntEnum):
    """Book side for L2 events and levels."""

    BID = 0
    ASK = 1


class LOBEventType(IntEnum):
    """Supported L2 event types."""

    ADD = 0
    CANCEL = 1
    TRADE = 2


class SpoofingState(IntEnum):
    """High-level LOB manipulation classification."""

    NORMAL = 0
    BID_SPOOFING = 1
    ASK_SPOOFING = 2


class LOBConfig(BaseModel):
    """Runtime knobs for LOB dynamics."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    depth_levels: int = Field(default=5, ge=1, le=100)
    ctr_window_ms: int = Field(default=30_000, ge=1_000, le=600_000)
    ctr_spoofing_multiplier: float = Field(default=4.0, gt=1.0)
    rho_spoofing_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    max_levels: int = Field(default=20, ge=1, le=250)


class LOBLevel(BaseModel):
    """One market-by-price level."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    price: float
    quantity: float = Field(ge=0.0)
    order_count: int | None = Field(default=None, ge=0)

    @field_validator("price", "quantity")
    @classmethod
    def _finite_float(cls: Any, value: float) -> float:
        if not isfinite(float(value)):
            raise ValueError("LOB numeric fields must be finite")
        return float(value)


class LOBSnapshot(BaseModel):
    """Full market-by-price snapshot."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: int
    bids: tuple[LOBLevel, ...] = ()
    asks: tuple[LOBLevel, ...] = ()


class LOBEvent(BaseModel):
    """Granular L2 delta event."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: int
    type: LOBEventType
    side: LOBSide
    price: float
    quantity: float = Field(ge=0.0)

    @field_validator("price", "quantity")
    @classmethod
    def _finite_float(cls: Any, value: float) -> float:
        if not isfinite(float(value)):
            raise ValueError("LOB numeric fields must be finite")
        return float(value)


class LOBDynamicsResult(BaseModel):
    """One computed LOB metrics frame."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: int
    imbalance_rho: float
    ctr_bid: float
    ctr_ask: float
    spoofing_state: SpoofingState


class LOBDynamicsAnalysis(BaseModel):
    """JSON-safe LOB engine response for routers/UI.

    ``data_quality_score`` is an optional 0.0-1.0 indicator injected by the
    upstream bridge from the raw L2 metrics. Consumers treat ``None`` as 
    ``insufficient_data`` for L2 quality.

    ``spread``, ``bid_depth``, ``ask_depth`` and ``mid_price`` are optional
    book-quality metrics bridged from the L2 provider. They are not computed 
    internally; the bridge service is the only writer. The execution policy 
    reads them to gate order entry; ``None`` means ``insufficient_data`` for 
    that metric and the execution gate degrades accordingly.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = True
    ok: bool = True
    error: str | None = None
    source: str = "l2_order_book"
    result: LOBDynamicsResult | None = None
    config: LOBConfig = Field(default_factory=LOBConfig)
    data_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    spread: float | None = Field(default=None, ge=0.0)
    bid_depth: float | None = Field(default=None, ge=0.0)
    ask_depth: float | None = Field(default=None, ge=0.0)
    mid_price: float | None = Field(default=None, gt=0.0)
    hhi_concentration: float | None = Field(default=None, ge=0.0, le=1.0)
