"""Config institucional Alpaca: pre-trade gates, Elite orders, feature flags. # [PD-8][TH]"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AlpacaEliteAlgorithm = Literal["DMA", "VWAP", "TWAP"]
AlpacaDmaDestination = Literal["NYSE", "NASDAQ", "ARCA"]
BufferZone = Literal["GREEN", "YELLOW", "RED"]


class AlpacaPreTradeLimits(BaseModel):
    """Límites pre-trade autoritativos (hot-path gate)."""

    model_config = ConfigDict(frozen=True)

    max_position_notional_usd: float = Field(default=10_000.0, gt=0)
    max_order_notional_usd: float = Field(default=5_000.0, gt=0)
    max_open_positions: int = Field(default=5, ge=1)
    order_rate_limit_per_minute: int = Field(default=10, ge=1)
    kill_switch: bool = False
    bur_yellow_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    bur_red_threshold: float = Field(default=0.8, ge=0.0, le=1.0)

    @classmethod
    def from_env(cls) -> AlpacaPreTradeLimits:
        return cls(
            max_position_notional_usd=float(
                os.getenv("ALPACA_MAX_POSITION_NOTIONAL_USD", "10000.0")
            ),
            max_order_notional_usd=float(os.getenv("ALPACA_MAX_ORDER_NOTIONAL_USD", "5000.0")),
            max_open_positions=int(os.getenv("ALPACA_MAX_OPEN_POSITIONS", "5")),
            order_rate_limit_per_minute=int(os.getenv("ALPACA_ORDER_RATE_LIMIT_PER_MIN", "10")),
            kill_switch=os.getenv("ALPACA_KILL_SWITCH", "").lower() in {"1", "true", "yes"},
            bur_yellow_threshold=float(os.getenv("ALPACA_BUR_YELLOW_THRESHOLD", "0.5")),
            bur_red_threshold=float(os.getenv("ALPACA_BUR_RED_THRESHOLD", "0.8")),
        )


class AlpacaEliteOrderConfig(BaseModel):
    """Configuración de órdenes avanzadas Alpaca Elite (DMA/VWAP/TWAP)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    algorithm: AlpacaEliteAlgorithm = "VWAP"
    destination: AlpacaDmaDestination = "NASDAQ"
    display_qty: int | None = None
    start_time_iso: str | None = None
    end_time_iso: str | None = None
    min_notional_for_elite_usd: float = Field(default=2_500.0, gt=0)

    @classmethod
    def from_env(cls) -> AlpacaEliteOrderConfig:
        elite = os.getenv("ALPACA_ELITE_SMART_ROUTER", "").lower() in {
            "1",
            "true",
            "yes",
        }
        algo = os.getenv("ALPACA_ELITE_ALGORITHM", "VWAP").upper()
        if algo not in {"DMA", "VWAP", "TWAP"}:
            algo = "VWAP"
        dest = os.getenv("ALPACA_ELITE_DMA_DESTINATION", "NASDAQ").upper()
        if dest not in {"NYSE", "NASDAQ", "ARCA"}:
            dest = "NASDAQ"
        display_raw = os.getenv("ALPACA_ELITE_DMA_DISPLAY_QTY", "").strip()
        display_qty = int(display_raw) if display_raw.isdigit() else None
        return cls(
            enabled=elite,
            algorithm=algo,  # type: ignore[arg-type]
            destination=dest,  # type: ignore[arg-type]
            display_qty=display_qty,
            start_time_iso=os.getenv("ALPACA_ELITE_START_TIME") or None,
            end_time_iso=os.getenv("ALPACA_ELITE_END_TIME") or None,
            min_notional_for_elite_usd=float(os.getenv("ALPACA_ELITE_MIN_NOTIONAL_USD", "2500.0")),
        )


def ml_direction_classifier_enabled() -> bool:
    return os.getenv("ALPACA_ML_DIRECTION_CLASSIFIER", "").lower() in {
        "1",
        "true",
        "yes",
    }


def ivpin_enabled() -> bool:
    return os.getenv("ALPACA_IVPIN_ENABLED", "1").lower() not in {"0", "false", "no"}


__all__ = [
    "AlpacaDmaDestination",
    "AlpacaEliteAlgorithm",
    "AlpacaEliteOrderConfig",
    "AlpacaPreTradeLimits",
    "BufferZone",
    "ivpin_enabled",
    "ml_direction_classifier_enabled",
]
