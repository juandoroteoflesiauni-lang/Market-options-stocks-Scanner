"""Motor VPOC Migration — Sector Técnico.

Ports the vPOC/Value Area migration logic. Builds rolling volume profiles
and classifies the direction of Point of Control migration.
"""

from __future__ import annotations

import logging
from enum import Enum
from math import ceil, isfinite
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")
_DEFAULT_VALUE_AREA_TARGET: float = 0.70
_DEFAULT_ROLLING_WINDOW_SIZE: int = 3
_DEFAULT_MAX_BINS: int = 500
_MIN_BARS_PER_PROFILE: int = 2


class MigrationState(str, Enum):
    """State emitted by the rolling vPOC migration model."""

    VALUE_AREA_EXPANDING = "ValueAreaExpanding"
    VALUE_AREA_SHIFTING_UP = "ValueAreaShiftingUp"
    VALUE_AREA_SHIFTING_DOWN = "ValueAreaShiftingDown"
    POC_UNCHANGED = "PocUnchanged"
    POC_MIGRATING_UP = "PocMigratingUp"
    POC_MIGRATING_DOWN = "PocMigratingDown"
    CONSOLIDATING = "Consolidating"
    INSUFFICIENT = "Insufficient"


class PriceLevel(BaseModel):
    """Single snapped price bin inside a volume profile."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    price: float
    total_volume: float
    bid_volume: float
    ask_volume: float
    delta: float


class VPOCProfile(BaseModel):
    """Computed volume profile for a defined chronological window."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ok: bool = True
    error: str | None = None
    window_id: str = ""
    start_time: str = ""
    end_time: str = ""
    highest_price: float = 0.0
    lowest_price: float = 0.0
    total_volume: float = 0.0
    poc_price: float = 0.0
    value_area_high: float = 0.0
    value_area_low: float = 0.0
    value_area_coverage: float = 0.0
    levels: tuple[PriceLevel, ...] = ()


class VPOCMigrationSignal(BaseModel):
    """Rolling vPOC migration signal comparing oldest and newest profiles."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ok: bool = True
    error: str | None = None
    state: MigrationState = MigrationState.INSUFFICIENT
    current_poc: float = 0.0
    reference_poc: float = 0.0
    poc_delta: float = 0.0
    value_area_width_delta: float = 0.0
    value_area_midpoint_delta: float = 0.0
    window_count: int = 0
    profiles: tuple[VPOCProfile, ...] = ()


class VPOCConfig(BaseModel):
    """Runtime configuration for vPOC profile construction."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    tick_size: float | None = None
    value_area_target: float = _DEFAULT_VALUE_AREA_TARGET
    rolling_window_size: int = _DEFAULT_ROLLING_WINDOW_SIZE
    max_bins: int = _DEFAULT_MAX_BINS


class VPOCMigrationEngine:
    """Builds snapped volume profiles and classifies vPOC migration."""

    def __init__(self, config: VPOCConfig | None = None) -> None:
        self.config = config or VPOCConfig()
        if self.config.tick_size is not None and self.config.tick_size <= 0:
            raise ValueError("tick_size must be > 0")
        if not 0 < self.config.value_area_target < 1:
            raise ValueError("value_area_target must be in (0, 1)")
        if self.config.rolling_window_size < 2:
            raise ValueError("rolling_window_size must be >= 2")
        if self.config.max_bins < 10:
            raise ValueError("max_bins must be >= 10")

    def build_profile_from_bars(self, df: pd.DataFrame, window_id: str) -> VPOCProfile:
        """Build a volume profile from OHLCV bars."""
        try:
            frame = self._validate_frame(df)
            if len(frame) < _MIN_BARS_PER_PROFILE:
                return self._empty_profile(window_id, f"Insufficient bars ({len(frame)})")

            price_min = float(frame["low"].min())
            price_max = float(frame["high"].max())
            if not isfinite(price_min) or not isfinite(price_max):
                return self._empty_profile(window_id, "Non-finite price range")

            tick_size = self._resolve_tick_size(price_min, price_max)
            bins = self._bins_from_bars(frame, tick_size)
            if not bins:
                return self._empty_profile(window_id, "No volume bins produced")
            return self._finalise_profile(bins, frame, window_id)
        except Exception as exc:
            logger.warning("VPOC profile failed for %s: %s", window_id, exc)
            return self._empty_profile(window_id, str(exc))

    def calculate_migration(
        self, profiles: list[VPOCProfile] | tuple[VPOCProfile, ...]
    ) -> VPOCMigrationSignal:
        """Compare oldest and newest profiles and emit a migration state."""
        valid_profiles = tuple(p for p in profiles if p.ok)
        if len(valid_profiles) < 2:
            ref = valid_profiles[0] if valid_profiles else None
            return VPOCMigrationSignal(
                ok=False,
                error="Need at least 2 valid profiles for migration",
                state=MigrationState.INSUFFICIENT,
                current_poc=ref.poc_price if ref else 0.0,
                reference_poc=ref.poc_price if ref else 0.0,
                window_count=len(valid_profiles),
                profiles=valid_profiles,
            )

        reference = valid_profiles[0]
        current = valid_profiles[-1]
        poc_delta = current.poc_price - reference.poc_price
        ref_width = reference.value_area_high - reference.value_area_low
        cur_width = current.value_area_high - current.value_area_low
        width_delta = cur_width - ref_width
        ref_midpoint = (reference.value_area_high + reference.value_area_low) / 2.0
        cur_midpoint = (current.value_area_high + current.value_area_low) / 2.0
        midpoint_delta = cur_midpoint - ref_midpoint

        state = self._classify_state(poc_delta, width_delta, midpoint_delta)
        return VPOCMigrationSignal(
            ok=True,
            state=state,
            current_poc=round(current.poc_price, 6),
            reference_poc=round(reference.poc_price, 6),
            poc_delta=round(poc_delta, 6),
            value_area_width_delta=round(width_delta, 6),
            value_area_midpoint_delta=round(midpoint_delta, 6),
            window_count=len(valid_profiles),
            profiles=valid_profiles,
        )

    def build_rolling_signal(
        self,
        df: pd.DataFrame,
        window_size: int = _DEFAULT_ROLLING_WINDOW_SIZE,
    ) -> VPOCMigrationSignal:
        """Build consecutive chronological profiles and return the latest migration signal."""
        frame = self._validate_frame(df)
        size = max(2, window_size)
        if len(frame) < size * _MIN_BARS_PER_PROFILE:
            return VPOCMigrationSignal(
                ok=False,
                error=f"Need at least {size * _MIN_BARS_PER_PROFILE} bars for {size} windows",
                state=MigrationState.INSUFFICIENT,
            )

        chunk_size = ceil(len(frame) / size)
        chunks = [frame.iloc[s : s + chunk_size] for s in range(0, len(frame), chunk_size)][:size]
        profiles = [
            self.build_profile_from_bars(c.copy(), f"window-{i + 1}") for i, c in enumerate(chunks)
        ]
        return self.calculate_migration(profiles)

    def _validate_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            raise ValueError("Empty DataFrame")
        frame = df.copy()
        frame.columns = [str(col).lower() for col in frame.columns]
        missing = set(_REQUIRED_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"Missing columns: {sorted(missing)}")
        for col in _REQUIRED_COLUMNS:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=list(_REQUIRED_COLUMNS))
        frame = frame[frame["volume"] > 0].copy()
        if frame.empty:
            raise ValueError("No valid OHLCV rows")
        if "date" in frame.columns:
            frame = frame.reset_index(drop=True).sort_values("date")
        elif isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.sort_index()
        return frame.reset_index(drop=False)

    def _resolve_tick_size(self, price_min: float, price_max: float) -> float:
        if self.config.tick_size is not None:
            tick_size = float(self.config.tick_size)
        else:
            price_range = max(price_max - price_min, abs(price_max) * 0.001, 0.01)
            tick_size = price_range / min(100, self.config.max_bins)
        bin_count = int(ceil(max(price_max - price_min, tick_size) / tick_size)) + 1
        if bin_count > self.config.max_bins:
            tick_size = max((price_max - price_min) / max(self.config.max_bins - 1, 1), tick_size)
        return max(float(tick_size), 1e-9)

    def _bins_from_bars(self, frame: pd.DataFrame, tick_size: float) -> dict[float, PriceLevel]:
        bins: dict[float, dict[str, float]] = {}
        for row in frame.itertuples(index=False):
            high = float(row.high)
            low = float(row.low)
            close = float(row.close)
            volume = float(row.volume)
            lo = self._snap_to_tick(low, tick_size)
            hi = self._snap_to_tick(high, tick_size)
            if hi < lo:
                lo, hi = hi, lo
            levels = max(min(int(round((hi - lo) / tick_size)) + 1, self.config.max_bins), 1)
            total_buy = volume * self._buy_ratio(low, high, close)
            total_sell = volume - total_buy
            vol_per_lvl = volume / levels
            buy_per_lvl = total_buy / levels
            sell_per_lvl = total_sell / levels
            for i in range(levels):
                price = self._snap_to_tick(lo + i * tick_size, tick_size)
                bd = bins.setdefault(
                    price, {"total_volume": 0.0, "bid_volume": 0.0, "ask_volume": 0.0}
                )
                bd["total_volume"] += vol_per_lvl
                bd["bid_volume"] += buy_per_lvl
                bd["ask_volume"] += sell_per_lvl

        return {
            price: PriceLevel(
                price=round(price, 6),
                total_volume=float(v["total_volume"]),
                bid_volume=float(v["bid_volume"]),
                ask_volume=float(v["ask_volume"]),
                delta=float(v["bid_volume"] - v["ask_volume"]),
            )
            for price, v in bins.items()
        }

    def _finalise_profile(
        self, bins: dict[float, PriceLevel], frame: pd.DataFrame, window_id: str
    ) -> VPOCProfile:
        levels = tuple(sorted(bins.values(), key=lambda lv: lv.price))
        total_volume = sum(lv.total_volume for lv in levels)
        if total_volume <= 0:
            return self._empty_profile(window_id, "Zero total volume")
        poc = self._find_poc(levels)
        vah, val, coverage = self._calculate_value_area(levels, poc.price, total_volume)
        return VPOCProfile(
            ok=True,
            window_id=window_id,
            start_time=self._time_label(frame.iloc[0].to_dict()),
            end_time=self._time_label(frame.iloc[-1].to_dict()),
            highest_price=round(max(lv.price for lv in levels), 6),
            lowest_price=round(min(lv.price for lv in levels), 6),
            total_volume=round(float(total_volume), 4),
            poc_price=round(float(poc.price), 6),
            value_area_high=round(vah, 6),
            value_area_low=round(val, 6),
            value_area_coverage=round(coverage, 6),
            levels=levels,
        )

    def _calculate_value_area(
        self,
        ladder: tuple[PriceLevel, ...],
        poc_price: float,
        total_volume: float,
    ) -> tuple[float, float, float]:
        poc_idx = next((i for i, lv in enumerate(ladder) if lv.price == poc_price), -1)
        if poc_idx < 0:
            raise ValueError(f"POC price {poc_price} not found in ladder")
        target = total_volume * self.config.value_area_target
        accum = ladder[poc_idx].total_volume
        hi_idx = poc_idx + 1
        lo_idx = poc_idx - 1

        while accum < target:
            can_up = hi_idx < len(ladder)
            can_down = lo_idx >= 0
            if not can_up and not can_down:
                break
            up_vol = (ladder[hi_idx].total_volume if can_up else 0.0) + (
                ladder[hi_idx + 1].total_volume if can_up and hi_idx + 1 < len(ladder) else 0.0
            )
            down_vol = (ladder[lo_idx].total_volume if can_down else 0.0) + (
                ladder[lo_idx - 1].total_volume if can_down and lo_idx - 1 >= 0 else 0.0
            )
            if not can_down or (can_up and up_vol >= down_vol):
                accum += ladder[hi_idx].total_volume
                hi_idx += 1
                if (
                    accum < target
                    and hi_idx < len(ladder)
                    and up_vol > ladder[hi_idx - 1].total_volume
                ):
                    accum += ladder[hi_idx].total_volume
                    hi_idx += 1
            else:
                accum += ladder[lo_idx].total_volume
                lo_idx -= 1
                if accum < target and lo_idx >= 0 and down_vol > ladder[lo_idx + 1].total_volume:
                    accum += ladder[lo_idx].total_volume
                    lo_idx -= 1

        vah_idx = min(hi_idx - 1, len(ladder) - 1)
        val_idx = max(lo_idx + 1, 0)
        return ladder[vah_idx].price, ladder[val_idx].price, accum / total_volume

    @staticmethod
    def _find_poc(levels: tuple[PriceLevel, ...]) -> PriceLevel:
        return max(levels, key=lambda lv: (lv.total_volume, -lv.price))

    @staticmethod
    def _classify_state(
        poc_delta: float, value_area_width_delta: float, value_area_midpoint_delta: float
    ) -> MigrationState:
        if poc_delta == 0 and value_area_width_delta < 0:
            return MigrationState.CONSOLIDATING
        if poc_delta > 0:
            return MigrationState.POC_MIGRATING_UP
        if poc_delta < 0:
            return MigrationState.POC_MIGRATING_DOWN
        if value_area_width_delta > 0:
            return MigrationState.VALUE_AREA_EXPANDING
        if value_area_midpoint_delta > 0:
            return MigrationState.VALUE_AREA_SHIFTING_UP
        if value_area_midpoint_delta < 0:
            return MigrationState.VALUE_AREA_SHIFTING_DOWN
        return MigrationState.POC_UNCHANGED

    @staticmethod
    def _snap_to_tick(price: float, tick_size: float) -> float:
        return round(round(price / tick_size) * tick_size, 10)

    @staticmethod
    def _buy_ratio(low: float, high: float, close: float) -> float:
        price_range = high - low
        if price_range <= 0:
            return 0.5
        return float(np.clip((close - low) / price_range, 0.0, 1.0))

    @staticmethod
    def _time_label(row: dict[str, Any]) -> str:
        if "date" in row and pd.notna(row["date"]):
            return str(pd.Timestamp(row["date"]).date())
        if "index" in row and pd.notna(row["index"]):
            v = row["index"]
            if isinstance(v, pd.Timestamp):
                return str(v.date())
            return str(v)
        return ""

    @staticmethod
    def _empty_profile(window_id: str, error: str) -> VPOCProfile:
        return VPOCProfile(ok=False, error=error, window_id=window_id)


def analyze_vpoc_from_ohlcv(
    df: pd.DataFrame,
    config: VPOCConfig | None = None,
    window_size: int = _DEFAULT_ROLLING_WINDOW_SIZE,
) -> VPOCMigrationSignal:
    """Analyze OHLCV data and return the vPOC migration signal."""
    try:
        engine = VPOCMigrationEngine(config)
        return engine.build_rolling_signal(df, window_size)
    except Exception as exc:
        logger.exception("VPOC analysis failed")
        return VPOCMigrationSignal(ok=False, error=str(exc), state=MigrationState.INSUFFICIENT)
