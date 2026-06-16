from __future__ import annotations
"""Incremental VWAP engine with volume-weighted standard deviation bands."""


from dataclasses import dataclass
from math import isfinite, sqrt

import pandas as pd
from pydantic import BaseModel, Field

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


class VWAPBands(BaseModel):
    """Symmetric VWAP standard-deviation bands."""

    upper3: float
    upper2: float
    upper1: float
    lower1: float
    lower2: float
    lower3: float


class VWAPSnapshot(BaseModel):
    """Current engine state after processing one or more price points."""

    current_vwap: float
    standard_deviation: float
    bands: VWAPBands
    cumulative_volume: float
    cumulative_pv: float
    cumulative_pv2: float
    last_timestamp: str | None = None
    tick_count: int = 0


class VWAPUpdateResult(BaseModel):
    """Update metadata plus the resulting snapshot."""

    snapshot: VWAPSnapshot
    skipped: bool = False
    session_reset: bool = False


class VWAPAnalysisOutput(BaseModel):
    """JSON-safe VWAP analysis for the technical terminal."""

    ok: bool
    error: str | None = None
    snapshot: VWAPSnapshot | None = None
    last_close: float | None = None
    price_vs_vwap: float | None = None
    price_zscore: float | None = None
    above_vwap: bool | None = None
    history: list[dict[str, float | str | int | None]] = Field(default_factory=list)


@dataclass(slots=True)
class PricePoint:
    """Atomic price point for VWAP ingestion."""

    price: float
    volume: float
    timestamp: str | None = None


@dataclass(slots=True)
class VWAPConfig:
    """Runtime configuration for VWAP bands and rounding."""

    band_multipliers: tuple[float, float, float] = (1.0, 2.0, 3.0)
    precision: int = 8
    session_duration_points: int | None = None


@dataclass(slots=True)
class _EngineState:
    sum_volume: float = 0.0
    sum_pv: float = 0.0
    sum_pv2: float = 0.0
    last_timestamp: str | None = None
    session_start_index: int | None = None
    tick_count: int = 0


class VWAPEngine:
    """O(1) incremental VWAP calculator using weighted variance identity."""

    def __init__(self: VWAPEngine, config: VWAPConfig | None = None) -> None:
        self.config = config or VWAPConfig()
        self._state = _EngineState()

    def update(self: VWAPEngine, point: PricePoint, index: int | None = None) -> VWAPUpdateResult:
        """Incorporate one price point and return the updated snapshot."""
        session_reset = False
        if not self._is_valid_point(point):
            return VWAPUpdateResult(snapshot=self.get_snapshot(), skipped=True)

        if self._should_auto_reset(index):
            self.reset(index)
            session_reset = True

        self._state.sum_volume += point.volume
        self._state.sum_pv += point.volume * point.price
        self._state.sum_pv2 += point.volume * point.price * point.price
        self._state.last_timestamp = point.timestamp
        self._state.tick_count += 1
        if self._state.session_start_index is None:
            self._state.session_start_index = index

        return VWAPUpdateResult(
            snapshot=self.get_snapshot(), skipped=False, session_reset=session_reset
        )

    def process_batch(self: VWAPEngine, points: list[PricePoint]) -> list[VWAPUpdateResult]:
        """Process points in timestamp order where timestamps are available."""
        sorted_points = sorted(points, key=lambda point: point.timestamp or "")
        return [self.update(point, idx) for idx, point in enumerate(sorted_points)]

    def get_snapshot(self: VWAPEngine) -> VWAPSnapshot:
        """Return the current snapshot without mutating engine state."""
        vwap = self._compute_vwap()
        sd = self._compute_standard_deviation(vwap)
        bands = self._compute_bands(vwap, sd)
        precision = self.config.precision
        return VWAPSnapshot(
            current_vwap=round(vwap, precision),
            standard_deviation=round(sd, precision),
            bands=bands,
            cumulative_volume=self._state.sum_volume,
            cumulative_pv=self._state.sum_pv,
            cumulative_pv2=self._state.sum_pv2,
            last_timestamp=self._state.last_timestamp,
            tick_count=self._state.tick_count,
        )

    def reset(self: VWAPEngine, session_start_index: int | None = None) -> None:
        """Reset all accumulators for a new session."""
        self._state = _EngineState(session_start_index=session_start_index)

    @staticmethod
    def from_ohlcv_frame(df: pd.DataFrame, config: VWAPConfig | None = None) -> VWAPAnalysisOutput:
        """Analyze an OHLCV DataFrame using typical price as the VWAP price input."""
        try:
            frame = _validate_ohlcv_frame(df)
            if frame.empty:
                return VWAPAnalysisOutput(ok=False, error="No valid OHLCV rows")

            engine = VWAPEngine(config)
            history: list[dict[str, float | str | int | None]] = []
            for idx, row in enumerate(frame.itertuples(index=False)):
                price = (float(row.high) + float(row.low) + float(row.close)) / 3.0
                result = engine.update(
                    PricePoint(
                        price=price,
                        volume=float(row.volume),
                        timestamp=pd.Timestamp(row.date).isoformat(),
                    ),
                    index=idx,
                )
                if not result.skipped:
                    snapshot = result.snapshot
                    history.append(
                        {
                            "time": snapshot.last_timestamp,
                            "vwap": snapshot.current_vwap,
                            "standard_deviation": snapshot.standard_deviation,
                            "upper1": snapshot.bands.upper1,
                            "upper2": snapshot.bands.upper2,
                            "upper3": snapshot.bands.upper3,
                            "lower1": snapshot.bands.lower1,
                            "lower2": snapshot.bands.lower2,
                            "lower3": snapshot.bands.lower3,
                            "tick_count": snapshot.tick_count,
                        }
                    )

            snapshot = engine.get_snapshot()
            last_close = float(frame["close"].iloc[-1])
            spread = last_close - snapshot.current_vwap
            zscore = (
                spread / snapshot.standard_deviation if snapshot.standard_deviation > 0 else 0.0
            )
            return VWAPAnalysisOutput(
                ok=snapshot.tick_count > 0,
                error=None if snapshot.tick_count > 0 else "No positive-volume price points",
                snapshot=snapshot if snapshot.tick_count > 0 else None,
                last_close=last_close if snapshot.tick_count > 0 else None,
                price_vs_vwap=spread if snapshot.tick_count > 0 else None,
                price_zscore=zscore if snapshot.tick_count > 0 else None,
                above_vwap=last_close > snapshot.current_vwap if snapshot.tick_count > 0 else None,
                history=history[-120:],
            )
        except Exception as exc:
            logger.exception("VWAP analysis failed")
            return VWAPAnalysisOutput(ok=False, error=str(exc))

    def _compute_vwap(self: VWAPEngine) -> float:
        if self._state.sum_volume <= 0:
            return 0.0
        return self._state.sum_pv / self._state.sum_volume

    def _compute_standard_deviation(self: VWAPEngine, vwap: float) -> float:
        if self._state.sum_volume <= 0:
            return 0.0
        mean_of_squares = self._state.sum_pv2 / self._state.sum_volume
        variance = mean_of_squares - vwap * vwap
        return sqrt(max(0.0, variance))

    def _compute_bands(self: VWAPEngine, vwap: float, sd: float) -> VWAPBands:
        m1, m2, m3 = self.config.band_multipliers
        precision = self.config.precision
        return VWAPBands(
            upper3=round(vwap + m3 * sd, precision),
            upper2=round(vwap + m2 * sd, precision),
            upper1=round(vwap + m1 * sd, precision),
            lower1=round(vwap - m1 * sd, precision),
            lower2=round(vwap - m2 * sd, precision),
            lower3=round(vwap - m3 * sd, precision),
        )

    def _should_auto_reset(self: VWAPEngine, incoming_index: int | None) -> bool:
        duration = self.config.session_duration_points
        start = self._state.session_start_index
        if duration is None or start is None or incoming_index is None:
            return False
        return incoming_index - start >= duration

    @staticmethod
    def _is_valid_point(point: PricePoint) -> bool:
        return (
            point.price > 0
            and point.volume > 0
            and isfinite(point.price)
            and isfinite(point.volume)
        )


class VWAPService:
    """Small multi-symbol manager for batch or streaming VWAP updates."""

    def __init__(self: VWAPService, config: VWAPConfig | None = None) -> None:
        self.config = config or VWAPConfig()
        self._engines: dict[str, VWAPEngine] = {}

    def feed(self: VWAPService, symbol: str, point: PricePoint) -> VWAPUpdateResult:
        """Feed one symbol-specific point."""
        engine = self._get_or_create_engine(symbol)
        return engine.update(point)

    def feed_batch(
        self: VWAPService, symbol: str, points: list[PricePoint]
    ) -> list[VWAPUpdateResult]:
        """Feed multiple points for one symbol."""
        engine = self._get_or_create_engine(symbol)
        return engine.process_batch(points)

    def get_snapshot(self: VWAPService, symbol: str) -> VWAPSnapshot | None:
        """Return a symbol snapshot if the engine was initialized."""
        engine = self._engines.get(symbol.upper())
        return engine.get_snapshot() if engine else None

    def reset_session(self: VWAPService, symbol: str) -> None:
        """Reset a symbol-specific engine."""
        self._get_or_create_engine(symbol).reset()

    def get_active_symbols(self: VWAPService) -> list[str]:
        """List symbols with at least one processed point."""
        return [
            symbol
            for symbol, engine in self._engines.items()
            if engine.get_snapshot().tick_count > 0
        ]

    def _get_or_create_engine(self: VWAPService, symbol: str) -> VWAPEngine:
        key = symbol.upper().strip()
        if key not in self._engines:
            self._engines[key] = VWAPEngine(self.config)
        return self._engines[key]


def analyze_vwap_from_ohlcv(df: pd.DataFrame) -> VWAPAnalysisOutput:
    """Analyze OHLCV data and return advanced VWAP state."""
    return VWAPEngine.from_ohlcv_frame(df)


def _validate_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    required = {"high", "low", "close", "volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing OHLCV columns: {', '.join(missing)}")

    frame = df.reset_index(drop=True).copy() if "date" in df.columns else df.copy()
    if "date" not in frame.columns:
        frame["date"] = pd.to_datetime(frame.index)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "high", "low", "close", "volume"])
    frame = frame[
        (frame["volume"] > 0) & (frame["high"] > 0) & (frame["low"] > 0) & (frame["close"] > 0)
    ]
    return frame.sort_values("date").reset_index(drop=True)
