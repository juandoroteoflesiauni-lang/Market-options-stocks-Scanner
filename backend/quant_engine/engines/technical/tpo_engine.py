from __future__ import annotations
"""Motor TPO Skewness — Sector Técnico.

Build and classify a Time Price Opportunity (TPO) profile using
Fisher-Pearson skewness and bimodal detection.
"""


import logging
from math import floor, isfinite, sqrt

import numpy as np
import pandas as pd

from ...domain.technical.tpo_models import (
    ProfileShape,
    TPOLevel,
    TPOProfile,
    TPOSkewnessConfig,
    TPOSkewnessSignal,
)

logger = logging.getLogger(__name__)


class _MutableLevel:
    def __init__(self, price: float) -> None:
        self.price = price
        self.brackets: set[int] = set()

    @property
    def tpo_count(self) -> int:
        return len(self.brackets)


class TPOSkewnessEngine:
    """Build and classify a TPO profile from OHLCV bars."""

    def __init__(self, session_id: str, config: TPOSkewnessConfig | None = None) -> None:
        self.config = config or TPOSkewnessConfig()
        self.session_id = session_id
        self._tick_size = self.config.tick_size
        self._session_start_ms = self._parse_time_to_ms(self.config.session_start_time)
        self._bracket_duration_ms = self.config.bracket_duration_minutes * 60_000
        self._levels: dict[float, _MutableLevel] = {}
        self._session_start: str | None = None
        self._session_end: str | None = None
        self._highest_price: float | None = None
        self._lowest_price: float | None = None
        self._is_intraday_input = False

    @property
    def tick_size(self) -> float | None:
        return self._tick_size

    def ingest_frame(self, df: pd.DataFrame) -> None:
        """Ingest OHLCV bars ordered oldest to newest."""
        frame = _validate_ohlcv_frame(df)
        if frame.empty:
            return

        self._tick_size = self._tick_size or _infer_tick_size(frame, self.config.max_total_levels)
        self._is_intraday_input = _looks_intraday(frame["date"])
        self._session_start = str(pd.Timestamp(frame["date"].iloc[0]))
        self._session_end = str(pd.Timestamp(frame["date"].iloc[-1]))

        for bar_index, row in enumerate(frame.itertuples(index=False)):
            low = float(row.low)
            high = float(row.high)
            if not all(isfinite(x) for x in (low, high)) or high < low:
                continue
            bracket_id = self._compute_bracket_id(pd.Timestamp(row.date), bar_index)
            self._ingest_range(low, high, bracket_id)

    def evaluate(self) -> TPOSkewnessSignal:
        """Compute Fisher-Pearson skewness and classify the current profile."""
        if self._tick_size is None or self._tick_size <= 0:
            return TPOSkewnessSignal(ok=False, error="No tick size available")
        if len(self._levels) < 3:
            return TPOSkewnessSignal(ok=False, error="Insufficient TPO price levels")

        profile, shape = self._compute_profile()
        return TPOSkewnessSignal(
            ok=True,
            timestamp=self._session_end,
            skewness_value=profile.skewness,
            profile_shape=shape,
            snapshot=profile,
            tick_size=self._tick_size,
            bracket_count=len({b for level in self._levels.values() for b in level.brackets}),
            is_intraday_input=self._is_intraday_input,
        )

    @classmethod
    def analyze_ohlcv(
        cls,
        df: pd.DataFrame,
        session_id: str = "technical",
    ) -> TPOSkewnessSignal:
        """Convenience entry point used by the technical payload service."""
        try:
            engine = cls(session_id=session_id)
            engine.ingest_frame(df)
            return engine.evaluate()
        except Exception as exc:
            logger.exception("TPO skewness analysis failed")
            return TPOSkewnessSignal(ok=False, error=str(exc))

    # ── internal ──────────────────────────────────────────────────────────────

    def _ingest_range(self, low: float, high: float, bracket_id: int) -> None:
        assert self._tick_size is not None
        low_bin = self._normalise_price(low)
        high_bin = self._normalise_price(high)
        if high_bin < low_bin:
            low_bin, high_bin = high_bin, low_bin

        bin_count = int(round((high_bin - low_bin) / self._tick_size)) + 1
        step = (
            max(1, int(np.ceil(bin_count / self.config.max_bins_per_bar)))
            if bin_count > self.config.max_bins_per_bar
            else 1
        )

        for offset in range(0, bin_count, step):
            price = self._normalise_price(low_bin + offset * self._tick_size)
            if len(self._levels) >= self.config.max_total_levels and price not in self._levels:
                break
            self._touch_price_level(price, bracket_id)

    def _touch_price_level(self, price: float, bracket_id: int) -> None:
        level = self._levels.get(price)
        if level is None:
            level = _MutableLevel(price)
            self._levels[price] = level
        level.brackets.add(bracket_id)
        self._highest_price = (
            price if self._highest_price is None else max(self._highest_price, price)
        )
        self._lowest_price = price if self._lowest_price is None else min(self._lowest_price, price)

    def _compute_profile(self) -> tuple[TPOProfile, ProfileShape]:
        levels = list(self._levels.values())
        total_tpos = sum(lv.tpo_count for lv in levels)
        if total_tpos <= 0:
            return self._profile_from_stats(0, 0.0, 0.0, 0.0, None), ProfileShape.Transitional

        centre = ((self._highest_price or 0.0) + (self._lowest_price or 0.0)) / 2.0
        poc = max(levels, key=lambda lv: (lv.tpo_count, -abs(lv.price - centre)))
        mean = sum(lv.tpo_count * lv.price for lv in levels) / total_tpos
        variance = sum(lv.tpo_count * (lv.price - mean) ** 2 for lv in levels) / total_tpos
        sigma = sqrt(max(variance, 0.0))

        if self._tick_size is None or sigma < self._tick_size / 2.0:
            skewness = 0.0
        else:
            third_moment = sum(lv.tpo_count * (lv.price - mean) ** 3 for lv in levels)
            skewness = third_moment / (total_tpos * (sigma**3))

        profile = self._profile_from_stats(total_tpos, mean, sigma, skewness, poc.price)
        return profile, self._classify_shape(skewness)

    def _profile_from_stats(
        self,
        total_tpos: int,
        mean: float,
        sigma: float,
        skewness: float,
        poc_price: float | None,
    ) -> TPOProfile:
        compact = _compact_levels(self._levels, self.config.compact_level_limit)
        return TPOProfile(
            session_id=self.session_id,
            session_start=self._session_start,
            session_end=self._session_end,
            highest_price=self._highest_price,
            lowest_price=self._lowest_price,
            poc_price=poc_price,
            mean_price=mean,
            standard_deviation=sigma,
            skewness=skewness,
            total_tpos=total_tpos,
            level_count=len(self._levels),
            levels=compact,
        )

    def _classify_shape(self, skewness: float) -> ProfileShape:
        if self._detect_bimodal():
            return ProfileShape.DDoubleDistribution
        if abs(skewness) <= self.config.symmetry_threshold:
            return ProfileShape.NormalDistribution
        if skewness > self.config.skew_threshold:
            return ProfileShape.BShape
        if skewness < -self.config.skew_threshold:
            return ProfileShape.PShape
        return ProfileShape.Transitional

    def _detect_bimodal(self) -> bool:
        if len(self._levels) < 2 * self.config.bimodal_gap_ticks:
            return False
        consecutive = 0
        for price in sorted(self._levels):
            if self._levels[price].tpo_count <= 1:
                consecutive += 1
                if consecutive >= self.config.bimodal_gap_ticks:
                    return True
            else:
                consecutive = 0
        return False

    def _normalise_price(self, raw: float) -> float:
        assert self._tick_size is not None
        steps = round(raw / self._tick_size)
        digits = (
            max(0, min(8, int(np.ceil(np.log10(1.0 / self._tick_size))) + 2))
            if self._tick_size < 1
            else 4
        )
        return round(steps * self._tick_size, digits)

    def _compute_bracket_id(self, timestamp: pd.Timestamp, bar_index: int) -> int:
        if not self._is_intraday_input:
            return bar_index
        ms = (
            timestamp.hour * 3_600_000
            + timestamp.minute * 60_000
            + timestamp.second * 1_000
            + floor(timestamp.microsecond / 1_000)
        )
        return floor(max(0, ms - self._session_start_ms) / self._bracket_duration_ms)

    @staticmethod
    def _parse_time_to_ms(hh_mm: str) -> int:
        try:
            h, m = hh_mm.split(":", maxsplit=1)
            hours, minutes = int(h), int(m)
        except ValueError as exc:
            raise ValueError(f'Invalid session start time "{hh_mm}". Expected "HH:MM".') from exc
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise ValueError(f'Invalid session start time "{hh_mm}". Expected "HH:MM".')
        return (hours * 60 + minutes) * 60_000


def analyze_tpo_skewness_from_ohlcv(
    df: pd.DataFrame,
    session_id: str = "technical",
) -> TPOSkewnessSignal:
    """Analyze OHLCV data and return a JSON-safe TPO skewness signal."""
    return TPOSkewnessEngine.analyze_ohlcv(df, session_id=session_id)


# ── helpers ───────────────────────────────────────────────────────────────────


def _validate_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing OHLCV columns: {', '.join(missing)}")
    frame = df.reset_index(drop=True).copy() if "date" in df.columns else df.copy()
    if "date" not in frame.columns:
        frame["date"] = pd.to_datetime(frame.index)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for col in required:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["date", "high", "low", "close"])
    frame = frame[(frame["high"] >= frame["low"]) & (frame["high"] > 0) & (frame["low"] > 0)]
    return frame.sort_values("date").reset_index(drop=True)


def _infer_tick_size(frame: pd.DataFrame, max_total_levels: int) -> float:
    price_min = float(frame["low"].min())
    price_max = float(frame["high"].max())
    span = max(price_max - price_min, price_max * 0.001, 0.01)
    raw_tick = span / max(max_total_levels * 0.70, 1)
    close = float(frame["close"].iloc[-1])
    price_floor = max(abs(close) * 0.0001, 0.0001)
    tick = max(raw_tick, price_floor)
    if close >= 10:
        tick = max(round(tick, 2), 0.01)
    elif close >= 1:
        tick = max(round(tick, 4), 0.0001)
    else:
        tick = max(round(tick, 6), 0.000001)
    return float(tick)


def _looks_intraday(dates: pd.Series) -> bool:
    if len(dates) < 2:
        return False
    sorted_dates = pd.to_datetime(dates).sort_values()
    deltas = sorted_dates.diff().dropna()
    return bool((deltas < pd.Timedelta(hours=6)).any())


def _compact_levels(levels: dict[float, _MutableLevel], limit: int) -> tuple[TPOLevel, ...]:
    ordered = sorted(levels.values(), key=lambda lv: lv.price)
    if len(ordered) > limit:
        step = int(np.ceil(len(ordered) / limit))
        ordered = ordered[::step][:limit]
    return tuple(
        TPOLevel(
            price=lv.price,
            tpo_count=lv.tpo_count,
            brackets=tuple(sorted(lv.brackets)),
        )
        for lv in ordered
    )
