"""Motor de Order Flow Imbalance (OFI) — Sector Técnico.

Implementa el modelo OFI de Cont, Kukanov y Stoikov (2014) con adaptador
proxy OHLCV para datos del terminal técnico.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

_DEFAULT_PRICE_EPSILON: float = 1e-9
_DEFAULT_EMA_ALPHA: float = 0.1
_DEFAULT_ACCUMULATION_THRESHOLD: float = 0.149
_DEFAULT_DISTRIBUTION_THRESHOLD: float = -0.311
_DEFAULT_WINDOW_COUNT: int = 100
_DEFAULT_MAX_HISTORY: int = 120
_REQUIRED_OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

from typing import Literal

SmoothingMode = Literal["EMA", "SUM", "NORM"]
WindowKind = Literal["TICKS", "TIME_MS"]


class L1Snapshot(BaseModel):
    """Level-1 best bid/ask snapshot."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: float
    best_bid_price: float
    best_bid_size: float
    best_ask_price: float
    best_ask_size: float


class OFIResult(BaseModel):  # type: ignore[no-redef]
    """Output produced after processing one L1 snapshot."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: float = 0.0
    raw_ofi: float = 0.0
    accumulated_ofi: float = 0.0
    regime: str = "Neutral"
    delta_bid: float = 0.0
    delta_ask: float = 0.0
    window_tick_count: int = 0


class RegimeThresholds(BaseModel):
    """Thresholds used to classify accumulated OFI."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    accumulation: float = _DEFAULT_ACCUMULATION_THRESHOLD
    distribution: float = _DEFAULT_DISTRIBUTION_THRESHOLD


class OFIEngineConfig(BaseModel):
    """Runtime configuration for the OFI engine."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    window_kind: WindowKind = "TICKS"
    window_value: int = _DEFAULT_WINDOW_COUNT
    smoothing_mode: SmoothingMode = "EMA"
    ema_alpha: float = _DEFAULT_EMA_ALPHA
    regime_thresholds: RegimeThresholds = RegimeThresholds()
    price_epsilon: float = _DEFAULT_PRICE_EPSILON


class OFIAnalysisOutput(BaseModel):  # type: ignore[no-redef]
    """Compact OFI analysis block suitable for API payloads."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ok: bool = True
    error: str | None = None
    regime: str = "Neutral"
    latest_raw_ofi: float = 0.0
    latest_accumulated_ofi: float = 0.0
    latest_delta_bid: float = 0.0
    latest_delta_ask: float = 0.0
    window_tick_count: int = 0
    history: tuple[OFIResult, ...] = ()


class OFIEngine:
    """Stateful L1 Order Flow Imbalance calculator."""

    def __init__(self, config: OFIEngineConfig | None = None) -> None:
        self.config = config or OFIEngineConfig()
        if self.config.price_epsilon <= 0:
            raise ValueError("price_epsilon must be > 0")
        if not 0 < self.config.ema_alpha <= 1:
            raise ValueError("ema_alpha must be in (0, 1]")
        if self.config.window_value < 1:
            raise ValueError("window_value must be >= 1")
        if self.config.regime_thresholds.distribution >= self.config.regime_thresholds.accumulation:
            raise ValueError("distribution threshold must be below accumulation threshold")

        self._prev_snapshot: L1Snapshot | None = None
        self._ema_value: float = 0.0
        self._ofi_ring: list[tuple[float, float, float]] = []
        self._latest = OFIResult()

    def update(self, snapshot: L1Snapshot) -> OFIResult:
        """Process a new L1 snapshot and return the current OFI result."""
        self._validate_snapshot(snapshot)
        if self._prev_snapshot is None:
            self._prev_snapshot = snapshot
            self._latest = self._write_result(snapshot.timestamp, 0.0, 0.0, 0.0, 0.0, 0)
            return self._latest

        prev = self._prev_snapshot
        delta_bid = self.compute_delta_bid(
            prev.best_bid_price,
            prev.best_bid_size,
            snapshot.best_bid_price,
            snapshot.best_bid_size,
        )
        delta_ask = self.compute_delta_ask(
            prev.best_ask_price,
            prev.best_ask_size,
            snapshot.best_ask_price,
            snapshot.best_ask_size,
        )
        raw_ofi = delta_bid - delta_ask
        accumulated, tick_count = self._accumulate(
            raw_ofi, snapshot.timestamp, abs(delta_bid) + abs(delta_ask)
        )
        self._latest = self._write_result(
            snapshot.timestamp, raw_ofi, delta_bid, delta_ask, accumulated, tick_count
        )
        self._prev_snapshot = snapshot
        return self._latest

    def reset(self) -> None:
        """Clear accumulated state."""
        self._prev_snapshot = None
        self._ema_value = 0.0
        self._ofi_ring.clear()
        self._latest = OFIResult()

    def peek(self) -> OFIResult:
        """Return the latest OFI result."""
        return self._latest

    def compute_delta_bid(
        self,
        prev_bid_price: float,
        prev_bid_size: float,
        curr_bid_price: float,
        curr_bid_size: float,
    ) -> float:
        """Bid contribution per Cont et al. equation 2."""
        diff = curr_bid_price - prev_bid_price
        if diff > self.config.price_epsilon:
            return curr_bid_size
        if diff < -self.config.price_epsilon:
            return -prev_bid_size
        return curr_bid_size - prev_bid_size

    def compute_delta_ask(
        self,
        prev_ask_price: float,
        prev_ask_size: float,
        curr_ask_price: float,
        curr_ask_size: float,
    ) -> float:
        """Ask contribution per Cont et al. equation 2."""
        diff = curr_ask_price - prev_ask_price
        if diff < -self.config.price_epsilon:
            return curr_ask_size
        if diff > self.config.price_epsilon:
            return -prev_ask_size
        return curr_ask_size - prev_ask_size

    def analyze_ohlcv_proxy(
        self,
        df: pd.DataFrame,
        max_history: int = _DEFAULT_MAX_HISTORY,
    ) -> OFIAnalysisOutput:
        """Build conservative L1 proxies from OHLCV bars and return a compact analysis."""
        try:
            frame = self._validate_ohlcv_frame(df)
            if len(frame) < 2:
                return OFIAnalysisOutput(ok=False, error=f"Insufficient bars ({len(frame)})")

            history: list[OFIResult] = []
            for idx, row in enumerate(frame.itertuples(index=False)):
                snapshot = self._snapshot_from_bar(row, idx)
                result = self.update(snapshot)
                history.append(result)

            latest = history[-1]
            tail = tuple(history[-max_history:])
            return OFIAnalysisOutput(
                ok=True,
                regime=latest.regime,
                latest_raw_ofi=round(latest.raw_ofi, 6),
                latest_accumulated_ofi=round(latest.accumulated_ofi, 6),
                latest_delta_bid=round(latest.delta_bid, 6),
                latest_delta_ask=round(latest.delta_ask, 6),
                window_tick_count=latest.window_tick_count,
                history=tail,
            )
        except Exception as exc:
            logger.warning("OFI analysis failed: %s", exc)
            return OFIAnalysisOutput(ok=False, error=str(exc))

    def _accumulate(
        self, raw_ofi: float, timestamp: float, volume_flux: float
    ) -> tuple[float, int]:
        if self.config.smoothing_mode == "EMA":
            self._ema_value = (
                self.config.ema_alpha * raw_ofi + (1 - self.config.ema_alpha) * self._ema_value
            )
            return self._ema_value, 0

        self._ofi_ring.append((timestamp, raw_ofi, volume_flux))
        self._trim_ring(timestamp)
        total_ofi = sum(item[1] for item in self._ofi_ring)
        if self.config.smoothing_mode == "NORM":
            total_flux = sum(item[2] for item in self._ofi_ring)
            total_ofi = total_ofi / total_flux if total_flux > self.config.price_epsilon else 0.0
        return total_ofi, len(self._ofi_ring)

    def _trim_ring(self, timestamp: float) -> None:
        if self.config.window_kind == "TICKS":
            overflow = len(self._ofi_ring) - self.config.window_value
            if overflow > 0:
                del self._ofi_ring[:overflow]
            return
        min_timestamp = timestamp - self.config.window_value
        while self._ofi_ring and self._ofi_ring[0][0] < min_timestamp:
            self._ofi_ring.pop(0)

    def _classify_regime(self, accumulated: float) -> str:
        thresholds = self.config.regime_thresholds
        if accumulated > thresholds.accumulation:
            return "StrongAccumulation"
        if accumulated < thresholds.distribution:
            return "StrongDistribution"
        return "Neutral"

    def _write_result(
        self,
        timestamp: float,
        raw_ofi: float,
        delta_bid: float,
        delta_ask: float,
        accumulated: float,
        window_tick_count: int,
    ) -> OFIResult:
        return OFIResult(
            timestamp=timestamp,
            raw_ofi=round(raw_ofi, 6),
            accumulated_ofi=round(accumulated, 6),
            regime=self._classify_regime(accumulated),
            delta_bid=round(delta_bid, 6),
            delta_ask=round(delta_ask, 6),
            window_tick_count=window_tick_count,
        )

    @staticmethod
    def _validate_snapshot(snapshot: L1Snapshot) -> None:
        if snapshot.best_bid_price <= 0 or snapshot.best_ask_price <= 0:
            raise ValueError("Snapshot prices must be positive")
        if snapshot.best_bid_size < 0 or snapshot.best_ask_size < 0:
            raise ValueError("Snapshot sizes cannot be negative")
        if snapshot.best_bid_price >= snapshot.best_ask_price:
            raise ValueError("Crossed or locked L1 book")

    @staticmethod
    def _validate_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            raise ValueError("Empty DataFrame")
        frame = df.copy()
        frame.columns = [str(col).lower() for col in frame.columns]
        missing = set(_REQUIRED_OHLCV_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"Missing columns: {sorted(missing)}")
        for col in _REQUIRED_OHLCV_COLUMNS:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=list(_REQUIRED_OHLCV_COLUMNS))
        frame = frame[frame["volume"] > 0].copy()
        if frame.empty:
            raise ValueError("No valid OHLCV rows")
        if "date" in frame.columns:
            frame = frame.reset_index(drop=True).sort_values("date")
        elif isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.sort_index()
        return frame.reset_index(drop=True)

    @staticmethod
    def _snapshot_from_bar(row: object, idx: int) -> L1Snapshot:
        high = float(row.high)  # type: ignore[attr-defined]
        low = float(row.low)  # type: ignore[attr-defined]
        close = float(row.close)  # type: ignore[attr-defined]
        volume = float(row.volume)  # type: ignore[attr-defined]
        spread = max(high - low, abs(close) * 0.0005, 0.01)
        half_spread = spread / 2.0
        price_range = high - low
        buy_ratio = (
            0.5 if price_range <= 0 else float(np.clip((close - low) / price_range, 0.0, 1.0))
        )
        return L1Snapshot(
            timestamp=float(idx),
            best_bid_price=max(close - half_spread, 1e-9),
            best_bid_size=max(volume * buy_ratio, 0.0),
            best_ask_price=close + half_spread,
            best_ask_size=max(volume * (1.0 - buy_ratio), 0.0),
        )


def analyze_ofi_from_ohlcv(
    df: pd.DataFrame,
    config: OFIEngineConfig | None = None,
) -> OFIAnalysisOutput:
    """Analyze OHLCV data and return OFI signal."""
    try:
        engine = OFIEngine(config)
        return engine.analyze_ohlcv_proxy(df)
    except Exception as exc:
        logger.exception("OFI analysis failed")
        return OFIAnalysisOutput(ok=False, error=str(exc))
