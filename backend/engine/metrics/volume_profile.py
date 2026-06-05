"""
backend/engine/metrics/volume_profile.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Volume Profile Engine — identifies price magnets and walls (HVN/LVN).
Stateless and vectorized implementation without pandas.
"""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.volume_profile")

type FloatArray = npt.NDArray[np.float64]


class VolumeNode(BaseModel):
    """Single node in the volume profile representing a price bin."""
    model_config = ConfigDict(frozen=True)

    price: float
    volume_pct: float
    node_type: str  # "HVN" | "LVN" | "POC" | "NORMAL"


class VolumeProfileReport(BaseModel):
    """Aggregate volume profile report for an asset."""
    model_config = ConfigDict(frozen=True)

    symbol: str
    poc: float          # Point of Control
    vah: float          # Value Area High (70% vol)
    val: float          # Value Area Low
    hvn_levels: list[float] = []
    lvn_levels: list[float] = []
    profile: list[VolumeNode] = []
    nodes_found: int = 0


class VolumeProfileEngine:
    """
    Calculates Volume Profile (Volume at Price) to identify liquidity nodes.
    Purely stateless and vectorized.
    """

    def analyze(
        self,
        symbol: str,
        price_volume_data: FloatArray,
        bins: int = 50,
    ) -> Result[VolumeProfileReport]:
        """
        Analyzes volume distribution across price levels.

        Parameters
        ----------
        symbol : str
            Symbol of the asset.
        price_volume_data : FloatArray
            2D NumPy array with shape (N, 2) where:
            0 = price
            1 = volume
        bins : int
            Number of price buckets/bins.

        Returns
        -------
        Result[VolumeProfileReport]
            The VolumeProfileReport wrapped in a Result monad.
        """
        try:
            # 1. Validations
            if price_volume_data.ndim != 2 or price_volume_data.shape[1] != 2:
                return Result.failure(
                    reason=(
                        f"price_volume_data must be a 2D array of shape (N, 2), "
                        f"got shape {price_volume_data.shape}"
                    )
                )

            n = len(price_volume_data)
            if n == 0:
                return Result.failure(reason="price_volume_data is empty")

            if np.any(np.isnan(price_volume_data)):
                return Result.failure(reason="Input data contains NaN values")

            prices = price_volume_data[:, 0]
            volumes = price_volume_data[:, 1]

            if np.any(volumes < 0.0):
                return Result.failure(reason="Volumes must be non-negative")

            if n < 5:
                # Return a safe neutral report
                return Result.success(
                    VolumeProfileReport(
                        symbol=symbol,
                        poc=0.0,
                        vah=0.0,
                        val=0.0,
                        hvn_levels=[],
                        lvn_levels=[],
                        profile=[],
                        nodes_found=0,
                    )
                )

            price_min = float(np.min(prices))
            price_max = float(np.max(prices))

            if price_min == price_max:
                # Return safe report with single level
                single_node = VolumeNode(price=price_min, volume_pct=1.0, node_type="POC")
                return Result.success(
                    VolumeProfileReport(
                        symbol=symbol,
                        poc=price_min,
                        vah=price_min,
                        val=price_min,
                        hvn_levels=[],
                        lvn_levels=[],
                        profile=[single_node],
                        nodes_found=0,
                    )
                )

            # 2. Binning vectorizado en C
            counts, bin_edges = np.histogram(prices, bins=bins, weights=volumes)
            bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
            bin_size = (price_max - price_min) / bins

            total_volume = float(np.sum(counts))
            if total_volume == 0.0:
                return Result.failure(reason="Total volume is zero")

            volume_pcts = counts / total_volume

            # 3. Find POC
            poc_idx = int(np.argmax(counts))
            poc_price = float(bin_centers[poc_idx])

            # 4. Find Value Area (70%)
            sort_idxs = np.argsort(counts)[::-1]
            sorted_counts = counts[sort_idxs]
            sorted_prices = bin_centers[sort_idxs]
            cum_vol = np.cumsum(sorted_counts)
            va_mask = cum_vol <= total_volume * 0.70

            if np.any(va_mask):
                vah = float(np.max(sorted_prices[va_mask]))
                val = float(np.min(sorted_prices[va_mask]))
            else:
                vah = val = poc_price

            # 5. Detección Vectorizada de Nodos (HVN / LVN)
            mean_vol = counts.mean()

            # HVN: Local peak
            is_peak = (counts[1:-1] > counts[:-2]) & (counts[1:-1] > counts[2:])
            hvn_mask = is_peak & (counts[1:-1] > mean_vol * 1.2)
            hvn_indices = np.where(hvn_mask)[0] + 1
            hvn_levels = [float(x) for x in bin_centers[hvn_indices]]

            # LVN: Local trough
            is_trough = (counts[1:-1] < counts[:-2]) & (counts[1:-1] < counts[2:])
            lvn_mask = is_trough & (counts[1:-1] < mean_vol * 0.6)
            lvn_indices = np.where(lvn_mask)[0] + 1
            lvn_levels = [float(x) for x in bin_centers[lvn_indices]]

            # Slices for top 5
            hvn_levels_sliced = hvn_levels[:5]
            lvn_levels_sliced = lvn_levels[:5]

            # 6. Build Node List for UI
            node_list = []
            for i in range(len(counts)):
                p = float(bin_centers[i])
                v = float(volume_pcts[i])
                ntype = "NORMAL"
                if abs(p - poc_price) < bin_size / 2:
                    ntype = "POC"
                elif any(abs(p - h) < bin_size / 2 for h in hvn_levels_sliced):
                    ntype = "HVN"
                elif any(abs(p - lvl) < bin_size / 2 for lvl in lvn_levels_sliced):
                    ntype = "LVN"
                node_list.append(VolumeNode(price=p, volume_pct=v, node_type=ntype))

            report = VolumeProfileReport(
                symbol=symbol,
                poc=poc_price,
                vah=vah,
                val=val,
                hvn_levels=hvn_levels_sliced,
                lvn_levels=lvn_levels_sliced,
                profile=node_list,
                nodes_found=len(hvn_levels) + len(lvn_levels),
            )
            return Result.success(report)

        except Exception as e:
            logger.error("VolumeProfile engine analysis failed: %s", e)
            return Result.failure(reason=f"VolumeProfile engine analysis failed: {e}")
