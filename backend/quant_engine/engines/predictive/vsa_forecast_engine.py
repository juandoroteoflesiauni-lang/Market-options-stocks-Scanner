"""
backend/layer_3_specialists/ia_probabilistico/engines/vsa_forecast_engine.py
════════════════════════════════════════════════════════════════════════════════
VSA Forecast Engine: Volume Force Index (VFI) & Intra-Bar Modeling.
Estimates final candle metrics and aggregate volume flow intensity.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("quantumbeta.vsa_forecast_engine")


class VSAForecastResult(BaseModel):
    """Result of the intra-bar forecasting model (Institutional Alignment)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    timestamp: datetime

    estimated_volume: float = Field(description="Projected volume at candle close")
    estimated_spread: float = Field(description="Projected high-low spread at candle close")
    confidence: float = Field(description="Confidence in projection (0-1)")
    is_climax_likely: bool = Field(description="True if estimated volume exceeds thresholds")

    ok: bool = True
    error: str | None = None


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

    def calculate_vfi(self, df: pd.DataFrame, period: int = 14) -> dict[str, float]:
        """
        Calculates the Volume Force Index (VFI).
        VFI measures the trend of money flow based on volume intensity.
        """
        try:
            typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
            inter = np.log(typical_price) - np.log(typical_price.shift(1))
            v_inter = np.log(df["volume"]) - np.log(df["volume"].rolling(period).mean())

            # Weighted Force
            force = inter * v_inter
            vfi = force.rolling(period).sum()

            vfi_last = float(vfi.iloc[-1])
            vfi_slope = float(vfi.iloc[-1] - vfi.iloc[-5]) / 5.0

            return {"vfi": vfi_last, "slope": vfi_slope}
        except Exception as e:
            logger.error(f"VFI calculation failed: {e}")
            return {"vfi": 0.0, "slope": 0.0}

    def predict_current_bar(
        self,
        df_ohlcv: pd.DataFrame,
        seconds_elapsed: int,
        total_seconds: int,
        ticker: str = "UNKNOWN",
    ) -> VSAForecastResult:
        """
        Predicts final volume and spread for the current (incomplete) candle.
        Based on transaction run-rate.
        """
        ts = datetime.now(tz=UTC)
        try:
            if len(df_ohlcv) < 2:
                return VSAForecastResult(
                    ticker=ticker,
                    timestamp=ts,
                    estimated_volume=0.0,
                    estimated_spread=0.0,
                    confidence=0.0,
                    is_climax_likely=False,
                    ok=False,
                    error="Insufficient historical data",
                )

            current_v = float(df_ohlcv["volume"].iloc[-1])
            current_s = float(df_ohlcv["high"].iloc[-1] - df_ohlcv["low"].iloc[-1])

            # 1. Run-rate Projection
            elapsed_ratio = max(0.001, seconds_elapsed / total_seconds)
            est_vol = current_v / elapsed_ratio

            # 2. Linear Spread Projection (sqrt scaling)
            est_spread = current_s * np.sqrt(1.0 / elapsed_ratio)

            # 3. Climax Detection
            hist_vol_mean = df_ohlcv["volume"].iloc[:-1].tail(self.vol_avg_period).mean()
            is_climax = est_vol > (hist_vol_mean * self.climax_threshold)

            # 4. Confidence (increases as we approach the close)
            confidence = np.clip(elapsed_ratio, 0.1, 0.95)

            return VSAForecastResult(
                ticker=ticker,
                timestamp=ts,
                estimated_volume=round(est_vol, 2),
                estimated_spread=round(est_spread, 6),
                confidence=round(float(confidence), 2),
                is_climax_likely=is_climax,
                ok=True,
            )
        except Exception as e:
            logger.error(f"VSA Forecast error for {ticker}: {e}")
            return VSAForecastResult(
                ticker=ticker,
                timestamp=ts,
                estimated_volume=0.0,
                estimated_spread=0.0,
                confidence=0.0,
                is_climax_likely=False,
                ok=False,
                error=str(e),
            )

    def detect_footprint_clusters(self, df: pd.DataFrame) -> tuple[float, float]:
        """Identifies high-density volume clusters (Support/Resistance)."""
        try:
            sorted_by_vol = df.sort_values("volume", ascending=False).head(5)
            support = float(sorted_by_vol["low"].min())
            resistance = float(sorted_by_vol["high"].max())
            return support, resistance
        except Exception:
            return 0.0, 0.0

    def generate_vp_forecast(self, df: pd.DataFrame) -> bool:
        """Predicts if a Volume Profile expansion is imminent."""
        try:
            recent_vol = df["volume"].iloc[-5:].mean()
            prev_vol = df["volume"].iloc[-20:-5].mean()
            vol_expansion = recent_vol > prev_vol * 1.5
            price_compression = (df["high"].iloc[-5:].max() - df["low"].iloc[-5:].min()) < (
                (df["high"].iloc[-20:-5].max() - df["low"].iloc[-20:-5].min()) * 0.5
            )
            return bool(vol_expansion and price_compression)
        except Exception:
            return False


# ────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: IA / PROBABILÍSTICO
# Archivo        : vsa_forecast_engine.py
# Sub-capa       : Engine (Volume Spread Analysis)
# Framework ML   : pandas/numpy
# Descripcion    : Integración institutional de Volume Force Index y Intra-bar projection.
# ────────────────────────────────────────────────────────────────
