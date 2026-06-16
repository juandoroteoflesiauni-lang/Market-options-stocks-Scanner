from __future__ import annotations
"""Single Prints engine for TPO profiles.

This module ports the TypeScript Single Prints detector into the Python
technical specialist. It consumes the TPO profile contract emitted by
``tpo_skewness.py`` and tracks the lifecycle of detected zones in memory.
"""


from enum import StrEnum
from math import isfinite
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.config.logger_setup import get_logger
from backend.quant_engine.engines.technical.tpo_skewness import TPOLevel, TPOProfile

logger = get_logger(__name__)


class SinglePrintType(StrEnum):
    """Geometric classification of a Single Print zone."""

    BUYING_TAIL = "BuyingTail"
    SELLING_TAIL = "SellingTail"
    INTERNAL = "Internal"


class ZoneStatus(StrEnum):
    """Lifecycle state of a Single Print zone."""

    ACTIVE = "Active"
    VALIDATED = "Validated"
    FILLED = "Filled"


class OHLCBar(BaseModel):
    """OHLC bar used to track fills after a zone is detected."""

    model_config = ConfigDict(extra="ignore")

    timestamp: str
    open: float
    high: float
    low: float
    close: float


class SinglePrintZone(BaseModel):
    """Detected block of consecutive TPO levels with exactly one TPO."""

    model_config = ConfigDict(extra="ignore")

    id: str
    creation_date: str | None = None
    symbol: str
    type: SinglePrintType
    top_price: float
    bottom_price: float
    size: int
    status: ZoneStatus = ZoneStatus.ACTIVE
    filled_date: str | None = None
    source_session_id: str


class SinglePrintConfig(BaseModel):
    """Runtime configuration for Single Prints detection and tracking."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    min_tail_size: int = Field(default=2, ge=1)
    tick_size: float | None = Field(default=None, gt=0)
    auto_validate_tails_on_open: bool = True


class ScanResult(BaseModel):
    """Result emitted when scanning a closed TPO profile."""

    model_config = ConfigDict(extra="ignore")

    new_zones: tuple[SinglePrintZone, ...] = ()
    session_id: str
    scanned_at: str


class TrackingResult(BaseModel):
    """Result emitted by open/fill lifecycle tracking."""

    model_config = ConfigDict(extra="ignore")

    updated_zones: tuple[SinglePrintZone, ...] = ()
    filled_zones: tuple[SinglePrintZone, ...] = ()
    validated_zones: tuple[SinglePrintZone, ...] = ()


class SinglePrintEngine:
    """Detects and tracks Single Print zones from TPO profiles."""

    def __init__(self: SinglePrintEngine, config: SinglePrintConfig | None = None) -> None:
        self.config = config or SinglePrintConfig()
        self._all_zones: dict[str, SinglePrintZone] = {}
        self._active_zone_ids: list[str] = []

    def scan_profile(self: SinglePrintEngine, profile: TPOProfile) -> ScanResult:
        """Scan a closed TPO profile and register newly detected zones."""
        levels = tuple(profile.levels)
        self._assert_profile_ordered(profile.session_id, levels)
        tick_size = self._resolve_tick_size(profile)
        detected: list[SinglePrintZone] = []
        buffer: list[TPOLevel] = []

        for level in levels:
            if level.tpo_count == 1:
                buffer.append(level)
                continue
            zone = self._try_create_zone(buffer, profile, tick_size)
            if zone is not None:
                detected.append(zone)
            buffer = []

        zone = self._try_create_zone(buffer, profile, tick_size)
        if zone is not None:
            detected.append(zone)

        for detected_zone in detected:
            self._all_zones[detected_zone.id] = detected_zone
            self._active_zone_ids.append(detected_zone.id)

        return ScanResult(
            new_zones=tuple(detected),
            session_id=profile.session_id,
            scanned_at=_profile_scan_time(profile),
        )

    def process_bar(self: SinglePrintEngine, bar: OHLCBar) -> TrackingResult:
        """Mark active zones as filled when the OHLC range consumes them."""
        filled: list[SinglePrintZone] = []
        remaining_ids: list[str] = []

        for zone_id in self._active_zone_ids:
            zone = self._all_zones[zone_id]
            if self._is_filled(bar, zone):
                updated = zone.model_copy(
                    update={"status": ZoneStatus.FILLED, "filled_date": bar.timestamp}
                )
                self._all_zones[zone_id] = updated
                filled.append(updated)
            else:
                remaining_ids.append(zone_id)

        self._active_zone_ids = remaining_ids
        return TrackingResult(updated_zones=tuple(filled), filled_zones=tuple(filled))

    def process_open(self: SinglePrintEngine, open_price: float, timestamp: str) -> TrackingResult:
        """Validate tail zones when the next session opens outside them."""
        if not self.config.auto_validate_tails_on_open:
            return TrackingResult()

        validated: list[SinglePrintZone] = []
        for zone_id in self._active_zone_ids:
            zone = self._all_zones[zone_id]
            if zone.status is not ZoneStatus.ACTIVE:
                continue

            should_validate = (
                zone.type is SinglePrintType.BUYING_TAIL and open_price > zone.top_price
            ) or (zone.type is SinglePrintType.SELLING_TAIL and open_price < zone.bottom_price)
            if should_validate:
                updated = zone.model_copy(update={"status": ZoneStatus.VALIDATED})
                self._all_zones[zone_id] = updated
                validated.append(updated)

        return TrackingResult(
            updated_zones=tuple(validated),
            validated_zones=tuple(validated),
        )

    def get_active_zones(self: SinglePrintEngine) -> tuple[SinglePrintZone, ...]:
        """Return zones that can still be filled."""
        return tuple(self._all_zones[zone_id] for zone_id in self._active_zone_ids)

    def get_all_zones(self: SinglePrintEngine) -> tuple[SinglePrintZone, ...]:
        """Return the full in-memory zone history."""
        return tuple(self._all_zones.values())

    def get_zones_by_type(
        self: SinglePrintEngine, zone_type: SinglePrintType
    ) -> tuple[SinglePrintZone, ...]:
        """Return all known zones matching a type."""
        return tuple(zone for zone in self._all_zones.values() if zone.type is zone_type)

    def _try_create_zone(
        self: SinglePrintEngine,
        buffer: list[TPOLevel],
        profile: TPOProfile,
        tick_size: float,
    ) -> SinglePrintZone | None:
        if len(buffer) < self.config.min_tail_size:
            return None

        bottom_price = _round_to_tick(buffer[0].price, tick_size)
        top_price = _round_to_tick(buffer[-1].price, tick_size)
        if not all(isfinite(value) for value in (bottom_price, top_price)):
            return None

        size = int(round((top_price - bottom_price) / tick_size)) + 1
        return SinglePrintZone(
            id=f"sp_{uuid4().hex[:16]}",
            creation_date=profile.session_end or profile.session_start,
            symbol=profile.session_id,
            type=self._classify_zone(bottom_price, top_price, profile, tick_size),
            top_price=top_price,
            bottom_price=bottom_price,
            size=size,
            source_session_id=profile.session_id,
        )

    def _classify_zone(
        self: SinglePrintEngine,
        bottom_price: float,
        top_price: float,
        profile: TPOProfile,
        tick_size: float,
    ) -> SinglePrintType:
        epsilon = tick_size * 0.5
        lowest_price = profile.lowest_price
        highest_price = profile.highest_price
        if lowest_price is not None and abs(bottom_price - lowest_price) <= epsilon:
            return SinglePrintType.BUYING_TAIL
        if highest_price is not None and abs(top_price - highest_price) <= epsilon:
            return SinglePrintType.SELLING_TAIL
        return SinglePrintType.INTERNAL

    def _is_filled(self: SinglePrintEngine, bar: OHLCBar, zone: SinglePrintZone) -> bool:
        tick_size = self.config.tick_size or max((zone.top_price - zone.bottom_price), 1e-9)
        epsilon = tick_size * 0.5
        if zone.type is SinglePrintType.BUYING_TAIL:
            return bar.low <= zone.bottom_price + epsilon
        if zone.type is SinglePrintType.SELLING_TAIL:
            return bar.high >= zone.top_price - epsilon
        return bar.low <= zone.bottom_price + epsilon and bar.high >= zone.top_price - epsilon

    def _resolve_tick_size(self: SinglePrintEngine, profile: TPOProfile) -> float:
        if self.config.tick_size is not None:
            return self.config.tick_size
        prices = [level.price for level in profile.levels]
        if len(prices) < 2:
            raise ValueError("TPO profile needs at least 2 levels or an explicit tick_size")
        diffs = [
            round(prices[idx] - prices[idx - 1], 10)
            for idx in range(1, len(prices))
            if prices[idx] > prices[idx - 1]
        ]
        if not diffs:
            raise ValueError("Unable to infer tick_size from flat TPO profile")
        return float(min(diffs))

    @staticmethod
    def _assert_profile_ordered(session_id: str, levels: tuple[TPOLevel, ...]) -> None:
        for idx in range(1, len(levels)):
            if levels[idx].price < levels[idx - 1].price:
                raise ValueError(
                    f'TPOProfile "{session_id}" has unordered levels at index {idx}: '
                    f"{levels[idx].price} < {levels[idx - 1].price}"
                )


def scan_single_prints_from_tpo_profile(
    profile: TPOProfile,
    config: SinglePrintConfig | None = None,
) -> ScanResult:
    """Convenience entry point for stateless detection in services."""
    return SinglePrintEngine(config=config).scan_profile(profile)


def _round_to_tick(price: float, tick_size: float) -> float:
    return round(round(price / tick_size) * tick_size, 10)


def _profile_scan_time(profile: TPOProfile) -> str:
    return profile.session_end or profile.session_start or profile.session_id
