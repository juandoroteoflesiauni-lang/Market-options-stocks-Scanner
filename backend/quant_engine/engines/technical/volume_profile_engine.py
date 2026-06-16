from __future__ import annotations
"""
backend/layer_3_specialists/ia_probabilistico/engines/volume_profile_engine.py
════════════════════════════════════════════════════════════════════════════════
Volume Profile Engine — identifies price magnets and walls (HVN/LVN).

Strategy:
  1. Bin historical price/volume data into N buckets.
  2. Identify the Point of Control (POC) and Value Area (VAH/VAL).
  3. Detect High Volume Nodes (HVN) — historical price magnets.
  4. Detect Low Volume Nodes (LVN) — price voids where movement is fast.
════════════════════════════════════════════════════════════════════════════════
"""


import logging
from dataclasses import dataclass, field

import pandas as pd


logger = logging.getLogger(__name__)


@dataclass
class VolumeNode:
    price: float
    volume_pct: float
    node_type: str  # "HVN" | "LVN" | "POC" | "NORMAL"


@dataclass
class VolumeProfileReport:
    symbol: str
    poc: float  # Point of Control
    vah: float  # Value Area High (70% vol)
    val: float  # Value Area Low
    hvn_levels: list[float] = field(default_factory=list)
    lvn_levels: list[float] = field(default_factory=list)
    profile: list[VolumeNode] = field(default_factory=list)
    nodes_found: int = 0


class VolumeProfileEngine:
    """
    Calculates Volume Profile (Volume at Price) to identify liquidity nodes.
    """

    def analyze(self, symbol: str, df: pd.DataFrame, bins: int = 50) -> VolumeProfileReport:
        """
        Analyzes volume distribution across price levels.
        Expects df with 'close' and 'volume' columns.
        """
        if df.empty or len(df) < 5:
            return VolumeProfileReport(symbol=symbol, poc=0, vah=0, val=0)

        price_min = df["close"].min()
        price_max = df["close"].max()

        if price_min == price_max:
            return VolumeProfileReport(symbol=symbol, poc=price_min, vah=price_min, val=price_min)

        # 1. Create Bins
        bin_size = (price_max - price_min) / bins
        df["bin"] = ((df["close"] - price_min) / bin_size).astype(int).clip(0, bins - 1)

        # 2. Aggregate Volume per Bin
        profile_df = df.groupby("bin")["volume"].sum().reset_index()
        profile_df["price"] = price_min + (profile_df["bin"] * bin_size) + (bin_size / 2)

        total_volume = profile_df["volume"].sum()
        profile_df["volume_pct"] = profile_df["volume"] / total_volume

        # 3. Find POC
        poc_idx = profile_df["volume"].idxmax()
        poc_price = profile_df.loc[poc_idx, "price"]

        # 4. Find Value Area (70%)
        sorted_profile = profile_df.sort_values("volume", ascending=False)
        sorted_profile["cum_vol"] = sorted_profile["volume"].cumsum()
        va_df = sorted_profile[sorted_profile["cum_vol"] <= total_volume * 0.70]

        if not va_df.empty:
            vah = va_df["price"].max()
            val = va_df["price"].min()
        else:
            vah = val = poc_price

        # 5. Identify HVN/LVN (Local Maxima/Minima)
        hvn_levels = []
        lvn_levels = []
        vol_arr = profile_df["volume"].values

        for i in range(1, len(vol_arr) - 1):
            # HVN: Local peak
            if vol_arr[i] > vol_arr[i - 1] and vol_arr[i] > vol_arr[i + 1]:
                if vol_arr[i] > profile_df["volume"].mean() * 1.2:
                    hvn_levels.append(float(profile_df.loc[i, "price"]))

            # LVN: Local trough
            if vol_arr[i] < vol_arr[i - 1] and vol_arr[i] < vol_arr[i + 1]:
                if vol_arr[i] < profile_df["volume"].mean() * 0.6:
                    lvn_levels.append(float(profile_df.loc[i, "price"]))

        # 6. Build Node List for UI
        node_list = []
        for idx, row in profile_df.iterrows():
            p = float(row["price"])
            v = float(row["volume_pct"])
            ntype = "NORMAL"
            if abs(p - poc_price) < bin_size / 2:
                ntype = "POC"
            elif any(abs(p - h) < bin_size / 2 for h in hvn_levels):
                ntype = "HVN"
            elif any(abs(p - l) < bin_size / 2 for l in lvn_levels):
                ntype = "LVN"

            node_list.append(VolumeNode(price=p, volume_pct=v, node_type=ntype))

        return VolumeProfileReport(
            symbol=symbol,
            poc=float(poc_price),
            vah=float(vah),
            val=float(val),
            hvn_levels=hvn_levels[:5],  # top 5
            lvn_levels=lvn_levels[:5],
            profile=node_list,
            nodes_found=len(hvn_levels) + len(lvn_levels),
        )
