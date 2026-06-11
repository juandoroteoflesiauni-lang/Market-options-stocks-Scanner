"""
backend/engine/metrics/volatility_surface.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Volatility Surface Engine — analyzes IV Skew and Smile dynamics.
Stateless and vectorized implementation without pandas.
"""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.volatility_surface")

type FloatArray = npt.NDArray[np.float64]


class SkewPoint(BaseModel):
    """Implied volatility skew point representing a historical period."""

    model_config = ConfigDict(frozen=True)

    periods_ago: int
    put_iv: float
    call_iv: float
    skew: float  # put_iv - call_iv


class VolSurfaceReport(BaseModel):
    """Aggregate implied volatility surface report."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    current_skew: float
    skew_percentile: float  # relative to history
    fear_regime: str  # "HIGH_SKEW" | "LOW_SKEW" | "NEUTRAL"
    put_call_iv_ratio: float
    historical_skew: list[SkewPoint] = []
    risk_signal: str = "NEUTRAL"


class VolatilitySurfaceEngine:
    """
    Analyzes the Implied Volatility surface and skew to detect tail risk.
    Purely stateless and vectorized.
    """

    def analyze(
        self,
        symbol: str,
        iv_data: FloatArray,
    ) -> Result[VolSurfaceReport]:
        """
        Calculates skew dynamics from historical Put/Call IV.

        Parameters
        ----------
        symbol : str
            Symbol of the asset.
        iv_data : FloatArray
            2D NumPy array of shape (N, 2) where:
            - Column 0: put_iv
            - Column 1: call_iv
            - Rows are ordered from most recent (row 0) to oldest (row N-1)

        Returns
        -------
        Result[VolSurfaceReport]
            The VolSurfaceReport wrapped in a Result monad.
        """
        try:
            # 1. Validations
            if iv_data.ndim != 2 or iv_data.shape[1] != 2:
                return Result.failure(
                    reason=(
                        f"iv_data must be a 2D array of shape (N, 2), " f"got shape {iv_data.shape}"
                    )
                )

            n = len(iv_data)
            if n < 2:
                return Result.failure(
                    reason=f"iv_data must contain at least 2 rows of historical data, got {n}"
                )

            if np.any(np.isnan(iv_data)):
                return Result.failure(reason="iv_data contains NaN values")

            if np.any(iv_data < 0.0):
                return Result.failure(reason="iv_data contains negative implied volatilities")

            # Check if current (index 0) call_iv is zero
            if iv_data[0, 1] == 0.0:
                return Result.failure(reason="Current call_iv is zero, division by zero prevented")

            # 2. Vectorization of skews
            skews = iv_data[:, 0] - iv_data[:, 1]
            curr_skew = float(skews[0])
            curr_ratio = float(iv_data[0, 0] / iv_data[0, 1])

            # 3. Percentile calculation
            skew_percentile = float(np.sum(skews <= curr_skew) / n)

            # 4. Regime determination
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

            # 6. Map history up to 30 periods
            historical_skew = []
            limit = min(30, n)
            for i in range(limit):
                historical_skew.append(
                    SkewPoint(
                        periods_ago=i,
                        put_iv=float(iv_data[i, 0]),
                        call_iv=float(iv_data[i, 1]),
                        skew=float(skews[i]),
                    )
                )

            report = VolSurfaceReport(
                symbol=symbol,
                current_skew=round(curr_skew, 4),
                skew_percentile=round(skew_percentile, 4),
                fear_regime=fear_regime,
                put_call_iv_ratio=round(curr_ratio, 4),
                historical_skew=historical_skew,
                risk_signal=risk_signal,
            )
            return Result.success(report)

        except Exception as e:
            logger.error("VolatilitySurface engine analysis failed: %s", e)
            return Result.failure(reason=f"VolatilitySurface engine analysis failed: {e}")
