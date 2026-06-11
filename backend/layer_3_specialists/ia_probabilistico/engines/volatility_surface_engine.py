"""
backend/layer_3_specialists/ia_probabilistico/engines/volatility_surface_engine.py
════════════════════════════════════════════════════════════════════════════════
Volatility Surface Engine — analyzes IV Skew and Smile dynamics.

Purpose:
  - Detect "Institutional Fear" by comparing Put IV vs Call IV.
  - Analyze IV Skew (tail risk hedging).
  - Identify IV Smirks (abnormal demand for specific strikes).
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SkewPoint:
    date: str
    put_iv: float
    call_iv: float
    skew: float  # put_iv - call_iv


@dataclass
class VolSurfaceReport:
    symbol: str
    current_skew: float
    skew_percentile: float  # relative to history
    fear_regime: str  # "CONTANGO" | "BACKWARDATION" | "HIGH_SKEW" | "NEUTRAL"
    put_call_iv_ratio: float
    historical_skew: list[SkewPoint] = field(default_factory=list)
    risk_signal: str = "NEUTRAL"


class VolatilitySurfaceEngine:
    """
    Analyzes the Implied Volatility surface and skew to detect tail risk.
    """

    def analyze(
        self, symbol: str, iv_history: list[Any]  # Expects list of FMPOptionsIVHistorical
    ) -> VolSurfaceReport:
        """
        Calculates skew dynamics from historical Put/Call IV.
        """
        if not iv_history:
            return VolSurfaceReport(
                symbol=symbol,
                current_skew=0,
                skew_percentile=0,
                fear_regime="UNKNOWN",
                put_call_iv_ratio=1.0,
            )

        # 1. Map history
        skew_series = []
        for h in iv_history:
            p_iv = h.putIv or 0
            c_iv = h.callIv or 0
            skew_series.append(
                SkewPoint(date=h.date or "", put_iv=p_iv, call_iv=c_iv, skew=p_iv - c_iv)
            )

        if not skew_series:
            return VolSurfaceReport(
                symbol=symbol,
                current_skew=0,
                skew_percentile=0,
                fear_regime="UNKNOWN",
                put_call_iv_ratio=1.0,
            )

        # 2. Latest metrics
        latest = skew_series[0]
        curr_skew = latest.skew
        curr_ratio = latest.put_iv / latest.call_iv if latest.call_iv > 0 else 1.0

        # 3. Percentile calculation
        all_skews = [s.skew for s in skew_series]
        if len(all_skews) > 1:
            skew_percentile = float(np.percentile(all_skews, curr_skew))  # Simplistic
            # More accurate percentile:
            count = sum(1 for s in all_skews if s <= curr_skew)
            skew_percentile = count / len(all_skews)
        else:
            skew_percentile = 0.5

        # 4. Regime determination
        # High Skew (> 2 SD or high percentile) indicates hedging demand
        if skew_percentile > 0.85:
            fear_regime = "HIGH_SKEW"
            risk_signal = "BEARISH_HEDGING"
        elif skew_percentile < 0.15:
            fear_regime = "LOW_SKEW"
            risk_signal = "COMPLACENCY"
        else:
            fear_regime = "NEUTRAL"
            risk_signal = "NEUTRAL"

        # 5. Overwrite signal if ratio is extreme
        if curr_ratio > 1.5:
            risk_signal = "EXTREME_PUT_DEMAND"
        elif curr_ratio < 0.7:
            risk_signal = "BULLISH_SPECULATION"

        return VolSurfaceReport(
            symbol=symbol,
            current_skew=round(curr_skew, 4),
            skew_percentile=round(skew_percentile, 4),
            fear_regime=fear_regime,
            put_call_iv_ratio=round(curr_ratio, 4),
            historical_skew=skew_series[:30],  # last 30 days
            risk_signal=risk_signal,
        )
