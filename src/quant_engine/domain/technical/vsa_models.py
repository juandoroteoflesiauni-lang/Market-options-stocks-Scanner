"""Contratos de Dominio para el Motor VSA (Volume Spread Analysis) — Sector Técnico.

Define las etiquetas de Tom Williams, sesgos direccionales, constantes de señalización
y estructuras de datos por barra y consolidado de anomalías de volumen/precio.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VSALabel(StrEnum):
    """Las seis etiquetas canónicas de Tom Williams para barras VSA."""

    STOPPING_VOLUME = "STOPPING_VOLUME"  # Sellers agotados; senal bullish
    CLIMAX_BUY = "CLIMAX_BUY"  # Distribucion/top; senal bearish
    CLIMAX_SELL = "CLIMAX_SELL"  # Capitulacion/bottom; senal bullish
    NO_SUPPLY = "NO_SUPPLY"  # Sin oferta tras caida; senal bullish
    NO_DEMAND = "NO_DEMAND"  # Sin demanda tras rally; senal bearish
    EFFORT_VS_RESULT = "EFFORT_VS_RESULT"  # Distribucion encubierta; bearish
    NORMAL = "NORMAL"


class DirectionalBias(StrEnum):
    """Sesgo direccional VSA."""

    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    CASH = "cash"


BULLISH_TRIGGER_LABELS: frozenset[VSALabel] = frozenset(
    {
        VSALabel.STOPPING_VOLUME,
        VSALabel.CLIMAX_SELL,
        VSALabel.NO_SUPPLY,
    }
)

BEARISH_TRIGGER_LABELS: frozenset[VSALabel] = frozenset(
    {
        VSALabel.CLIMAX_BUY,
        VSALabel.NO_DEMAND,
        VSALabel.EFFORT_VS_RESULT,
    }
)

BULLISH_LABELS = BULLISH_TRIGGER_LABELS
BEARISH_LABELS = BEARISH_TRIGGER_LABELS
INTERCEPTED_LABELS = BEARISH_TRIGGER_LABELS


class VSABarResult(BaseModel):
    """Resultado VSA para una única barra OHLCV."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    index: int
    label: VSALabel
    vz_score: float = Field(description="Z-score de volumen")
    absorption_index: float = Field(description="Índice de absorción")
    a_index_zscore: float = Field(description="Z-score del índice de absorción")
    relative_position: float = Field(description="Posición relativa (A_index Paper)")
    close_location: float = Field(description="Ubicación del cierre [0, 1]")
    spread_pct: float = Field(description="Spread porcentual (H-L)/C")
    is_bullish_candle: bool
    is_anomalous_absorption: bool = Field(description="Flag de absorción anómala")
    is_buying_climax: bool = Field(description="Flag de clímax de compra")
    mfi_kinetic: float | None = None
    weis_wave_volume: float | None = None
    weis_wave_direction: int | None = None


class VSAResult(BaseModel):
    """Resultado consolidado del motor VSA por ticker/timeframe."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    timeframe: str
    timestamp: datetime

    signal: DirectionalBias
    recent_labels: list[VSALabel] = Field(description="Etiquetas de las últimas 5 barras")
    direction: Literal["LONG", "SHORT", "NEUTRAL"] = "NEUTRAL"

    # Métricas de la última barra
    last_vz_score: float = 0.0
    last_absorption_index: float = 0.0
    last_a_index_zscore: float = 0.0
    last_relative_position: float = 0.0
    last_mfi_kinetic: float | None = None
    last_close_location: float = 0.5
    last_spread_pct: float = 0.0
    last_weis_wave_volume: float | None = None
    last_weis_wave_direction: int | None = None

    # Contadores de observabilidad
    bullish_signals_count: int = 0
    intercepted_bearish_count: int = 0
    bearish_label_count: int = 0

    # Flags operativos
    is_absorption_active: bool = False
    is_buying_climax_active: bool = False
    long_signal_active: bool = Field(default=False, description="Señal 0DTE Long activa")
    short_0dte: bool = Field(default=False, description="Señal 0DTE Short activa")

    # Resultados por barra (opcional)
    bar_results: list[VSABarResult] = Field(default_factory=list)

    # Score cualitativo
    composite_score: float = 0.0

    # Métricas Pro
    rvol: float = 0.0
    vol_velocity: float = 0.0
    buy_absorption: bool = False
    sell_absorption: bool = False
    effort_result_ratio: float = 0.0
    adv: float = 0.0
    weis_wave_peak: bool = False
    vfi_value: float = 0.0
    vfi_slope: float = 0.0
    is_forecast_climax: bool = False
    footprint_support: float | None = None
    footprint_resistance: float | None = None
    cvd_last: float = 0.0
    cvd_slope: float = 0.0

    error: str | None = None
    ok: bool = True

    @field_validator("signal", mode="before")
    @classmethod
    def _normalize_signal(cls, v: DirectionalBias | str) -> DirectionalBias | str:
        """Acepta valores legacy en mayusculas y valores canonicos lowercase."""
        if isinstance(v, DirectionalBias):
            return v
        if isinstance(v, str):
            key = v.upper()
            if key in DirectionalBias.__members__:
                return DirectionalBias[key]
        return v

    def model_post_init(self, __context: object) -> None:
        object.__setattr__(self, "direction", self._direction_from_signal())

    def _direction_from_signal(self) -> Literal["LONG", "SHORT", "NEUTRAL"]:
        if self.signal == DirectionalBias.BULLISH:
            return "LONG"
        if self.signal == DirectionalBias.BEARISH:
            return "SHORT"
        return "NEUTRAL"

    @property
    def short_signal_active(self) -> bool:
        return bool(self.short_0dte)
