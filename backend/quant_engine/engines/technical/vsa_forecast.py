"""
backend/engine/metrics/vsa_forecast.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

VSA Forecast Engine: Volume Force Index (VFI) & Intra-Bar Modeling.
Estimates final candle metrics and aggregate volume flow intensity.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.vsa_forecast_engine")

FloatArray = npt.NDArray[np.float64]


class VSAForecastResult(BaseModel):
    """Result of the intra-bar forecasting model (Institutional Alignment)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    timestamp: datetime

    estimated_volume: float = Field(description="Projected volume at candle close")
    estimated_spread: float = Field(description="Projected high-low spread at candle close")
    confidence: float = Field(description="Confidence in projection (0-1)")
    is_climax_likely: bool = Field(description="True if estimated volume exceeds thresholds")


class VSAForecastEngine:
    """
    Volume Spread Analysis Engine.
    Combines trend intensity (VFI) with run-rate projections.
    """

    def __init__(
        self,
        vol_avg_period: int = 20,
        climax_threshold: float = 1.5,
    ) -> None:
        self.vol_avg_period = vol_avg_period
        self.climax_threshold = climax_threshold

    def calculate_vfi(self, ohlcv: FloatArray, period: int = 14) -> Result[dict[str, float]]:
        """
        Calculates the Volume Force Index (VFI).
        VFI measures the trend of money flow based on volume intensity.
        """
        try:
            n = len(ohlcv)
            # Need at least 2 * period - 1 to have any valid VFI value, plus 4 more for slope
            start_idx = 2 * period - 2
            if n <= start_idx + 4:
                return Result.failure(
                    reason=(
                        f"Insufficient history for VFI slope: "
                        f"need at least {start_idx + 5} rows, got {n}"
                    )
                )

            high = ohlcv[:, 1]
            low = ohlcv[:, 2]
            close = ohlcv[:, 3]
            volume = ohlcv[:, 4]

            if np.any(volume <= 0):
                return Result.failure(reason="Volume contains zero or negative values")

            typical_price = (high + low + close) / 3.0
            if np.any(typical_price <= 0):
                return Result.failure(reason="Typical price contains zero or negative values")

            # 1. Prepare typical price changes
            inter = np.log(typical_price[1:]) - np.log(typical_price[:-1])

            # 2. Rolling mean of volume using cumsum
            volume_mean = np.empty(n)
            volume_mean[:] = np.nan
            cumsum_vol = np.cumsum(volume)
            volume_mean[period - 1] = cumsum_vol[period - 1] / period
            volume_mean[period:] = (cumsum_vol[period:] - cumsum_vol[:-period]) / period

            # 3. Volume force components
            v_inter = np.log(volume) - np.log(volume_mean)

            force = np.empty(n)
            force[:] = np.nan
            force[period - 1 :] = inter[period - 2 :] * v_inter[period - 1 :]

            # 4. Rolling sum of force
            vfi = np.empty(n)
            vfi[:] = np.nan
            valid_force = np.nan_to_num(force)
            cumsum_force = np.cumsum(valid_force)

            vfi[start_idx] = cumsum_force[start_idx]
            for i in range(start_idx + 1, n):
                vfi[i] = cumsum_force[i] - cumsum_force[i - period]

            vfi_last = float(vfi[-1])
            vfi_slope = float(vfi[-1] - vfi[-5]) / 5.0

            return Result.success({"vfi": vfi_last, "slope": vfi_slope})
        except Exception as e:
            logger.error("VFI calculation failed: %s", e)
            return Result.failure(reason=f"VFI calculation failed: {e}")

    def predict_current_bar(
        self,
        ohlcv: FloatArray,
        seconds_elapsed: int,
        total_seconds: int,
        ticker: str = "UNKNOWN",
    ) -> Result[VSAForecastResult]:
        """
        Predicts final volume and spread for the current (incomplete) candle.
        Based on transaction run-rate.
        """
        ts = datetime.now(tz=UTC)
        try:
            if len(ohlcv) < 2:
                return Result.failure(reason="Insufficient historical data")

            current_v = float(ohlcv[-1, 4])
            current_s = float(ohlcv[-1, 1] - ohlcv[-1, 2])

            # 1. Run-rate Projection
            elapsed_ratio = max(0.001, seconds_elapsed / total_seconds)
            est_vol = current_v / elapsed_ratio

            # 2. Linear Spread Projection (sqrt scaling)
            est_spread = current_s * np.sqrt(1.0 / elapsed_ratio)

            # 3. Climax Detection
            hist_vol = ohlcv[:-1, 4]
            if len(hist_vol) == 0:
                return Result.failure(reason="Insufficient history to calculate volume average")

            avg_period = min(len(hist_vol), self.vol_avg_period)
            hist_vol_mean = float(np.mean(hist_vol[-avg_period:]))
            is_climax = est_vol > (hist_vol_mean * self.climax_threshold)

            # 4. Confidence (increases as we approach the close)
            confidence = np.clip(elapsed_ratio, 0.1, 0.95)

            prediction = VSAForecastResult(
                ticker=ticker,
                timestamp=ts,
                estimated_volume=round(est_vol, 2),
                estimated_spread=round(est_spread, 6),
                confidence=round(float(confidence), 2),
                is_climax_likely=bool(is_climax),
            )
            return Result.success(prediction)
        except Exception as e:
            logger.error("VSA Forecast error for %s: %s", ticker, e)
            return Result.failure(reason=f"VSA Forecast error: {e}")

    def detect_footprint_clusters(self, ohlcv: FloatArray) -> Result[tuple[float, float]]:
        """Identifies high-density volume clusters (Support/Resistance)."""
        try:
            n = len(ohlcv)
            if n == 0:
                return Result.failure(reason="ohlcv array is empty")

            volume = ohlcv[:, 4]
            sorted_indices = np.argsort(volume)
            top_k = min(n, 5)
            top_indices = sorted_indices[-top_k:]

            support = float(np.min(ohlcv[top_indices, 2]))
            resistance = float(np.max(ohlcv[top_indices, 1]))

            return Result.success((support, resistance))
        except Exception as e:
            logger.error("Footprint cluster detection failed: %s", e)
            return Result.failure(reason=f"Footprint cluster detection failed: {e}")

    def generate_vp_forecast(self, ohlcv: FloatArray) -> Result[bool]:
        """Predicts if a Volume Profile expansion is imminent."""
        try:
            n = len(ohlcv)
            if n < 20:
                return Result.failure(reason="Insufficient data: need at least 20 rows")

            recent_vol = np.mean(ohlcv[-5:, 4])
            prev_vol = np.mean(ohlcv[-20:-5, 4])

            vol_expansion = recent_vol > 0 if prev_vol <= 0 else recent_vol > prev_vol * 1.5

            recent_high_max = np.max(ohlcv[-5:, 1])
            recent_low_min = np.min(ohlcv[-5:, 2])
            prev_high_max = np.max(ohlcv[-20:-5, 1])
            prev_low_min = np.min(ohlcv[-20:-5, 2])

            recent_spread = recent_high_max - recent_low_min
            prev_spread = prev_high_max - prev_low_min

            price_compression = recent_spread < (prev_spread * 0.5)

            return Result.success(bool(vol_expansion and price_compression))
        except Exception as e:
            logger.error("Volume Profile forecast failed: %s", e)
            return Result.failure(reason=f"Volume Profile forecast failed: {e}")
