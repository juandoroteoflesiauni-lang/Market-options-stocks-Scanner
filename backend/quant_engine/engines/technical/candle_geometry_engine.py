from __future__ import annotations
"""Candle geometry engine for the technical specialist.

Normalizes candle bodies and wicks with rolling z-scores so the terminal can
surface statistically unusual rejection, momentum and expansion candles.
"""


from collections import deque
from enum import StrEnum
from math import isfinite
from statistics import fmean, stdev
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

_REQUIRED_COLUMNS = ("open", "high", "low", "close")

CandleDirection = Literal[-1, 0, 1]


class CandleGeometryEventType(StrEnum):
    """Event labels emitted by the candle geometry engine."""

    EXTREME_UPPER_WICK = "ExtremeUpperWick"
    EXTREME_LOWER_WICK = "ExtremeLowerWick"
    EXTREME_BODY = "ExtremeBody"
    RANGE_EXPANSION = "RangeExpansion"


class CandleGeometryConfig(BaseModel):
    """Runtime knobs for rolling candle morphology."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    lookback_window: int = Field(default=20, ge=5, le=250)
    outlier_threshold: float = Field(default=2.0, gt=0)
    expansion_threshold: float = Field(default=1.8, gt=0)
    max_events: int = Field(default=40, ge=1, le=500)


class CandleGeometryVector(BaseModel):
    """Raw and normalized decomposition of a candle."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    upper_wick: float
    body: float
    lower_wick: float
    total_range: float
    upper_wick_z: float = 0.0
    body_z: float = 0.0
    lower_wick_z: float = 0.0
    relative_size: float = 0.0
    direction: CandleDirection = 0


class CandleGeometrySnapshot(BaseModel):
    """Computed geometry for one candle."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    vector: CandleGeometryVector
    is_extreme_upper_wick: bool = False
    is_extreme_body: bool = False
    is_extreme_lower_wick: bool = False
    is_range_expansion: bool = False
    is_significant: bool = False
    atr: float = 0.0
    window_count: int = 0


class CandleGeometryEvent(BaseModel):
    """Compact event suitable for chart markers."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: str
    type: CandleGeometryEventType
    price: float
    score: float
    direction: CandleDirection


class CandleGeometryAnalysis(BaseModel):
    """Terminal payload produced by the geometry engine."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = True
    ok: bool = True
    error: str | None = None
    latest: CandleGeometrySnapshot | None = None
    significant_count: int = 0
    extreme_upper_wick_count: int = 0
    extreme_lower_wick_count: int = 0
    extreme_body_count: int = 0
    range_expansion_count: int = 0
    lookback_window: int = 20
    outlier_threshold: float = 2.0
    events: tuple[CandleGeometryEvent, ...] = ()


class CandleGeometryEngine:
    """Stateful rolling z-score candle morphology engine."""

    def __init__(self: CandleGeometryEngine, config: CandleGeometryConfig | None = None) -> None:
        self.config = config or CandleGeometryConfig()
        self._bodies: deque[float] = deque(maxlen=self.config.lookback_window)
        self._upper_wicks: deque[float] = deque(maxlen=self.config.lookback_window)
        self._lower_wicks: deque[float] = deque(maxlen=self.config.lookback_window)
        self._ranges: deque[float] = deque(maxlen=self.config.lookback_window)
        self._snapshots: list[CandleGeometrySnapshot] = []
        self._events: list[CandleGeometryEvent] = []

    def process_frame(self: CandleGeometryEngine, df: pd.DataFrame) -> CandleGeometryAnalysis:
        """Process chronological OHLCV rows and return a compact analysis."""
        try:
            frame = _validate_frame(df)
        except ValueError as exc:
            return CandleGeometryAnalysis(
                ok=False,
                error=str(exc),
                lookback_window=self.config.lookback_window,
                outlier_threshold=self.config.outlier_threshold,
            )

        for row in frame.itertuples(index=False):
            self.process_bar(
                timestamp=str(row.timestamp),
                open_price=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume or 0.0),
            )
        return self.build_output()

    def process_bar(
        self: CandleGeometryEngine,
        *,
        timestamp: str,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
    ) -> CandleGeometrySnapshot:
        """Process one closed candle."""
        total_range = max(high - low, 0.0)
        body = abs(close - open_price)
        upper_wick = max(high - max(open_price, close), 0.0)
        lower_wick = max(min(open_price, close) - low, 0.0)
        direction: CandleDirection = 1 if close > open_price else -1 if close < open_price else 0

        body_z = _zscore(body, self._bodies)
        upper_z = _zscore(upper_wick, self._upper_wicks)
        lower_z = _zscore(lower_wick, self._lower_wicks)
        atr = fmean(self._ranges) if self._ranges else total_range
        relative_size = total_range / atr if atr > 0 else 0.0

        vector = CandleGeometryVector(
            upper_wick=upper_wick,
            body=body,
            lower_wick=lower_wick,
            total_range=total_range,
            upper_wick_z=upper_z,
            body_z=body_z,
            lower_wick_z=lower_z,
            relative_size=relative_size,
            direction=direction,
        )
        is_upper = upper_z >= self.config.outlier_threshold
        is_body = body_z >= self.config.outlier_threshold
        is_lower = lower_z >= self.config.outlier_threshold
        is_expansion = relative_size >= self.config.expansion_threshold
        snapshot = CandleGeometrySnapshot(
            timestamp=timestamp,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            vector=vector,
            is_extreme_upper_wick=is_upper,
            is_extreme_body=is_body,
            is_extreme_lower_wick=is_lower,
            is_range_expansion=is_expansion,
            is_significant=is_upper or is_body or is_lower or is_expansion,
            atr=atr,
            window_count=len(self._ranges),
        )

        self._snapshots.append(snapshot)
        self._append_events(snapshot)
        self._bodies.append(body)
        self._upper_wicks.append(upper_wick)
        self._lower_wicks.append(lower_wick)
        self._ranges.append(total_range)
        return snapshot

    def build_output(self: CandleGeometryEngine) -> CandleGeometryAnalysis:
        """Build a compact JSON-safe payload."""
        significant = [snap for snap in self._snapshots if snap.is_significant]
        return CandleGeometryAnalysis(
            enabled=True,
            ok=True,
            latest=self._snapshots[-1] if self._snapshots else None,
            significant_count=len(significant),
            extreme_upper_wick_count=sum(
                1 for snap in self._snapshots if snap.is_extreme_upper_wick
            ),
            extreme_lower_wick_count=sum(
                1 for snap in self._snapshots if snap.is_extreme_lower_wick
            ),
            extreme_body_count=sum(1 for snap in self._snapshots if snap.is_extreme_body),
            range_expansion_count=sum(1 for snap in self._snapshots if snap.is_range_expansion),
            lookback_window=self.config.lookback_window,
            outlier_threshold=self.config.outlier_threshold,
            events=tuple(self._events[-self.config.max_events :]),
        )

    def _append_events(self: CandleGeometryEngine, snapshot: CandleGeometrySnapshot) -> None:
        vector = snapshot.vector
        if snapshot.is_extreme_upper_wick:
            self._events.append(
                CandleGeometryEvent(
                    timestamp=snapshot.timestamp,
                    type=CandleGeometryEventType.EXTREME_UPPER_WICK,
                    price=snapshot.high,
                    score=vector.upper_wick_z,
                    direction=-1,
                )
            )
        if snapshot.is_extreme_lower_wick:
            self._events.append(
                CandleGeometryEvent(
                    timestamp=snapshot.timestamp,
                    type=CandleGeometryEventType.EXTREME_LOWER_WICK,
                    price=snapshot.low,
                    score=vector.lower_wick_z,
                    direction=1,
                )
            )
        if snapshot.is_extreme_body:
            self._events.append(
                CandleGeometryEvent(
                    timestamp=snapshot.timestamp,
                    type=CandleGeometryEventType.EXTREME_BODY,
                    price=snapshot.close,
                    score=vector.body_z,
                    direction=vector.direction,
                )
            )
        if snapshot.is_range_expansion:
            self._events.append(
                CandleGeometryEvent(
                    timestamp=snapshot.timestamp,
                    type=CandleGeometryEventType.RANGE_EXPANSION,
                    price=snapshot.close,
                    score=vector.relative_size,
                    direction=vector.direction,
                )
            )


def analyze_candle_geometry_from_ohlcv(
    df: pd.DataFrame,
    config: CandleGeometryConfig | None = None,
) -> CandleGeometryAnalysis:
    """Convenience adapter used by the technical terminal service."""
    return CandleGeometryEngine(config).process_frame(df)


def _validate_frame(df: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in _REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLC columns: {', '.join(missing)}")

    frame = df.copy()
    if "date" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif "timestamp" not in frame.columns:
        frame["timestamp"] = [str(i) for i in range(len(frame))]
    if "volume" not in frame.columns:
        frame["volume"] = 0.0

    for col in (*_REQUIRED_COLUMNS, "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=list(_REQUIRED_COLUMNS)).reset_index(drop=True)
    frame = frame[
        frame[list(_REQUIRED_COLUMNS)].map(lambda value: isfinite(float(value))).all(axis=1)
    ]
    if len(frame) < 6:
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
