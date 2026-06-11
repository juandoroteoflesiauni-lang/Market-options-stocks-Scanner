"""Motor SMC Fractal FVG con Gate de Entropía — Sector Técnico.

Implementa la metodología de análisis Fractal FVG filtrada por Entropía de Shannon
para identificar regímenes de mercado ordenados y confluencias institucionales.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from ...math.technical.technical import atr, shannon_entropy

logger = logging.getLogger(__name__)

_ENTROPY_WINDOW: int = 20
_ENTROPY_BINS: int = 10
_ENTROPY_THRESHOLD: float = 3.2  # H < 3.2 = Mercado ordenado/tendencial
_DISPLACEMENT_MULT: float = 1.4  # Ratio cuerpo/ATR institucional
_FVG_MIN_SIZE_ATR: float = 0.5


class FractalSignal(BaseModel):
    """Señal de confluencia fractal SMC-FVG."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    timestamp: datetime
    bias: str = "CASH"  # "LONG" | "SHORT" | "CASH"
    fvg_size: float = 0.0
    entropy_score: float = 4.0
    is_fvg_active: bool = False


class EntropyScore(BaseModel):
    """Medición aislada del estado de entropía."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    timestamp: datetime
    value: float
    z_score: float = 0.0
    is_ordered: bool = False


class SMCFractalEngine:
    """Motor SMC de Confluencia Fractal con Entropy Gate."""

    @staticmethod
    def analyze(df_ohlcv: pd.DataFrame, ticker: str) -> FractalSignal:
        """Ejecuta el análisis Fractal FVG completo."""
        try:
            h = df_ohlcv["high"].to_numpy(dtype=float)
            l = df_ohlcv["low"].to_numpy(dtype=float)
            c = df_ohlcv["close"].to_numpy(dtype=float)

            log_returns = np.log(c[1:] / (c[:-1] + 1e-12))
            entropy_series = shannon_entropy(log_returns, n=_ENTROPY_WINDOW, bins=_ENTROPY_BINS)
            entropy_full = np.concatenate([[np.nan], entropy_series])
            current_h = float(entropy_full[-1]) if not np.isnan(entropy_full[-1]) else 4.0
            is_ordered = current_h < _ENTROPY_THRESHOLD

            fvg_active = False
            fvg_direction = "NEUTRAL"
            fvg_size = 0.0

            if len(l) >= 3:
                if l[-1] > h[-3]:
                    fvg_active = True
                    fvg_direction = "BULLISH"
                    fvg_size = l[-1] - h[-3]
                elif h[-1] < l[-3]:
                    fvg_active = True
                    fvg_direction = "BEARISH"
                    fvg_size = l[-3] - h[-1]

            atr_arr = atr(c, h, l, n=10)
            body = np.abs(
                df_ohlcv["close"].to_numpy(dtype=float) - df_ohlcv["open"].to_numpy(dtype=float)
            )
            displacement = 0.0
            if len(body) >= 2 and not np.isnan(atr_arr[-2]) and atr_arr[-2] > 0:
                displacement = body[-2] / atr_arr[-2]

            is_institutional = displacement >= _DISPLACEMENT_MULT
            prev_close = float(df_ohlcv["close"].iloc[-2]) if len(df_ohlcv) >= 2 else c[-1]
            prev_open = float(df_ohlcv["open"].iloc[-2]) if len(df_ohlcv) >= 2 else c[-1]
            impulse_bullish = prev_close > prev_open
            impulse_bearish = prev_close < prev_open

            bias = "CASH"
            if is_ordered:
                bull_ev = (fvg_active and fvg_direction == "BULLISH") or (
                    is_institutional and impulse_bullish
                )
                bear_ev = (fvg_active and fvg_direction == "BEARISH") or (
                    is_institutional and impulse_bearish
                )
                if bull_ev and not bear_ev:
                    bias = "LONG"
                elif bear_ev and not bull_ev:
                    bias = "SHORT"

            return FractalSignal(
                ticker=ticker,
                timestamp=datetime.now(UTC),
                bias=bias,
                fvg_size=float(fvg_size),
                entropy_score=float(current_h),
                is_fvg_active=fvg_active,
            )

        except Exception as exc:
            logger.exception("[SMCFractal] Error en análisis para %s: %s", ticker, exc)
            return FractalSignal(
                ticker=ticker,
                timestamp=datetime.now(UTC),
                bias="CASH",
                fvg_size=0.0,
                entropy_score=4.0,
                is_fvg_active=False,
            )

    @staticmethod
    def get_entropy_state(df: pd.DataFrame, ticker: str) -> EntropyScore:
        """Medición aislada del estado de entropía para el guardián de orquestación."""
        c = df["close"].to_numpy(dtype=float)
        log_returns = np.log(c[1:] / (c[:-1] + 1e-12))
        h_series = shannon_entropy(log_returns, n=_ENTROPY_WINDOW, bins=_ENTROPY_BINS)
        current_val = (
            float(h_series[-1]) if len(h_series) > 0 and not np.isnan(h_series[-1]) else 4.0
        )

        z = 0.0
        if len(h_series) > 50:
            baseline = h_series[~np.isnan(h_series)][-50:]
            if len(baseline) > 10:
                mu, sigma = np.mean(baseline), np.std(baseline)
                if sigma > 1e-6:
                    z = (current_val - mu) / sigma

        return EntropyScore(
            ticker=ticker,
            timestamp=datetime.now(UTC),
            value=current_val,
            z_score=float(z),
            is_ordered=current_val < _ENTROPY_THRESHOLD,
        )
