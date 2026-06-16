from __future__ import annotations
"""Order-flow delta proxy engine for the technical specialist.

The current terminal has daily/intraday OHLCV bars but not a true aggressor
tick stream. This engine provides a conservative proxy for CVD, divergences and
absorption-like bars while keeping the contract ready for a future tick adapter.
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

_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")
SwingSide = Literal["High", "Low"]


class DeltaDivergenceClass(StrEnum):
    """Classical divergence family."""

    REGULAR = "Regular"
    HIDDEN = "Hidden"


class DeltaDirection(StrEnum):
    """Directional implication of a delta signal."""

    BULLISH = "Bullish"
    BEARISH = "Bearish"


class AbsorptionSide(StrEnum):
    """Hidden-liquidity implication."""

    BULLISH = "Bullish"
    BEARISH = "Bearish"


class OrderFlowDeltaConfig(BaseModel):
    """Runtime knobs for OHLCV-proxy order-flow delta."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    swing_lookback: int = Field(default=3, ge=1, le=10)
    min_divergence_magnitude: float = Field(default=0.05, ge=0, le=1)
    absorption_zscore_threshold: float = Field(default=2.0, gt=0)
    max_history: int = Field(default=80, ge=10, le=500)
    max_events: int = Field(default=30, ge=1, le=300)


class CVDPoint(BaseModel):
    """Single CVD point derived from one OHLCV bar."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: str
    price: float
    period_delta: float
    cumulative_delta: float


class DeltaSwingPoint(BaseModel):
    """Confirmed price swing carrying CVD at the pivot."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    type: SwingSide
    timestamp: str
    price: float
    cvd_at_time: float
    confirmed_at: str


class DeltaDivergenceSignal(BaseModel):
    """Divergence between price structure and cumulative delta."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    class_: DeltaDivergenceClass = Field(alias="class")
    direction: DeltaDirection
    previous_swing: DeltaSwingPoint
    current_swing: DeltaSwingPoint
    price_delta: float
    cvd_delta: float
    divergence_magnitude: float
    timestamp: str


class AbsorptionSignal(BaseModel):
    """Absorption-like bar from extreme delta with poor price progress."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    side: AbsorptionSide
    timestamp: str
    price: float
    delta_magnitude: float
    z_score: float
    total_volume: float


class OrderFlowDeltaAnalysis(BaseModel):
    """Terminal payload produced by the order-flow delta engine."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    enabled: bool = True
    ok: bool = True
    error: str | None = None
    source: str = "ohlcv_proxy"
    latest_cvd: float = 0.0
    latest_period_delta: float = 0.0
    delta_bias: DeltaDirection | str = "Neutral"
    divergence_count: int = 0
    absorption_count: int = 0
    history: tuple[CVDPoint, ...] = ()
    swings: tuple[DeltaSwingPoint, ...] = ()
    divergences: tuple[DeltaDivergenceSignal, ...] = ()
    absorptions: tuple[AbsorptionSignal, ...] = ()


class _Bar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    delta: float
    cvd: float


class OrderFlowDeltaEngine:
    """CVD and divergence detector backed by OHLCV proxy deltas."""

    def __init__(self: OrderFlowDeltaEngine, config: OrderFlowDeltaConfig | None = None) -> None:
        self.config = config or OrderFlowDeltaConfig()
        self._bars: list[_Bar] = []
        self._delta_abs: deque[float] = deque(maxlen=120)
        self._swings: list[DeltaSwingPoint] = []
        self._divergences: list[DeltaDivergenceSignal] = []
        self._absorptions: list[AbsorptionSignal] = []
        self._cvd = 0.0
        self._last_high: DeltaSwingPoint | None = None
        self._last_low: DeltaSwingPoint | None = None

    def analyze_ohlcv_proxy(self: OrderFlowDeltaEngine, df: pd.DataFrame) -> OrderFlowDeltaAnalysis:
        """Process OHLCV bars and return compact CVD/absorption analysis."""
        try:
            frame = _validate_frame(df)
        except ValueError as exc:
            return OrderFlowDeltaAnalysis(ok=False, error=str(exc))

        for row in frame.itertuples(index=False):
            self.process_bar(
                timestamp=str(row.timestamp),
                open_price=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
        return self.build_output()

    def process_bar(
        self: OrderFlowDeltaEngine,
        *,
        timestamp: str,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> None:
        """Process one OHLCV bar using close-location value for signed delta."""
        bar_range = max(high - low, 0.0)
        clv = ((close - low) - (high - close)) / bar_range if bar_range > 0 else 0.0
        signed_delta = volume * max(min(clv, 1.0), -1.0)
        self._cvd += signed_delta
        bar = _Bar(
            timestamp=timestamp,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            delta=signed_delta,
            cvd=self._cvd,
        )
        self._bars.append(bar)
        self._detect_absorption(bar)
        self._detect_swing_if_ready()

    def build_output(self: OrderFlowDeltaEngine) -> OrderFlowDeltaAnalysis:
        """Build a compact JSON-safe payload."""
        latest = self._bars[-1] if self._bars else None
        bias: DeltaDirection | str = "Neutral"
        if latest and latest.delta > 0:
            bias = DeltaDirection.BULLISH
        elif latest and latest.delta < 0:
            bias = DeltaDirection.BEARISH

        return OrderFlowDeltaAnalysis(
            enabled=True,
            ok=True,
            source="ohlcv_proxy",
            latest_cvd=latest.cvd if latest else 0.0,
            latest_period_delta=latest.delta if latest else 0.0,
            delta_bias=bias,
            divergence_count=len(self._divergences),
            absorption_count=len(self._absorptions),
            history=tuple(
                CVDPoint(
                    timestamp=bar.timestamp,
                    price=bar.close,
                    period_delta=bar.delta,
                    cumulative_delta=bar.cvd,
                )
                for bar in self._bars[-self.config.max_history :]
            ),
            swings=tuple(self._swings[-self.config.max_events :]),
            divergences=tuple(self._divergences[-self.config.max_events :]),
            absorptions=tuple(self._absorptions[-self.config.max_events :]),
        )

    def _detect_swing_if_ready(self: OrderFlowDeltaEngine) -> None:
        k = self.config.swing_lookback
        width = (2 * k) + 1
        if len(self._bars) < width:
            return
        window = self._bars[-width:]
        center = window[k]
        left = window[:k]
        right = window[k + 1 :]
        confirmed_at = window[-1].timestamp
        if all(center.high > bar.high for bar in (*left, *right)):
            self._register_swing("High", center.timestamp, center.high, center.cvd, confirmed_at)
        if all(center.low < bar.low for bar in (*left, *right)):
            self._register_swing("Low", center.timestamp, center.low, center.cvd, confirmed_at)

    def _register_swing(
        self: OrderFlowDeltaEngine,
        swing_type: SwingSide,
        timestamp: str,
        price: float,
        cvd: float,
        confirmed_at: str,
    ) -> None:
        previous = self._last_high if swing_type == "High" else self._last_low
        swing = DeltaSwingPoint(
            type=swing_type,
            timestamp=timestamp,
            price=price,
            cvd_at_time=cvd,
            confirmed_at=confirmed_at,
        )
        self._swings.append(swing)
        if previous is not None:
            divergence = self._build_divergence(previous, swing)
            if divergence is not None:
                self._divergences.append(divergence)
        if swing_type == "High":
            self._last_high = swing
        else:
            self._last_low = swing

    def _build_divergence(
        self: OrderFlowDeltaEngine,
        previous: DeltaSwingPoint,
        current: DeltaSwingPoint,
    ) -> DeltaDivergenceSignal | None:
        price_delta = current.price - previous.price
        cvd_delta = current.cvd_at_time - previous.cvd_at_time
        cvd_range = max(
            (
                max((bar.cvd for bar in self._bars), default=0.0)
                - min((bar.cvd for bar in self._bars), default=0.0)
            ),
            1.0,
        )
        magnitude = abs(cvd_delta) / cvd_range
        if magnitude < self.config.min_divergence_magnitude:
            return None

        direction: DeltaDirection | None = None
        cls: DeltaDivergenceClass | None = None
        if current.type == "High":
            if price_delta > 0 and cvd_delta < 0:
                direction = DeltaDirection.BEARISH
                cls = DeltaDivergenceClass.REGULAR
            elif price_delta < 0 and cvd_delta > 0:
                direction = DeltaDirection.BEARISH
                cls = DeltaDivergenceClass.HIDDEN
        elif price_delta < 0 and cvd_delta > 0:
            direction = DeltaDirection.BULLISH
            cls = DeltaDivergenceClass.REGULAR
        elif price_delta > 0 and cvd_delta < 0:
            direction = DeltaDirection.BULLISH
            cls = DeltaDivergenceClass.HIDDEN

        if direction is None or cls is None:
            return None
        return DeltaDivergenceSignal(
            **{"class": cls},
            direction=direction,
            previous_swing=previous,
            current_swing=current,
            price_delta=price_delta,
            cvd_delta=cvd_delta,
            divergence_magnitude=min(magnitude, 1.0),
            timestamp=current.confirmed_at,
        )

    def _detect_absorption(self: OrderFlowDeltaEngine, bar: _Bar) -> None:
        z_score = _zscore(abs(bar.delta), self._delta_abs)
        self._delta_abs.append(abs(bar.delta))
        if z_score < self.config.absorption_zscore_threshold:
            return
        bar_range = max(bar.high - bar.low, 0.0)
        if bar_range <= 0 or bar.volume <= 0:
            return
        body_progress = abs(bar.close - bar.open) / bar_range
        if body_progress > 0.35:
            return
        side = AbsorptionSide.BEARISH if bar.delta > 0 else AbsorptionSide.BULLISH
        self._absorptions.append(
            AbsorptionSignal(
                side=side,
                timestamp=bar.timestamp,
                price=bar.close,
                delta_magnitude=bar.delta,
                z_score=z_score,
                total_volume=bar.volume,
            )
        )


def analyze_order_flow_delta_from_ohlcv(
    df: pd.DataFrame,
    config: OrderFlowDeltaConfig | None = None,
) -> OrderFlowDeltaAnalysis:
    """Convenience adapter used by the technical terminal service."""
    return OrderFlowDeltaEngine(config).analyze_ohlcv_proxy(df)


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
    if len(frame) < 8:
        raise ValueError(f"Insufficient bars ({len(frame)})")
    return frame[["timestamp", "open", "high", "low", "close", "volume"]]


def _compute_ofd_score(
    cvd_cumulative: float,
    cvd_trend: float,
    divergence_detected: bool,
    divergence_direction: str,
    absorption_detected: bool,
    absorption_side: str,
) -> float:
    """
    Score OFD simétrico [-100, +100].
    Negativo = sell pressure dominante → SHORT.
    Positivo = buy pressure dominante  → LONG.
    """
    score = 0.0

    if cvd_trend > 0:
        score += min(cvd_trend * 40.0, 40.0)
    else:
        score += max(cvd_trend * 40.0, -40.0)

    if divergence_detected:
        if divergence_direction == "bullish":
            score += 30.0
        elif divergence_direction == "bearish":
            score -= 30.0

    if absorption_detected:
        if absorption_side == "buyers":
            score += 20.0
        elif absorption_side == "sellers":
            score -= 20.0

    return float(max(-100.0, min(100.0, score)))


def _detect_bearish_absorption(
    delta_zscore: float,
    price_body_pct: float,
    volume_zscore: float,
) -> bool:
    """
    Detecta absorción BEARISH: vendedores institucionales absorben compradores.
    """
    high_buy_delta = delta_zscore > 1.5
    low_price_advance = price_body_pct < 0.25
    high_volume = volume_zscore > 1.0

    return high_buy_delta and low_price_advance and high_volume


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
