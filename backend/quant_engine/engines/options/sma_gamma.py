"""
SMA Gamma-Adjusted Engine
=========================
Calculates a Gamma-Adjusted SMA (Simple Moving Average) using institutional
GEX (Gamma Exposure) as the weighting vector. Bars where dealers are actively
hedging (high GEX) pull the average, revealing institutional support/resistance.
"""

import numpy as np
import pandas as pd


class SMAGammaEngine:
    """
    Stateful engine that calculates the Gamma-Adjusted SMA dynamically.
    """

    def __init__(
        self,
        ticker: str,
        period: int = 20,
        norm_window: int = 390,
        floor_weight: float = 0.10,
    ):
        self.ticker = ticker
        self.period = period
        self.norm_window = norm_window
        self.floor_weight = floor_weight

        self._close_buf: list[float] = []
        self._gex_buf: list[float] = []
        self._history: list[dict] = []

    def update(self, close: float, net_gex: float, timestamp: pd.Timestamp) -> dict:
        self._close_buf.append(close)
        self._gex_buf.append(net_gex)

        # Mantener buffer acotado (2x la ventana más grande)
        max_len = max(self.period, self.norm_window) * 2
        if len(self._close_buf) > max_len:
            self._close_buf.pop(0)
            self._gex_buf.pop(0)

        s_close = pd.Series(self._close_buf)
        s_gex = pd.Series(self._gex_buf)

        # 1. Normalizar GEX
        gex_abs = s_gex.abs()
        roll_min = gex_abs.rolling(self.norm_window, min_periods=1).min()
        roll_max = gex_abs.rolling(self.norm_window, min_periods=1).max()
        rango = (roll_max - roll_min).replace(0, np.nan)
        gex_norm = (gex_abs - roll_min) / rango
        gex_norm = gex_norm.fillna(0.5)
        gex_w = self.floor_weight + (1.0 - self.floor_weight) * gex_norm

        # 2. SMA Clásica
        sma = s_close.rolling(self.period).mean()

        # 3. SMA Gamma-Adjusted
        num = (s_close * gex_w).rolling(self.period).sum()
        den = gex_w.rolling(self.period).sum()
        sma_ga = num / den

        desviacion = sma_ga - sma

        # 4. Lógica de señales
        umbral = s_close * 0.0002
        sesgo = "NEUTRAL"

        current_desv = desviacion.iloc[-1] if not pd.isna(desviacion.iloc[-1]) else 0.0
        current_umbral = umbral.iloc[-1] if not pd.isna(umbral.iloc[-1]) else 0.0

        if current_desv > current_umbral:
            sesgo = "BULL"
        elif current_desv < -current_umbral:
            sesgo = "BEAR"

        signal = "NEUTRAL"
        strength = 0

        if len(s_close) >= 2 and len(sma_ga) >= 2:
            prev_close = s_close.iloc[-2]
            curr_close = s_close.iloc[-1]
            prev_smaga = sma_ga.iloc[-2]
            curr_smaga = sma_ga.iloc[-1]

            if not pd.isna(prev_smaga) and not pd.isna(curr_smaga):
                if curr_close > curr_smaga and prev_close <= prev_smaga and sesgo == "BULL":
                    signal = "LONG"
                    strength = 3
                elif curr_close < curr_smaga and prev_close >= prev_smaga and sesgo == "BEAR":
                    signal = "SHORT"
                    strength = 3

        curr_sma = sma.iloc[-1] if not pd.isna(sma.iloc[-1]) else close
        curr_sga = sma_ga.iloc[-1] if not pd.isna(sma_ga.iloc[-1]) else close

        res = {
            "timestamp": timestamp,
            "ticker": self.ticker,
            "close": close,
            "sma": float(curr_sma),
            "sma_ga": float(curr_sga),
            "bias": sesgo,
            "signal": signal,
            "strength": strength,
            "deviation": float(current_desv),
        }
        self._history.append(res)
        return res
