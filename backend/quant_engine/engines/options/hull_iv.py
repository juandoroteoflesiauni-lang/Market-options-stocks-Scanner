"""
Hull IV Suite Engine
====================
HMA (Hull Moving Average) classic + dynamic bands calculated with implied IV ATM.
The channel width expands in high IV regimes and compresses in low IV.
"""

import math

import numpy as np
import pandas as pd


class HullIVEngine:
    def __init__(
        self,
        ticker: str,
        hma_period: int = 9,
        k: float = 1.0,
        barras_por_dia: int = 390,
        dias_trading: int = 252,
        ventana_regimen: int = 390,
        evitar_alta_iv: bool = True,
    ):
        self.ticker = ticker
        self.hma_period = hma_period
        self.k = k
        self.barras_por_dia = barras_por_dia
        self.dias_trading = dias_trading
        self.ventana_regimen = ventana_regimen
        self.evitar_alta_iv = evitar_alta_iv

        self._close_buf: list[float] = []
        self._high_buf: list[float] = []
        self._low_buf: list[float] = []
        self._iv_buf: list[float] = []
        self._history: list[dict] = []

    def _wma(self, series: pd.Series, period: int) -> pd.Series:
        weights = np.arange(1, period + 1, dtype=float)
        return series.rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)

    def _hma(self, series: pd.Series, period: int) -> pd.Series:
        half = max(2, period // 2)
        sqrt_ = max(2, int(math.floor(math.sqrt(period))))
        raw = 2 * self._wma(series, half) - self._wma(series, period)
        return self._wma(raw, sqrt_)

    def update(
        self, high: float, low: float, close: float, iv_atm: float, timestamp: pd.Timestamp
    ) -> dict:
        self._high_buf.append(high)
        self._low_buf.append(low)
        self._close_buf.append(close)
        self._iv_buf.append(iv_atm)

        max_len = max(self.hma_period * 2, self.ventana_regimen) + 50
        if len(self._close_buf) > max_len:
            self._high_buf.pop(0)
            self._low_buf.pop(0)
            self._close_buf.pop(0)
            self._iv_buf.pop(0)

        s_close = pd.Series(self._close_buf)
        s_high = pd.Series(self._high_buf)
        s_low = pd.Series(self._low_buf)
        s_iv = pd.Series(self._iv_buf)

        # HMA
        h = self._hma(s_close, self.hma_period)

        # IV por barra
        iv_diaria = s_iv / math.sqrt(self.dias_trading)
        iv_b = iv_diaria / math.sqrt(self.barras_por_dia)

        banda_sup = h + self.k * s_close * iv_b
        banda_inf = h - self.k * s_close * iv_b
        ancho = banda_sup - banda_inf

        # Regimen
        iv_p75 = s_iv.rolling(self.ventana_regimen, min_periods=1).quantile(0.75)
        iv_p25 = s_iv.rolling(self.ventana_regimen, min_periods=1).quantile(0.25)

        regimen = "normal"
        if len(s_iv) > 0:
            if s_iv.iloc[-1] <= iv_p25.iloc[-1]:
                regimen = "baja_iv"
            elif s_iv.iloc[-1] >= iv_p75.iloc[-1]:
                regimen = "alta_iv"

        # Señales
        signal = "NEUTRAL"
        strength = 0

        if (
            len(s_close) >= 2
            and not pd.isna(banda_sup.iloc[-1])
            and not pd.isna(banda_sup.iloc[-2])
        ):
            rompe_sup = (
                s_close.iloc[-1] > banda_sup.iloc[-1] and s_high.iloc[-2] < banda_sup.iloc[-2]
            )
            rompe_inf = (
                s_close.iloc[-1] < banda_inf.iloc[-1] and s_low.iloc[-2] > banda_inf.iloc[-2]
            )

            en_alta_iv = regimen == "alta_iv"
            if self.evitar_alta_iv and en_alta_iv:
                rompe_sup = False
                rompe_inf = False

            if rompe_sup:
                signal = "LONG"
                strength = 3
            elif rompe_inf:
                signal = "SHORT"
                strength = 3

        curr_hma = float(h.iloc[-1]) if not pd.isna(h.iloc[-1]) else close
        curr_sup = float(banda_sup.iloc[-1]) if not pd.isna(banda_sup.iloc[-1]) else close
        curr_inf = float(banda_inf.iloc[-1]) if not pd.isna(banda_inf.iloc[-1]) else close
        curr_ancho = float(ancho.iloc[-1]) if not pd.isna(ancho.iloc[-1]) else 0.0

        res = {
            "timestamp": timestamp,
            "ticker": self.ticker,
            "signal": signal,
            "strength": strength,
            "hma": curr_hma,
            "banda_sup": curr_sup,
            "banda_inf": curr_inf,
            "ancho_banda": curr_ancho,
            "regimen": regimen,
            "stop_dist": curr_ancho / 2.0,
        }
        self._history.append(res)
        return res
