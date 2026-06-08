"""Volume Node topography engine for the technical specialist.

Detects high-volume nodes (HVN) and low-volume nodes (LVN) from a volume
profile curve using smoothing, local extrema and prominence filtering.
"""

from __future__ import annotations

from enum import StrEnum
from math import exp, isfinite
from typing import Literal, cast

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from backend.config.logger_setup import get_logger
from backend.layer_3_specialists.tecnico.volume import VolumeAnalytics

logger = get_logger(__name__)

Direction = Literal["above", "below", "either"]


class VolumeNodeType(StrEnum):
    """Discriminates accepted value from thin rejection zones."""

    HVN = "HVN"
    LVN = "LVN"


class TopographyNode(BaseModel):
    """Single HVN/LVN node emitted by the topography engine."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    price: float
    volume: float
    smoothed: float
    type: VolumeNodeType
    prominence: float
    distance_to_last: float | None = None
    distance_pct_to_last: float | None = None


class VolumeNodeConfig(BaseModel):
    """Runtime knobs for volume-node extraction."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    n_bins: int = Field(default=100, ge=10, le=500)
    smoothing_window: int = Field(default=5, ge=1, le=31)
    smoothing_sigma: float = Field(default=1.2, gt=0)
    smoothing_method: Literal["gaussian", "sma"] = "gaussian"
    prominence_threshold: float = Field(default=0.15, ge=0, le=1)
    edge_bins_as_lvn: bool = True
    max_nodes: int = Field(default=20, ge=1, le=100)


class VolumeNodeTopography(BaseModel):
    """Computed HVN/LVN topography for a profile window."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = True
    ok: bool = True
    error: str | None = None
    timestamp: str | None = None
    poc_price: float | None = None
    last_close: float | None = None
    node_count: int = 0
    hvn_count: int = 0
    lvn_count: int = 0
    nearest_hvn_above: TopographyNode | None = None
    nearest_hvn_below: TopographyNode | None = None
    nearest_lvn_above: TopographyNode | None = None
    nearest_lvn_below: TopographyNode | None = None
    nodes: tuple[TopographyNode, ...] = ()


class VolumeNodeEngine:
    """Builds auction topography from a price-volume profile."""

    def __init__(self: VolumeNodeEngine, config: VolumeNodeConfig | None = None) -> None:
        raw_config = config or VolumeNodeConfig()
        window = raw_config.smoothing_window
        if window % 2 == 0:
            window += 1
        self.config = raw_config.model_copy(update={"smoothing_window": window})
        self._kernel = _build_gaussian_kernel(
            self.config.smoothing_window,
            self.config.smoothing_sigma,
        )

    def compute_from_profile(
        self: VolumeNodeEngine,
        prices: np.ndarray,
        volumes: np.ndarray,
        *,
        timestamp: str | None = None,
        last_close: float | None = None,
    ) -> VolumeNodeTopography:
        """Compute topography from sorted profile arrays."""
        if len(prices) < 3 or len(volumes) < 3:
            return VolumeNodeTopography(ok=False, error="Need at least 3 profile bins")

        order = np.argsort(prices)
        ordered_prices = prices[order].astype(float)
        ordered_volumes = np.maximum(volumes[order].astype(float), 0.0)
        total_volume = float(ordered_volumes.sum())
        if total_volume <= 0:
            return VolumeNodeTopography(ok=False, error="Zero total profile volume")

        smoothed = (
            _apply_sma(ordered_volumes, self.config.smoothing_window)
            if self.config.smoothing_method == "sma"
            else _convolve_reflect(ordered_volumes, self._kernel)
        )
        hvn_candidates, lvn_candidates = _detect_extrema(smoothed)
        nodes = self._filter_by_prominence(
            ordered_prices,
            ordered_volumes,
            smoothed,
            hvn_candidates,
            lvn_candidates,
        )
        if self.config.edge_bins_as_lvn:
            nodes.extend(self._infer_edge_lvns(ordered_prices, ordered_volumes, smoothed, nodes))

        nodes = _decorate_distances(
            sorted(nodes, key=lambda node: node.price),
            last_close,
        )
        trimmed_nodes = tuple(
            sorted(nodes, key=lambda node: node.prominence, reverse=True)[: self.config.max_nodes]
        )
        ordered_trimmed = tuple(sorted(trimmed_nodes, key=lambda node: node.price))
        poc_idx = int(np.argmax(ordered_volumes))

        return VolumeNodeTopography(
            enabled=True,
            ok=True,
            timestamp=timestamp,
            poc_price=float(ordered_prices[poc_idx]),
            last_close=last_close,
            node_count=len(nodes),
            hvn_count=sum(1 for node in nodes if node.type is VolumeNodeType.HVN),
            lvn_count=sum(1 for node in nodes if node.type is VolumeNodeType.LVN),
            nearest_hvn_above=_nearest_node(nodes, last_close, VolumeNodeType.HVN, "above"),
            nearest_hvn_below=_nearest_node(nodes, last_close, VolumeNodeType.HVN, "below"),
            nearest_lvn_above=_nearest_node(nodes, last_close, VolumeNodeType.LVN, "above"),
            nearest_lvn_below=_nearest_node(nodes, last_close, VolumeNodeType.LVN, "below"),
            nodes=ordered_trimmed,
        )

    def compute_from_ohlcv(self: VolumeNodeEngine, df: pd.DataFrame) -> VolumeNodeTopography:
        """Build a volume profile from OHLCV and compute topography."""
        frame = _validate_frame(df)
        if len(frame) < 5:
            return VolumeNodeTopography(ok=False, error=f"Insufficient data ({len(frame)})")

        profile = VolumeAnalytics.compute_profile(frame, n_bins=self.config.n_bins)
        if not profile.success:
            return VolumeNodeTopography(ok=False, error=profile.error or "Volume profile failed")

        timestamp = _last_timestamp(frame)
        last_close = float(frame["close"].iloc[-1]) if "close" in frame.columns else None
        return self.compute_from_profile(
            np.asarray(profile.price_levels, dtype=float),
            np.asarray(profile.volume_at_price, dtype=float),
            timestamp=timestamp,
            last_close=last_close,
        )

    def _filter_by_prominence(
        self: VolumeNodeEngine,
        prices: np.ndarray,
        volumes: np.ndarray,
        smoothed: np.ndarray,
        hvn_candidates: list[int],
        lvn_candidates: list[int],
    ) -> list[TopographyNode]:
        threshold = self.config.prominence_threshold
        nodes: list[TopographyNode] = []
        n = len(smoothed)

        for idx in hvn_candidates:
            peak = float(smoothed[idx])
            if peak <= 0:
                continue
            left_valley = _nearest_candidate_value(idx, "left", lvn_candidates, smoothed, n)
            right_valley = _nearest_candidate_value(idx, "right", lvn_candidates, smoothed, n)
            prominence = (peak - max(left_valley, right_valley)) / peak
            if prominence >= threshold:
                nodes.append(
                    TopographyNode(
                        price=float(prices[idx]),
                        volume=float(volumes[idx]),
                        smoothed=peak,
                        type=VolumeNodeType.HVN,
                        prominence=float(min(prominence, 1.0)),
                    )
                )

        for idx in lvn_candidates:
            valley = float(smoothed[idx])
            left_peak = _nearest_candidate_value(idx, "left", hvn_candidates, smoothed, n)
            right_peak = _nearest_candidate_value(idx, "right", hvn_candidates, smoothed, n)
            reference_peak = min(left_peak, right_peak)
            if reference_peak <= 0:
                continue
            prominence = (reference_peak - valley) / reference_peak
            if prominence >= threshold:
                nodes.append(
                    TopographyNode(
                        price=float(prices[idx]),
                        volume=float(volumes[idx]),
                        smoothed=valley,
                        type=VolumeNodeType.LVN,
                        prominence=float(min(prominence, 1.0)),
                    )
                )
        return nodes

    def _infer_edge_lvns(
        self: VolumeNodeEngine,
        prices: np.ndarray,
        volumes: np.ndarray,
        smoothed: np.ndarray,
        existing: list[TopographyNode],
    ) -> list[TopographyNode]:
        existing_prices = {node.price for node in existing}
        edges: list[TopographyNode] = []
        for idx in (0, len(prices) - 1):
            price = float(prices[idx])
            if price in existing_prices:
                continue
            edges.append(
                TopographyNode(
                    price=price,
                    volume=float(volumes[idx]),
                    smoothed=float(smoothed[idx]),
                    type=VolumeNodeType.LVN,
                    prominence=self.config.prominence_threshold,
                )
            )
        return edges


def analyze_volume_nodes_from_ohlcv(
    df: pd.DataFrame,
    config: VolumeNodeConfig | None = None,
) -> VolumeNodeTopography:
    """Analyze OHLCV and return JSON-safe volume-node topography."""
    try:
        return VolumeNodeEngine(config).compute_from_ohlcv(df)
    except Exception as exc:
        logger.exception("Volume node analysis failed")
        return VolumeNodeTopography(ok=False, error=str(exc))


def _build_gaussian_kernel(size: int, sigma: float) -> np.ndarray:
    half = size // 2
    values = np.asarray([exp(-((idx - half) ** 2) / (2 * sigma * sigma)) for idx in range(size)])
    total = float(values.sum())
    kernel = values / total if total > 0 else np.ones(size) / size
    return cast(np.ndarray, kernel)


def _convolve_reflect(signal: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    n = len(signal)
    half = len(kernel) // 2
    output: np.ndarray = np.zeros(n, dtype=float)
    for idx in range(n):
        acc = 0.0
        for k_idx, weight in enumerate(kernel):
            source_idx = idx + k_idx - half
            if source_idx < 0:
                source_idx = -source_idx
            if source_idx >= n:
                source_idx = 2 * (n - 1) - source_idx
            source_idx = max(0, min(n - 1, source_idx))
            acc += float(signal[source_idx]) * float(weight)
        output[idx] = acc
    return output


def _apply_sma(signal: np.ndarray, window: int) -> np.ndarray:
    half = window // 2
    output: np.ndarray = np.zeros(len(signal), dtype=float)
    for idx in range(len(signal)):
        lo = max(0, idx - half)
        hi = min(len(signal), idx + half + 1)
        output[idx] = float(signal[lo:hi].mean())
    return output


def _detect_extrema(smoothed: np.ndarray) -> tuple[list[int], list[int]]:
    hvn: list[int] = []
    lvn: list[int] = []
    for idx in range(1, len(smoothed) - 1):
        left = float(smoothed[idx - 1])
        mid = float(smoothed[idx])
        right = float(smoothed[idx + 1])
        if mid > left and mid > right:
            hvn.append(idx)
        elif mid < left and mid < right:
            lvn.append(idx)
    return hvn, lvn


def _nearest_candidate_value(
    idx: int,
    direction: Literal["left", "right"],
    candidates: list[int],
    smoothed: np.ndarray,
    n: int,
) -> float:
    if direction == "left":
        left = [candidate for candidate in candidates if candidate < idx]
        return float(smoothed[max(left)]) if left else float(smoothed[0])
    right = [candidate for candidate in candidates if candidate > idx]
    return float(smoothed[min(right)]) if right else float(smoothed[n - 1])


def _nearest_node(
    nodes: list[TopographyNode],
    current_price: float | None,
    node_type: VolumeNodeType,
    direction: Direction,
) -> TopographyNode | None:
    if current_price is None or not isfinite(current_price):
        return None
    pool = [node for node in nodes if node.type is node_type]
    if direction == "above":
        pool = [node for node in pool if node.price > current_price]
        return min(pool, key=lambda node: node.price - current_price) if pool else None
    if direction == "below":
        pool = [node for node in pool if node.price < current_price]
        return min(pool, key=lambda node: current_price - node.price) if pool else None
    return min(pool, key=lambda node: abs(node.price - current_price)) if pool else None


def _decorate_distances(
    nodes: list[TopographyNode],
    last_close: float | None,
) -> list[TopographyNode]:
    if last_close is None or not isfinite(last_close) or abs(last_close) < 1e-12:
        return nodes
    return [
        node.model_copy(
            update={
                "distance_to_last": float(node.price - last_close),
                "distance_pct_to_last": float(((node.price - last_close) / last_close) * 100.0),
            }
        )
        for node in nodes
    ]


def _validate_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Empty DataFrame")
    frame = df.copy()
    frame.columns = [str(col).lower() for col in frame.columns]
    required = {"high", "low", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    for column in ("open", "high", "low", "close", "volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=list(required))
    frame = frame[(frame["high"] >= frame["low"]) & (frame["volume"] > 0)]
    if "date" in frame.columns:
        if frame.index.name == "date":
            frame.index.name = None
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")
    elif isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.sort_index()
    return frame.reset_index(drop=False)


def _last_timestamp(frame: pd.DataFrame) -> str | None:
    if frame.empty:
        return None
    if "date" in frame.columns and pd.notna(frame["date"].iloc[-1]):
        return str(pd.Timestamp(frame["date"].iloc[-1]))
    if "index" in frame.columns and pd.notna(frame["index"].iloc[-1]):
        return str(frame["index"].iloc[-1])
    return None
