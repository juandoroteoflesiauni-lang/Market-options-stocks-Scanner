from __future__ import annotations
"""Contratos de Dominio para el Motor SMC (Smart Money Concepts) — Sector Técnico.

Define los enums y modelos de datos (incluyendo Order Blocks, Fair Value Gaps,
eventos estructurales y modelos ICT) para el análisis de sesgo institucional.
"""


from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DirectionalBias(StrEnum):
    """Sesgo direccional bidireccional (LONG/SHORT/NEUTRAL/CASH)."""

    BULLISH = "bullish"
    BULLISH_WATCH = "bullish_watch"
    NEUTRAL = "neutral"
    BEARISH_WATCH = "bearish_watch"
    BEARISH = "bearish"
    CASH = "cash"


class ICTModelName(StrEnum):
    """Arquetipos institucionales ICT."""

    STOP_HUNT = "STOP_HUNT"
    TRAP = "TRAP"
    OTE = "OTE"
    RANGE_TRAP = "RANGE_TRAP"
    BEARISH_STOP_HUNT = "BEARISH_STOP_HUNT"
    BEARISH_BREAKER = "BEARISH_BREAKER"
    BEARISH_OTE = "BEARISH_OTE"
    BEARISH_JUDAS_SWING = "BEARISH_JUDAS_SWING"


class StructureEventType(StrEnum):
    BOS_BULL = "BOS_BULL"
    BOS_BEAR = "BOS_BEAR"
    CHOCH_BULL = "CHOCH_BULL"
    CHOCH_BEAR = "CHOCH_BEAR"


class SweepType(StrEnum):
    BSL_SWEEP = "BSL_SWEEP"
    SSL_SWEEP = "SSL_SWEEP"


class OrderBlock(BaseModel):
    """Bloque de Órdenes validado con desplazamiento institucional."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    bar_index: int
    direction: str
    high: float
    low: float
    close: float
    entry_zone: float
    delta: float
    r_wb: float
    sweep_candle: bool
    fvg_present: bool
    strength: float
    ob_50_level: float


class FairValueGap(BaseModel):
    """Desequilibrio de precio en patrón de 3 barras."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    bar_index: int
    direction: str
    top: float
    bottom: float
    size: float


class StructureEvent(BaseModel):
    """Evento de ruptura de estructura de mercado."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    bar_index: int
    event_type: str
    level: float


class LiquiditySweep(BaseModel):
    """Saneo de liquidez institucional (BSL / SSL)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    bar_index: int
    sweep_type: str
    level: float
    rvol: float


class ICTModelResult(BaseModel):
    """Resultado de detección de modelo ICT proyectado a LONG."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: ICTModelName
    confidence: float
    ote_top: float | None = None
    ote_bottom: float | None = None


class SMCResult(BaseModel):
    """Contrato de salida consolidado del motor SMC."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str = "UNKNOWN"
    timeframe: str = "UNKNOWN"

    order_blocks: list[OrderBlock] = Field(default_factory=list)
    fvg_zones: list[FairValueGap] = Field(default_factory=list)
    structure_events: list[StructureEvent] = Field(default_factory=list)
    liquidity_sweeps: list[LiquiditySweep] = Field(default_factory=list)

    sesgo: DirectionalBias = DirectionalBias.NEUTRAL

    ict_models: list[ICTModelResult] = Field(default_factory=list)
    dominant_model: ICTModelResult | None = None
    aggregate_confidence: float = 0.0

    ote_top: float | None = None
    ote_bottom: float | None = None
    key_levels: dict[str, float] = Field(default_factory=dict)

    error: str | None = None
    composite_score: float = 0.0

    @property
    def bias(self) -> str:
        if self.sesgo in (DirectionalBias.BULLISH, DirectionalBias.BULLISH_WATCH):
            return "LONG"
        if self.sesgo in (DirectionalBias.BEARISH, DirectionalBias.BEARISH_WATCH):
            return "SHORT"
        return "CASH"

    @property
    def direction(self) -> str:
        """LONG | SHORT | NEUTRAL — campo explícito derivado del sesgo."""
        if self.sesgo in (DirectionalBias.BULLISH, DirectionalBias.BULLISH_WATCH):
            return "LONG"
        if self.sesgo in (DirectionalBias.BEARISH, DirectionalBias.BEARISH_WATCH):
            return "SHORT"
        return "NEUTRAL"

    @property
    def ob_count_active(self) -> int:
        """Conteo de OB BULLISH activos."""
        return sum(1 for ob in self.order_blocks if ob.direction == "BULLISH")

    @property
    def ob_count_active_bearish(self) -> int:
        """Conteo de OB BEARISH activos (espejo bearish de ob_count_active)."""
        return sum(1 for ob in self.order_blocks if ob.direction == "BEARISH")

    @property
    def fvg_count_active(self) -> int:
        """Conteo de FVG BULLISH (compatibilidad SMCScore / confluencia opciones)."""
        return sum(1 for f in self.fvg_zones if f.direction == "BULLISH")

    @property
    def fvg_count_active_bearish(self) -> int:
        """Conteo de FVG BEARISH (espejo bearish)."""
        return sum(1 for f in self.fvg_zones if f.direction == "BEARISH")

    @property
    def choch_count(self) -> int:
        """Eventos CHOCH en la serie de estructura (cualquier dirección)."""
        return sum(1 for e in self.structure_events if "CHOCH" in str(e.event_type))

    @property
    def choch_count_bearish(self) -> int:
        """Eventos CHOCH_BEAR en la serie de estructura."""
        return sum(1 for e in self.structure_events if str(e.event_type) == "CHOCH_BEAR")

    @property
    def active_ict_model(self) -> str | None:
        """Señal tipo ICT para reglas legacy: barrido de liquidez si hay sweeps recientes."""
        if self.liquidity_sweeps:
            return "LIQUIDITY_SWEEP"
        if self.dominant_model is not None:
            return self.dominant_model.name.value
        return None

    @property
    def ok(self) -> bool:
        """El resultado es válido si tiene bias definido (incluyendo BEARISH)."""
        return self.bias != "CASH"
