"""Volume-backed market structure engine for the technical specialist."""

from __future__ import annotations

from collections import deque
from enum import StrEnum
from math import isfinite
from statistics import fmean, stdev

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


class SwingType(StrEnum):
    """Fractal pivot side."""

    SWING_HIGH = "SwingHigh"
    SWING_LOW = "SwingLow"


class MarketRegime(StrEnum):
    """Directional structure regime."""

    BULLISH = "Bullish"
    BEARISH = "Bearish"
    CONSOLIDATION = "Consolidation"


class StructureEventType(StrEnum):
    """Structure events emitted by the engine."""

    MSS_BULLISH = "MSS_Bullish"
    MSS_BEARISH = "MSS_Bearish"
    SWEEP_HIGH = "SweepHigh"
    SWEEP_LOW = "SweepLow"


class MarketStructureConfig(BaseModel):
    """Runtime knobs for fractal structure analysis."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    fractal_periods: int = Field(default=2, ge=1, le=10)
    volume_window: int = Field(default=50, ge=5, le=250)
    rvol_threshold: float = Field(default=1.5, ge=0)
    max_events: int = Field(default=40, ge=1, le=500)
    max_active_pools: int = Field(default=30, ge=1, le=200)


class LiquidityPool(BaseModel):
    """Confirmed fractal pivot monitored as resting liquidity."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    type: SwingType
    price_level: float
    timestamp: str
    is_swept: bool = False
    volume_at_creation: float = 0.0


class StructureEvent(BaseModel):
    """Compact market structure event."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: str
    type: StructureEventType
    price: float
    previous_regime: MarketRegime
    new_regime: MarketRegime
    pool_id: str
    rvol_score: float


class MarketStructureAnalysis(BaseModel):
    """Terminal payload produced by the structure engine."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = True
    ok: bool = True
    error: str | None = None
    regime: MarketRegime = MarketRegime.CONSOLIDATION
    active_pool_count: int = 0
    swept_pool_count: int = 0
    mss_count: int = 0
    sweep_count: int = 0
    latest_event: StructureEvent | None = None
    active_pools: tuple[LiquidityPool, ...] = ()
    events: tuple[StructureEvent, ...] = ()
    fractal_periods: int = 2
    rvol_threshold: float = 1.5


class _Candle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    index: int
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketStructureEngine:
    """Detects liquidity pools, sweeps and volume-confirmed MSS events."""

    def __init__(self: MarketStructureEngine, config: MarketStructureConfig | None = None) -> None:
        self.config = config or MarketStructureConfig()
        self._window: deque[_Candle] = deque(maxlen=(self.config.fractal_periods * 2) + 1)
        self._volumes: deque[float] = deque(maxlen=self.config.volume_window)
        self._active: dict[str, LiquidityPool] = {}
        self._events: list[StructureEvent] = []
        self._regime = MarketRegime.CONSOLIDATION
        self._swept_count = 0

    def process_frame(self: MarketStructureEngine, df: pd.DataFrame) -> MarketStructureAnalysis:
        """Process chronological OHLCV rows and return compact structure."""
        try:
            frame = _validate_frame(df)
        except ValueError as exc:
            return MarketStructureAnalysis(
                ok=False,
                error=str(exc),
                fractal_periods=self.config.fractal_periods,
                rvol_threshold=self.config.rvol_threshold,
            )

        for idx, row in enumerate(frame.itertuples(index=False)):
            candle = _Candle(
                index=idx,
                timestamp=str(row.timestamp),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
            self.process_candle(candle)
        return self.build_output()

    def process_candle(self: MarketStructureEngine, candle: _Candle) -> None:
        """Process one candle."""
        rvol = _zscore(candle.volume, self._volumes)
        self._evaluate_pools(candle, rvol)
        self._window.append(candle)
        if len(self._window) == self._window.maxlen:
            self._detect_center_pivot()
        self._volumes.append(candle.volume)

    def build_output(self: MarketStructureEngine) -> MarketStructureAnalysis:
        """Build a compact JSON-safe payload."""
        active = tuple(
            sorted(
                self._active.values(),
                key=lambda pool: pool.price_level,
            )[-self.config.max_active_pools :]
        )
        return MarketStructureAnalysis(
            enabled=True,
            ok=True,
            regime=self._regime,
            active_pool_count=len(active),
            swept_pool_count=self._swept_count,
            mss_count=sum(
                1
                for ev in self._events
                if ev.type in {StructureEventType.MSS_BULLISH, StructureEventType.MSS_BEARISH}
            ),
            sweep_count=sum(
                1
                for ev in self._events
                if ev.type in {StructureEventType.SWEEP_HIGH, StructureEventType.SWEEP_LOW}
            ),
            latest_event=self._events[-1] if self._events else None,
            active_pools=active,
            events=tuple(self._events[-self.config.max_events :]),
            fractal_periods=self.config.fractal_periods,
            rvol_threshold=self.config.rvol_threshold,
        )

    def _detect_center_pivot(self: MarketStructureEngine) -> None:
        candles = list(self._window)
        k = self.config.fractal_periods
        center = candles[k]
        left = candles[:k]
        right = candles[k + 1 :]

        if all(center.high > c.high for c in (*left, *right)):
            self._register_pool(SwingType.SWING_HIGH, center)
        if all(center.low < c.low for c in (*left, *right)):
            self._register_pool(SwingType.SWING_LOW, center)

    def _register_pool(self: MarketStructureEngine, swing_type: SwingType, candle: _Candle) -> None:
        price = candle.high if swing_type is SwingType.SWING_HIGH else candle.low
        pool_id = f"{swing_type.value}_{candle.index}_{candle.timestamp}"
        if pool_id in self._active:
            return
        self._active[pool_id] = LiquidityPool(
            id=pool_id,
            type=swing_type,
            price_level=price,
            timestamp=candle.timestamp,
            volume_at_creation=candle.volume,
        )
        if len(self._active) > self.config.max_active_pools * 2:
            for old_id in list(self._active)[: self.config.max_active_pools]:
                self._active.pop(old_id, None)

    def _evaluate_pools(self: MarketStructureEngine, candle: _Candle, rvol: float) -> None:
        for pool_id, pool in list(self._active.items()):
            if pool.type is SwingType.SWING_HIGH:
                wick_breach = candle.high > pool.price_level
                close_breach = candle.close > pool.price_level
                if not wick_breach:
                    continue
                if close_breach and rvol >= self.config.rvol_threshold:
                    self._emit(
                        candle,
                        StructureEventType.MSS_BULLISH,
                        pool,
                        rvol,
                        MarketRegime.BULLISH,
                    )
                    self._active.pop(pool_id, None)
                elif not pool.is_swept:
                    self._active[pool_id] = pool.model_copy(update={"is_swept": True})
                    self._swept_count += 1
                    self._emit(
                        candle,
                        StructureEventType.SWEEP_HIGH,
                        pool,
                        rvol,
                        self._regime,
                    )
            else:
                wick_breach = candle.low < pool.price_level
                close_breach = candle.close < pool.price_level
                if not wick_breach:
                    continue
                if close_breach and rvol >= self.config.rvol_threshold:
                    self._emit(
                        candle,
                        StructureEventType.MSS_BEARISH,
                        pool,
                        rvol,
                        MarketRegime.BEARISH,
                    )
                    self._active.pop(pool_id, None)
                elif not pool.is_swept:
                    self._active[pool_id] = pool.model_copy(update={"is_swept": True})
                    self._swept_count += 1
                    self._emit(
                        candle,
                        StructureEventType.SWEEP_LOW,
                        pool,
                        rvol,
                        self._regime,
                    )

    def _emit(
        self: MarketStructureEngine,
        candle: _Candle,
        event_type: StructureEventType,
        pool: LiquidityPool,
        rvol: float,
        new_regime: MarketRegime,
    ) -> None:
        previous = self._regime
        if event_type in {StructureEventType.MSS_BULLISH, StructureEventType.MSS_BEARISH}:
            self._regime = new_regime
        self._events.append(
            StructureEvent(
                timestamp=candle.timestamp,
                type=event_type,
                price=pool.price_level,
                previous_regime=previous,
                new_regime=self._regime,
                pool_id=pool.id,
                rvol_score=rvol,
            )
        )


def analyze_market_structure_from_ohlcv(
    df: pd.DataFrame,
    config: MarketStructureConfig | None = None,
) -> MarketStructureAnalysis:
    """Convenience adapter used by the technical terminal service."""
    return MarketStructureEngine(config).process_frame(df)


def _validate_frame(df: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in _REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {', '.join(missing)}")

    frame = df.copy()
    if "date" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif "timestamp" not in frame.columns:
        frame["timestamp"] = [str(i) for i in range(len(frame))]

    for col in _REQUIRED_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=list(_REQUIRED_COLUMNS)).reset_index(drop=True)
    frame = frame[
        frame[list(_REQUIRED_COLUMNS)].map(lambda value: isfinite(float(value))).all(axis=1)
    ]
    if len(frame) < 7:
        raise ValueError(f"Insufficient bars ({len(frame)})")
    return frame[["timestamp", "open", "high", "low", "close", "volume"]]


def _zscore(value: float, window: deque[float]) -> float:
    if len(window) < 2:
        return 0.0
    mean = fmean(window)
    sigma = stdev(window)
    if sigma <= 0:
        if value > mean:
            return 999.0
        if value < mean:
            return -999.0
        return 0.0
    return (value - mean) / sigma
