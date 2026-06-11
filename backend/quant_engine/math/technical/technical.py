"""Núcleo Matemático de Indicadores Técnicos — Sector Técnico.

Proporciona una librería de funciones matemáticas vectorizadas de alta performance
utilizando exclusivamente NumPy. Incluye indicadores clásicos, osciladores,
volatilidad, algoritmos institucionales y primitivas para Anchored VWAP.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import as_strided

# Constante de estabilidad numérica
_EPS: float = 1e-12


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS PRIVADOS
# ─────────────────────────────────────────────────────────────────────────────


def _f64(a: np.ndarray) -> np.ndarray:
    """Garantiza un array float64 contiguo en memoria C."""
    return np.ascontiguousarray(a, dtype=np.float64)


def _nan_full(n: int) -> np.ndarray:
    """Retorna un array de NaN float64 de longitud n."""
    out = np.empty(n, dtype=np.float64)
    out[:] = np.nan
    return out


def _rolling_window(a: np.ndarray, w: int) -> np.ndarray:
    """Crea una vista 2D de ventanas deslizantes sobre un array 1D (zero-copy)."""
    if len(a) < w:
        return np.empty((0, w), dtype=np.float64)
    shape = (len(a) - w + 1, w)
    strides = (a.strides[0], a.strides[0])
    return as_strided(a, shape=shape, strides=strides)


# ─────────────────────────────────────────────────────────────────────────────
# TechnicalMath — Librería Estática
# ─────────────────────────────────────────────────────────────────────────────


class TechnicalMath:
    """Librería de indicadores técnicos vectorizados — sin estado.

    Todas las entradas se convierten automáticamente a np.ndarray float64.
    Todas las salidas son np.ndarray float64 de la misma longitud que la entrada.
    """

    @staticmethod
    def sma(close: np.ndarray, n: int = 10) -> np.ndarray:
        """Media Móvil Simple (Simple Moving Average) — O(N) vía cumsum."""
        c = _f64(close)
        N = len(c)
        out = _nan_full(N)
        if n > N:
            return out
        cs = np.cumsum(c)
        out[n - 1] = cs[n - 1] / n
        out[n:] = (cs[n:] - cs[:-n]) / n
        return out

    @staticmethod
    def ema(close: np.ndarray, n: int = 12) -> np.ndarray:
        """Media Móvil Exponencial (EMA) — Maneja NaNs iniciales."""
        c = _f64(close)
        N = len(c)
        out = _nan_full(N)

        # Encontrar el primer índice no-NaN
        valid_indices = np.where(~np.isnan(c))[0]
        if len(valid_indices) == 0:
            return out

        start_idx = valid_indices[0]
        if (N - start_idx) < n:
            return out

        k = 2.0 / (n + 1.0)
        # Inicializar con el promedio de los primeros n valores válidos
        out[start_idx + n - 1] = c[start_idx : start_idx + n].mean()

        for i in range(start_idx + n, N):
            out[i] = c[i] * k + out[i - 1] * (1.0 - k)
        return out

    @staticmethod
    def smma(close: np.ndarray, n: int = 14, offset: int = 0) -> np.ndarray:
        """Media Móvil Suavizada de Wilder (SMMA / Smoothed MA)."""
        c = _f64(close)
        N = len(c)
        raw = _nan_full(N)
        if n > N:
            return raw
        raw[n - 1] = c[:n].mean()
        for i in range(n, N):
            raw[i] = (raw[i - 1] * (n - 1) + c[i]) / n
        if offset <= 0:
            return raw
        out = _nan_full(N)
        end = N - offset
        if end > 0:
            out[offset:] = raw[:end]
        return out

    @staticmethod
    def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
        """Relative Strength Index (RSI) corregido estilo Wilder."""
        c = _f64(close)
        N = len(c)
        out = _nan_full(N)
        if n + 1 > N:
            return out
        deltas = np.diff(c)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = gains[:n].mean()
        avg_loss = losses[:n].mean()
        out[n] = 100.0 - 100.0 / (1.0 + avg_gain / (avg_loss + _EPS))
        for i in range(n, len(deltas)):
            avg_gain = (avg_gain * (n - 1) + gains[i]) / n
            avg_loss = (avg_loss * (n - 1) + losses[i]) / n
            out[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / (avg_loss + _EPS))
        return out

    @staticmethod
    def rsi_hist(close: np.ndarray, n: int = 14, lookback: int = 5) -> np.ndarray:
        """RSI Histogram/Slope — Mide el momentum del RSI."""
        rsi = TechnicalMath.rsi(close, n)
        N = len(rsi)
        out = _nan_full(N)
        if lookback + 1 > N:
            return out
        # Calculamos la diferencia simple sobre el lookback
        out[lookback:] = rsi[lookback:] - rsi[:-lookback]
        return out

    @staticmethod
    def macd(
        close: np.ndarray,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Moving Average Convergence Divergence (MACD)."""
        ema_fast = TechnicalMath.ema(close, fast)
        ema_slow = TechnicalMath.ema(close, slow)
        macd_line = ema_fast - ema_slow
        signal_line = TechnicalMath.ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def atr(
        close: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        n: int = 14,
    ) -> np.ndarray:
        """Average True Range (ATR) de Wilder."""
        c, h, lo = _f64(close), _f64(high), _f64(low)
        N = len(c)
        out = _nan_full(N)
        if n > N:
            return out
        hl = h - lo
        h_cp = np.abs(h[1:] - c[:-1])
        l_cp = np.abs(lo[1:] - c[:-1])
        tr = np.empty(N, dtype=np.float64)
        tr[0] = h[0] - lo[0]
        tr[1:] = np.maximum(hl[1:], np.maximum(h_cp, l_cp))
        out[n - 1] = tr[:n].mean()
        for i in range(n, N):
            out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
        return out

    @staticmethod
    def bollinger(
        close: np.ndarray,
        n: int = 20,
        k: float = 2.0,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Bandas de Bollinger vectorizadas."""
        c = _f64(close)
        N = len(c)
        upper, mid, lower = _nan_full(N), _nan_full(N), _nan_full(N)
        if n > N:
            return upper, mid, lower
        wins = _rolling_window(c, n)
        m, s = wins.mean(axis=1), wins.std(axis=1, ddof=0)
        mid[n - 1 :] = m
        upper[n - 1 :] = m + k * s
        lower[n - 1 :] = m - k * s
        return upper, mid, lower

    @staticmethod
    def bbp(
        close: np.ndarray,
        n: int = 20,
        k: float = 2.0,
    ) -> np.ndarray:
        """Bollinger Band %B (Posición relativa)."""
        c = _f64(close)
        N = len(c)
        out = _nan_full(N)
        if n > N:
            return out
        upper, _, lower = TechnicalMath.bollinger(c, n, k)
        with np.errstate(divide="ignore", invalid="ignore"):
            out = (c - lower) / (upper - lower)
        return out

    @staticmethod
    def supertrend(
        close: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        n: int = 10,
        multiplier: float = 3.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        """SuperTrend Indicator."""
        c, h, lo = _f64(close), _f64(high), _f64(low)
        N = len(c)
        st = _nan_full(N)
        d = np.ones(N)
        if n > N:
            return st, d

        atr = TechnicalMath.atr(c, h, lo, n)
        hl2 = (h + lo) / 2.0
        basic_ub = hl2 + multiplier * atr
        basic_lb = hl2 - multiplier * atr

        final_ub = np.copy(basic_ub)
        final_lb = np.copy(basic_lb)

        final_ub[n - 1] = basic_ub[n - 1]
        final_lb[n - 1] = basic_lb[n - 1]
        st[n - 1] = final_lb[n - 1]
        d[n - 1] = 1

        for i in range(n, N):
            if basic_ub[i] < final_ub[i - 1] or c[i - 1] > final_ub[i - 1]:
                final_ub[i] = basic_ub[i]
            else:
                final_ub[i] = final_ub[i - 1]

            if basic_lb[i] > final_lb[i - 1] or c[i - 1] < final_lb[i - 1]:
                final_lb[i] = basic_lb[i]
            else:
                final_lb[i] = final_lb[i - 1]

            if st[i - 1] == final_ub[i - 1] and c[i] <= final_ub[i]:
                st[i] = final_ub[i]
                d[i] = -1
            elif (st[i - 1] == final_ub[i - 1] and c[i] > final_ub[i]) or (
                st[i - 1] == final_lb[i - 1] and c[i] >= final_lb[i]
            ):
                st[i] = final_lb[i]
                d[i] = 1
            elif st[i - 1] == final_lb[i - 1] and c[i] < final_lb[i]:
                st[i] = final_ub[i]
                d[i] = -1

        return st, d

    @staticmethod
    def vwap(
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
    ) -> np.ndarray:
        """Volume Weighted Average Price (VWAP) — Acumulativa por sesión/ventana."""
        h, lo, c, v = _f64(high), _f64(low), _f64(close), _f64(volume)
        tp = (h + lo + c) / 3.0
        pv = tp * v
        cumsum_pv, cumsum_v = np.cumsum(pv), np.cumsum(v)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(cumsum_v > _EPS, cumsum_pv / cumsum_v, tp)

    @staticmethod
    def ema_clusters(
        close: np.ndarray,
        periods: list[int] = [9, 21, 50, 200],
    ) -> dict[int, np.ndarray]:
        """Calcula un cluster de EMAs para análisis de colimación."""
        return {p: TechnicalMath.ema(close, p) for p in periods}

    @staticmethod
    def shannon_entropy(
        data: np.ndarray,
        n: int = 20,
        bins: int = 10,
    ) -> np.ndarray:
        """Rolling Shannon Entropy — Mide la aleatoriedad/complejidad del mercado."""
        d = _f64(data)
        N = len(d)
        out = _nan_full(N)
        if n > N:
            return out
        wins = _rolling_window(d, n)
        for i in range(len(wins)):
            counts, _ = np.histogram(wins[i], bins=bins)
            probs = counts / n
            p = probs[probs > 0]
            out[i + n - 1] = -np.sum(p * np.log2(p))
        return out


# ─────────────────────────────────────────────────────────────────────────────
# AVWAPMath — Primitivas Estadísticas para AVWAP
# ─────────────────────────────────────────────────────────────────────────────


class AVWAPMath:
    """Primitivas matemáticas para cálculos de Anchored VWAP y bandas de varianza."""

    @staticmethod
    def compute_anchored(
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
        anchor_idx: int = 0,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Calcula el AVWAP y la desviación estándar ponderada desde un anclaje."""
        n = len(high)
        avwap, std_dev = np.full(n, np.nan), np.full(n, np.nan)
        if anchor_idx < 0 or anchor_idx >= n:
            return avwap, std_dev

        h, lo, c, v = _f64(high), _f64(low), _f64(close), _f64(volume)
        tp = (h + lo + c) / 3.0
        pv_slice, v_slice = tp[anchor_idx:] * v[anchor_idx:], v[anchor_idx:]
        cum_pv, cum_v = np.cumsum(pv_slice), np.cumsum(v_slice)

        with np.errstate(divide="ignore", invalid="ignore"):
            av_slice = np.where(cum_v > _EPS, cum_pv / cum_v, tp[anchor_idx:])

        avwap[anchor_idx:] = av_slice
        sq_dev = (tp[anchor_idx:] - av_slice) ** 2
        cum_w_sq = np.cumsum(v_slice * sq_dev)

        with np.errstate(divide="ignore", invalid="ignore"):
            var_slice = np.where(cum_v > _EPS, cum_w_sq / cum_v, 0.0)

        std_dev[anchor_idx:] = np.sqrt(np.maximum(var_slice, 0.0))
        return avwap, std_dev
