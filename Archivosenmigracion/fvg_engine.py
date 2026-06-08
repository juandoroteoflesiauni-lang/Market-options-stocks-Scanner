"""Fair Value Gap engine for the technical specialist.

The engine ports the TypeScript FVG detector into the Python backend and keeps
the lifecycle logic deterministic over chronological OHLCV bars.
"""

from __future__ import annotations

from enum import StrEnum
from math import isfinite
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

_REQUIRED_COLUMNS = ("open", "high", "low", "close")


class FVGType(StrEnum):
    """Directional type of a Fair Value Gap."""

    BULLISH = "Bullish"
    BEARISH = "Bearish"


class FVGStatus(StrEnum):
    """Lifecycle state of an FVG zone."""

    ACTIVE = "Active"
    PARTIALLY_MITIGATED = "PartiallyMitigated"
    FULLY_MITIGATED = "FullyMitigated"
    INVALIDATED = "Invalidated"


class Candle(BaseModel):
    """OHLCV candle consumed by the FVG engine."""

    model_config = ConfigDict(extra="ignore")

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class FVGZone(BaseModel):
    """Fair Value Gap zone and its current mitigation cursor."""

    model_config = ConfigDict(extra="ignore")

    id: str
    creation_timestamp: str
    type: FVGType
    top_price: float
    bottom_price: float
    original_gap_size: float
    current_mitigation_level: float
    status: FVGStatus
    mitigation_pct: float = 0.0
    is_consequent_encroachment: bool = False
    is_iofed: bool = False
    mitigated_timestamp: str | None = None
    mitigated_at_index: int | None = None


class FVGConfig(BaseModel):
    """Runtime knobs for FVG detection."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    min_gap_size: float | None = Field(default=None, ge=0)
    max_active_fvgs: int = Field(default=100, ge=1)
    tick_size: float | None = Field(default=None, gt=0)
    mitigated_ttl_candles: int = Field(default=0, ge=0)


class FVGEvent(BaseModel):
    """Lifecycle event emitted by the engine."""

    model_config = ConfigDict(extra="ignore")

    type: str
    zone: FVGZone
    candle: Candle


class FVGAnalysisOutput(BaseModel):
    """Compact JSON-safe output for the technical terminal."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    ok: bool = True
    error: str | None = None
    active_count: int = 0
    history_count: int = 0
    bullish_active_count: int = 0
    bearish_active_count: int = 0
    partial_count: int = 0
    consequent_encroachment_count: int = 0
    iofed_count: int = 0
    tick_size: float | None = None
    min_gap_size: float | None = None
    active_zones: tuple[FVGZone, ...] = ()
    recent_events: tuple[FVGEvent, ...] = ()


class FVGEngine:
    """Stateful Fair Value Gap detector and mitigation tracker."""

    def __init__(self: FVGEngine, config: FVGConfig | None = None) -> None:
        self.config = config or FVGConfig()
        self._tick_size = self.config.tick_size
        self._min_gap_size = self.config.min_gap_size
        self._window: list[Candle] = []
        self._active: dict[str, FVGZone] = {}
        self._history: list[FVGZone] = []
        self._events: list[FVGEvent] = []
        self._candle_index = 0

    @property
    def tick_size(self: FVGEngine) -> float | None:
        """Return the effective tick size after ingestion."""
        return self._tick_size

    @property
    def min_gap_size(self: FVGEngine) -> float | None:
        """Return the effective minimum FVG size after ingestion."""
        return self._min_gap_size

    def load_history(self: FVGEngine, candles: list[Candle]) -> None:
        """Process historical candles in chronological order."""
        if not candles:
            return
        if self._tick_size is None:
            self._tick_size = _infer_tick_size(candles)
        if self._min_gap_size is None:
            self._min_gap_size = self._tick_size

        for candle in candles:
            self.tick(candle)

    def tick(self: FVGEngine, candle: Candle) -> None:
        """Process one closed candle."""
        self._scan_mitigation(candle)
        self._purge_expired_zones()
        self._advance_window(candle)
        self._detect_fvg(candle)
        self._candle_index += 1

    def get_active_fvgs(self: FVGEngine) -> tuple[FVGZone, ...]:
        """Return current active and partially mitigated zones."""
        return tuple(self._active.values())

    def get_history(self: FVGEngine) -> tuple[FVGZone, ...]:
        """Return closed or evicted zones."""
        return tuple(self._history)

    def get_recent_events(self: FVGEngine, limit: int = 25) -> tuple[FVGEvent, ...]:
        """Return recent lifecycle events."""
        return tuple(self._events[-limit:])

    def build_output(self: FVGEngine, recent_limit: int = 25) -> FVGAnalysisOutput:
        """Build compact terminal payload."""
        active = self.get_active_fvgs()
        return FVGAnalysisOutput(
            enabled=True,
            ok=True,
            active_count=len(active),
            history_count=len(self._history),
            bullish_active_count=sum(1 for zone in active if zone.type is FVGType.BULLISH),
            bearish_active_count=sum(1 for zone in active if zone.type is FVGType.BEARISH),
            partial_count=sum(1 for zone in active if zone.status is FVGStatus.PARTIALLY_MITIGATED),
            consequent_encroachment_count=sum(
                1 for zone in active if zone.is_consequent_encroachment
            ),
            iofed_count=sum(1 for zone in active if zone.is_iofed),
            tick_size=self._tick_size,
            min_gap_size=self._min_gap_size,
            active_zones=tuple(
                sorted(active, key=lambda zone: zone.original_gap_size, reverse=True)[:20]
            ),
            recent_events=self.get_recent_events(recent_limit),
        )

    def _advance_window(self: FVGEngine, candle: Candle) -> None:
        self._window.append(candle)
        if len(self._window) > 3:
            self._window = self._window[-3:]

    def _detect_fvg(self: FVGEngine, current: Candle) -> None:
        if len(self._window) < 3:
            return
        assert self._min_gap_size is not None
        candle_t_minus_2 = self._window[0]
        bullish_gap = current.low - candle_t_minus_2.high
        if bullish_gap > self._min_gap_size:
            self._register_fvg(
                FVGType.BULLISH,
                top_price=current.low,
                bottom_price=candle_t_minus_2.high,
                candle=current,
            )

        bearish_gap = candle_t_minus_2.low - current.high
        if bearish_gap > self._min_gap_size:
            self._register_fvg(
                FVGType.BEARISH,
                top_price=candle_t_minus_2.low,
                bottom_price=current.high,
                candle=current,
            )

    def _register_fvg(
        self: FVGEngine,
        zone_type: FVGType,
        top_price: float,
        bottom_price: float,
        candle: Candle,
    ) -> None:
        gap_size = top_price - bottom_price
        if not all(isfinite(value) for value in (top_price, bottom_price, gap_size)):
            return

        zone_id = f"{zone_type.value}-{candle.timestamp}"
        zone = FVGZone(
            id=zone_id,
            creation_timestamp=candle.timestamp,
            type=zone_type,
            top_price=top_price,
            bottom_price=bottom_price,
            original_gap_size=gap_size,
            current_mitigation_level=top_price
            if zone_type is FVGType.BULLISH
            else bottom_price,
            status=FVGStatus.ACTIVE,
        )
        zone = _refresh_zone_metrics(zone)

        if len(self._active) >= self.config.max_active_fvgs:
            oldest_key = next(iter(self._active))
            evicted = self._active.pop(oldest_key)
            self._history.append(evicted)
            self._emit("fvg:evicted", evicted, candle)

        self._active[zone_id] = zone
        self._emit("fvg:detected", zone, candle)

    def _scan_mitigation(self: FVGEngine, candle: Candle) -> None:
        for zone_id, zone in list(self._active.items()):
            if zone.status in {FVGStatus.FULLY_MITIGATED, FVGStatus.INVALIDATED}:
                continue

            previous_status = zone.status
            updated = (
                self._mitigate_bullish(zone, candle)
                if zone.type is FVGType.BULLISH
                else self._mitigate_bearish(zone, candle)
            )
            updated = _refresh_zone_metrics(updated)
            self._active[zone_id] = updated

            if updated.status is not previous_status:
                if updated.status is FVGStatus.PARTIALLY_MITIGATED:
                    self._emit("fvg:partially_mitigated", updated, candle)
                elif updated.status is FVGStatus.FULLY_MITIGATED:
                    self._emit("fvg:fully_mitigated", updated, candle)

    def _mitigate_bullish(self: FVGEngine, zone: FVGZone, candle: Candle) -> FVGZone:
        assert self._tick_size is not None
        epsilon = self._tick_size / 2.0
        if candle.low >= zone.top_price - epsilon:
            return zone

        penetration = max(candle.low, zone.bottom_price)
        current_level = min(zone.current_mitigation_level, penetration)
        if candle.low <= zone.bottom_price + epsilon:
            return zone.model_copy(
                update={
                    "current_mitigation_level": zone.bottom_price,
                    "status": FVGStatus.FULLY_MITIGATED,
                    "mitigated_timestamp": candle.timestamp,
                    "mitigated_at_index": self._candle_index,
                }
            )
        return zone.model_copy(
            update={
                "current_mitigation_level": current_level,
                "status": FVGStatus.PARTIALLY_MITIGATED,
            }
        )

    def _mitigate_bearish(self: FVGEngine, zone: FVGZone, candle: Candle) -> FVGZone:
        assert self._tick_size is not None
        epsilon = self._tick_size / 2.0
        if candle.high <= zone.bottom_price + epsilon:
            return zone

        penetration = min(candle.high, zone.top_price)
        current_level = max(zone.current_mitigation_level, penetration)
        if candle.high >= zone.top_price - epsilon:
            return zone.model_copy(
                update={
                    "current_mitigation_level": zone.top_price,
                    "status": FVGStatus.FULLY_MITIGATED,
                    "mitigated_timestamp": candle.timestamp,
                    "mitigated_at_index": self._candle_index,
                }
            )
        return zone.model_copy(
            update={
                "current_mitigation_level": current_level,
                "status": FVGStatus.PARTIALLY_MITIGATED,
            }
        )

    def _purge_expired_zones(self: FVGEngine) -> None:
        ttl = self.config.mitigated_ttl_candles
        for zone_id, zone in list(self._active.items()):
            if zone.status not in {FVGStatus.FULLY_MITIGATED, FVGStatus.INVALIDATED}:
                continue
            if zone.mitigated_at_index is None:
                continue
            if self._candle_index - zone.mitigated_at_index >= ttl:
                self._history.append(zone)
                del self._active[zone_id]

    def _emit(self: FVGEngine, event_type: str, zone: FVGZone, candle: Candle) -> None:
        self._events.append(FVGEvent(type=event_type, zone=zone, candle=candle))


def analyze_fvg_from_ohlcv(
    df: pd.DataFrame,
    config: FVGConfig | None = None,
) -> FVGAnalysisOutput:
    """Analyze OHLCV bars and return active FVG state for the terminal."""
    try:
        candles = _candles_from_frame(df)
        if len(candles) < 3:
            return FVGAnalysisOutput(ok=False, error=f"Need at least 3 candles, got {len(candles)}")
        engine = FVGEngine(config)
        engine.load_history(candles)
        return engine.build_output()
    except Exception as exc:
        logger.exception("FVG analysis failed")
        return FVGAnalysisOutput(ok=False, error=str(exc))


def _candles_from_frame(df: pd.DataFrame) -> list[Candle]:
    if df is None or df.empty:
        return []
    frame = df.copy()
    frame.columns = [str(col).lower() for col in frame.columns]
    missing = set(_REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")

    for column in (*_REQUIRED_COLUMNS, "volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=list(_REQUIRED_COLUMNS))
    frame = frame[(frame["high"] >= frame["low"]) & (frame["high"] > 0) & (frame["low"] > 0)]
    if frame.empty:
        return []

    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")
        timestamp_values = [str(pd.Timestamp(value)) for value in frame["date"]]
    elif isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.sort_index()
        timestamp_values = [str(pd.Timestamp(value)) for value in frame.index]
    else:
        frame = frame.reset_index(drop=True)
        timestamp_values = [str(index) for index in frame.index]

    candles: list[Candle] = []
    for timestamp, row in zip(timestamp_values, frame.itertuples(index=False), strict=False):
        candles.append(
            Candle(
                timestamp=timestamp,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(getattr(row, "volume", 0.0) or 0.0),
            )
        )
    return candles


def _infer_tick_size(candles: list[Candle]) -> float:
    prices: list[float] = []
    for candle in candles:
        prices.extend([candle.open, candle.high, candle.low, candle.close])
    ordered = sorted({round(price, 8) for price in prices if isfinite(price) and price > 0})
    diffs = [
        round(ordered[idx] - ordered[idx - 1], 8)
        for idx in range(1, len(ordered))
        if ordered[idx] > ordered[idx - 1]
    ]
    if diffs:
        return max(min(diffs), 1e-9)
    close = candles[-1].close
    return max(abs(close) * 0.0001, 0.0001)


def _refresh_zone_metrics(zone: FVGZone) -> FVGZone:
    pct = mitigation_pct(zone)
    return zone.model_copy(
        update={
            "mitigation_pct": pct,
            "is_consequent_encroachment": pct >= 50.0,
            "is_iofed": 0.0 < pct < 50.0,
        }
    )


def mitigation_pct(zone: FVGZone) -> float:
    """Compute FVG mitigation percentage in 0-100 scale."""
    if zone.original_gap_size <= 0:
        return 0.0
    filled = (
        zone.top_price - zone.current_mitigation_level
        if zone.type is FVGType.BULLISH
        else zone.current_mitigation_level - zone.bottom_price
    )
    return float(max(0.0, min(100.0, (filled / zone.original_gap_size) * 100.0)))
