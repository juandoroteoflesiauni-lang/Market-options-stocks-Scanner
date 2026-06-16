from __future__ import annotations
"""
backend/domain/snapshot_models.py
════════════════════════════════════════════════════════════════════════════════
Domain contracts for deterministic trade snapshot rehydration (Sector: DATA).
════════════════════════════════════════════════════════════════════════════════
"""


from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class SnapshotOrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SnapshotTimeFrame(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class AnnotationType(str, Enum):
    BOX = "box"
    MARKER = "marker"
    HLINE = "hline"


class MarkerShape(str, Enum):
    ARROW_UP = "arrowUp"
    ARROW_DOWN = "arrowDown"
    CIRCLE = "circle"
    SQUARE = "square"
    DIAMOND = "diamond"
    FLAG_UP = "flagUp"
    FLAG_DOWN = "flagDown"


class MarkerPosition(str, Enum):
    ABOVE_BAR = "aboveBar"
    BELOW_BAR = "belowBar"
    IN_BAR = "inBar"


class SMCStructureType(str, Enum):
    BULLISH_ORDER_BLOCK = "bullish_ob"
    BEARISH_ORDER_BLOCK = "bearish_ob"
    BULLISH_FVG = "bullish_fvg"
    BEARISH_FVG = "bearish_fvg"
    BREAK_OF_STRUCTURE = "bos"
    CHANGE_OF_CHARACTER = "choch"
    LIQUIDITY_SWEEP = "liq_sweep"


class VSASignalType(str, Enum):
    CLIMACTIC_ACTION = "climactic_action"
    NO_SUPPLY = "no_supply"
    NO_DEMAND = "no_demand"
    EFFORT_VS_RESULT = "effort_vs_result"
    STOPPING_VOLUME = "stopping_volume"
    TEST = "test"


class SnapshotMarketRegime(str, Enum):
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    NEUTRAL = "neutral"
    TRANSITION = "transition"


class SnapshotOHLCVBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int = 0

    @field_validator("timestamp_utc", mode="before")
    @classmethod
    def _ensure_utc(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        raise ValueError("timestamp_utc must be datetime")


class SnapshotSMCStructure(BaseModel):
    model_config = ConfigDict(frozen=True)

    structure_type: SMCStructureType
    price_high: Decimal
    price_low: Decimal
    bar_index_start: int
    bar_index_end: int | None = None
    is_mitigated: bool = False
    mitigation_index: int | None = None
    strength_score: float = Field(default=0.5, ge=0.0, le=1.0)
    label: str | None = None


class SnapshotGEX(BaseModel):
    model_config = ConfigDict(frozen=True)

    zero_gamma_level: Decimal
    call_wall: Decimal
    put_wall: Decimal
    hv_trigger: Decimal | None = None
    net_gex_usd: Decimal = Decimal("0")
    gamma_flip_confirmed: bool = False
    gex_regime: str = "neutral"


class SnapshotRisk(BaseModel):
    model_config = ConfigDict(frozen=True)

    stop_loss_price: Decimal
    take_profit_price: Decimal
    position_size_units: Decimal
    risk_reward_ratio: float
    max_drawdown_pct: float
    account_risk_pct: float
    atr_at_execution: Decimal
    invalidation_price: Decimal | None = None


class SnapshotMacro(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_regime: SnapshotMarketRegime
    vix_level: float | None = None
    dxy_trend: str = "neutral"
    session: str = "off"
    high_impact_event: bool = False
    event_description: str | None = None
    spy_trend: str = "neutral"


class SnapshotVSASignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal_type: VSASignalType
    bar_index: int
    price: Decimal
    volume: Decimal
    note: str | None = None


class SnapshotIndicators(BaseModel):
    model_config = ConfigDict(frozen=True)

    vwap: Decimal | None = None
    vwap_upper_band: Decimal | None = None
    vwap_lower_band: Decimal | None = None
    ema_fast: Decimal | None = None
    ema_slow: Decimal | None = None
    rsi_value: float | None = None
    atr_value: Decimal | None = None
    volume_sma: Decimal | None = None
    custom_lines: dict[str, Decimal] = Field(default_factory=dict)


class TradeDNARecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: UUID
    trade_hash: str
    schema_version: str = "1.0.0"
    created_at_utc: datetime

    symbol: str
    timeframe: SnapshotTimeFrame
    order_side: SnapshotOrderSide
    execution_price: Decimal
    timestamp_utc: datetime
    execution_bar_index: int

    historical_bars: tuple[SnapshotOHLCVBar, ...]

    smc_structures: tuple[SnapshotSMCStructure, ...] = Field(default_factory=tuple)
    vsa_signals: tuple[SnapshotVSASignal, ...] = Field(default_factory=tuple)
    gex_snapshot: SnapshotGEX
    risk_snapshot: SnapshotRisk
    macro_snapshot: SnapshotMacro
    indicators: SnapshotIndicators

    @model_validator(mode="after")
    def _validate_execution_bar_index(self) -> TradeDNARecord:
        if self.execution_bar_index >= len(self.historical_bars):
            raise ValueError("execution_bar_index outside historical_bars range")
        return self


class FrozenOHLCVBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class DynamicLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_id: str
    label: str
    color: str
    line_style: str = "solid"
    line_width: int = Field(default=1, ge=1, le=4)
    price_value: float
    is_visible: bool = True
    z_index: int = 0
    tooltip: str | None = None


class BoxAnnotation(BaseModel):
    model_config = ConfigDict(frozen=True)

    annotation_id: str
    annotation_type: AnnotationType = AnnotationType.BOX
    label: str
    label_short: str
    time_start: int
    time_end: int
    price_top: float
    price_bottom: float
    fill_color: str
    border_color: str
    border_width: int = Field(default=1, ge=1, le=3)
    is_mitigated: bool = False
    opacity: float = Field(default=0.2, ge=0.0, le=1.0)
    source_snapshot: str


class MarkerAnnotation(BaseModel):
    model_config = ConfigDict(frozen=True)

    annotation_id: str
    annotation_type: AnnotationType = AnnotationType.MARKER
    time: int
    price: float
    shape: MarkerShape
    position: MarkerPosition
    color: str
    size: int = Field(default=1, ge=1, le=3)
    label: str
    tooltip: str | None = None
    source_snapshot: str


class HLineAnnotation(BaseModel):
    model_config = ConfigDict(frozen=True)

    annotation_id: str
    annotation_type: AnnotationType = AnnotationType.HLINE
    price: float
    label: str
    color: str
    line_style: str = "dashed"
    line_width: int = Field(default=1, ge=1, le=3)
    source_snapshot: str


class TradeExecutionMarker(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: int
    price: float
    side: SnapshotOrderSide
    shape: MarkerShape
    position: MarkerPosition
    color: str
    label: str
    risk_reward: float
    stop_loss: float
    take_profit: float


class ChartMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    trade_hash: str
    symbol: str
    timeframe: str
    execution_timestamp: str
    execution_price: float
    order_side: str
    rehydration_schema: str = "1.0.0"
    hash_integrity_status: str = "VERIFIED"
    total_bars: int
    total_annotations: int
    session_at_trade: str
    market_regime: str


class ChartViewModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    metadata: ChartMetadata
    historical_bars: tuple[FrozenOHLCVBar, ...]
    dynamic_lines: dict[str, DynamicLine] = Field(default_factory=dict)
    static_annotations: tuple[BoxAnnotation | MarkerAnnotation | HLineAnnotation, ...] = Field(
        default_factory=tuple
    )
    trade_marker: TradeExecutionMarker

    @computed_field

    @property
    def viewport_suggestion(self) -> dict[str, float]:
        prices: list[float] = [bar.high for bar in self.historical_bars]
        prices.extend([bar.low for bar in self.historical_bars])

        for annotation in self.static_annotations:
            if isinstance(annotation, BoxAnnotation):
                prices.append(annotation.price_top)
                prices.append(annotation.price_bottom)
            else:
                prices.append(annotation.price)

        if not prices:
            return {}

        price_min = min(prices)
        price_max = max(prices)
        padding = (price_max - price_min) * 0.05
        return {
            "price_min": round(price_min - padding, 4),
            "price_max": round(price_max + padding, 4),
        }


__all__ = [
    "AnnotationType",
    "BoxAnnotation",
    "ChartMetadata",
    "ChartViewModel",
    "DynamicLine",
    "FrozenOHLCVBar",
    "HLineAnnotation",
    "MarkerAnnotation",
    "MarkerPosition",
    "MarkerShape",
    "SMCStructureType",
    "SnapshotGEX",
    "SnapshotIndicators",
    "SnapshotMacro",
    "SnapshotMarketRegime",
    "SnapshotOHLCVBar",
    "SnapshotOrderSide",
    "SnapshotRisk",
    "SnapshotSMCStructure",
    "SnapshotTimeFrame",
    "SnapshotVSASignal",
    "TradeDNARecord",
    "TradeExecutionMarker",
    "VSASignalType",
]

# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : snapshot_models.py
# Sub-capa         : Modelo
# Enfoque          : Contratos de dominio para TradeDNA y Rehidratación.
# Eliminado        : Comentarios legacy, referencias a V1.
# Preservado       : Lógica de validación, viewport_suggestion.
# ─────────────────────────────────────────────────────────────────────
