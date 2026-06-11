"""Núcleo matemático de Smart Money Concepts (SMC) — Sector Técnico.

Funciones vectorizadas puras numpy para detección de Order Blocks, Fair Value Gaps,
BOS/CHoCH, Liquidity Sweeps y cálculo de zona OTE.

Restricciones:
- Exclusivamente numpy.  Sin pandas, pydantic, logging ni capas de dominio.
- Toda división regularizada con _EPS = 1e-12.
- Funciones puras: reciben ndarray, retornan ndarray o tuplas de primitivos.
"""

from __future__ import annotations

import numpy as np

_EPS: float = 1e-12

# Parámetros calibrados por defecto
_DISPLACEMENT_DELTA: float = 1.3
_OB_LOOKBACK: int = 10
_OB_INVALIDATION_PCT: float = 0.50
_SWING_LOOKBACK: int = 5
_BOS_CONFIRM_MULT: float = 1.001
_LIQUIDITY_WINDOW: int = 20
_LIQ_VOL_FACTOR: float = 1.5
_OTE_LOW: float = 0.618
_OTE_HIGH: float = 0.786
_OTE_PIVOT_BARS: int = 40


# ─────────────────────────────────────────────────────────────────────────────
# §1  ATR (True Range vectorizado)
# ─────────────────────────────────────────────────────────────────────────────


def compute_atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    window: int = 14,
) -> np.ndarray:
    """Average True Range (Wilder) — shape (n,), NaN en prefijo."""
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = len(close)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    prev_c = close[:-1]
    tr[1:] = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prev_c), np.abs(low[1:] - prev_c)),
    )
    atr = np.full(n, np.nan, dtype=np.float64)
    if n < window:
        return atr
    atr[window - 1] = tr[:window].mean()
    alpha = 1.0 / window
    for i in range(window, n):
        atr[i] = atr[i - 1] * (1.0 - alpha) + tr[i] * alpha
    return atr


# ─────────────────────────────────────────────────────────────────────────────
# §2  ORDER BLOCKS
# ─────────────────────────────────────────────────────────────────────────────


def detect_order_blocks(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    atr: np.ndarray,
    vol_mean: np.ndarray,
    displacement_delta: float = _DISPLACEMENT_DELTA,
    invalidation_pct: float = _OB_INVALIDATION_PCT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Detecta Order Blocks institucionales (bullish y bearish).

    Returns
    -------
    bull_indices, bull_ob50, bear_indices, bear_ob50 : ndarray de int/float
        Índices de barras con OB y sus niveles 50 % respectivos.
    """
    open_ = np.asarray(open_, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    atr = np.asarray(atr, dtype=np.float64)
    vol_mean = np.asarray(vol_mean, dtype=np.float64)

    n = len(close)
    c_min_fwd = np.minimum.accumulate(close[::-1])[::-1]

    bull_idx, bull_ob50, bear_idx, bear_ob50 = [], [], [], []

    for t in range(n - 2):
        t1 = t + 1
        t2 = min(t + 2, n - 1)
        atr_t = atr[t]
        if not np.isfinite(atr_t) or atr_t < _EPS:
            continue

        h_t, l_t, o_t, c_t = high[t], low[t], open_[t], close[t]
        ob_50 = l_t + (h_t - l_t) * invalidation_pct
        delta_eff = abs(close[t1] - open_[t1]) / atr_t

        if (
            c_t < o_t
            and close[t1] > h_t
            and close[t2] > close[t1]
            and delta_eff >= displacement_delta
            and c_min_fwd[t1] >= ob_50
        ):
            bull_idx.append(t)
            bull_ob50.append(ob_50)

        elif (
            c_t > o_t
            and close[t1] < l_t
            and close[t2] < close[t1]
            and delta_eff >= displacement_delta
        ):
            bear_idx.append(t)
            bear_ob50.append(h_t - (h_t - l_t) * invalidation_pct)

    return (
        np.array(bull_idx, dtype=np.int64),
        np.array(bull_ob50, dtype=np.float64),
        np.array(bear_idx, dtype=np.int64),
        np.array(bear_ob50, dtype=np.float64),
    )


# ─────────────────────────────────────────────────────────────────────────────
# §3  FAIR VALUE GAPS
# ─────────────────────────────────────────────────────────────────────────────


def detect_fvg(
    high: np.ndarray,
    low: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Detecta Fair Value Gaps (3-bar imbalance patterns).

    Returns
    -------
    bull_bar_idx, bull_top, bull_bottom, bear_bar_idx, bear_size : ndarray
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    n = len(high)

    bull_bar, bull_top, bull_bot = [], [], []
    bear_bar, bear_top, bear_bot = [], [], []

    for i in range(1, n - 1):
        gap_bull = low[i + 1] - high[i - 1]
        gap_bear = low[i - 1] - high[i + 1]
        if gap_bull > 0:
            bull_bar.append(i)
            bull_top.append(low[i + 1])
            bull_bot.append(high[i - 1])
        elif gap_bear > 0:
            bear_bar.append(i)
            bear_top.append(low[i - 1])
            bear_bot.append(high[i + 1])

    return (
        np.array(bull_bar, dtype=np.int64),
        np.array(bull_top, dtype=np.float64),
        np.array(bull_bot, dtype=np.float64),
        np.array(bear_bar, dtype=np.int64),
        np.array([t - b for t, b in zip(bear_top, bear_bot, strict=False)], dtype=np.float64),
    )


# ─────────────────────────────────────────────────────────────────────────────
# §4  SWING POINTS — BOS / CHoCH
# ─────────────────────────────────────────────────────────────────────────────


def compute_swing_levels(
    high: np.ndarray,
    low: np.ndarray,
    lookback: int = _SWING_LOOKBACK,
) -> tuple[np.ndarray, np.ndarray]:
    """Rolling max/min de los `lookback` períodos anteriores (Swing High / Low).

    Returns
    -------
    swing_high, swing_low : ndarray shape (n,) con NaN en prefijo.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    n = len(high)
    sh = np.full(n, np.nan, dtype=np.float64)
    sl = np.full(n, np.nan, dtype=np.float64)
    for i in range(lookback, n):
        sh[i] = high[i - lookback : i].max()
        sl[i] = low[i - lookback : i].min()
    return sh, sl


def detect_bos_choch(
    close: np.ndarray,
    swing_high: np.ndarray,
    swing_low: np.ndarray,
    fvg_bar_indices: np.ndarray,
    bos_confirm_mult: float = _BOS_CONFIRM_MULT,
    lookback: int = _SWING_LOOKBACK,
) -> tuple[np.ndarray, np.ndarray]:
    """Detecta eventos BOS y CHoCH.

    Returns
    -------
    event_bar_indices, event_levels : ndarray (int64 y float64).
        Tipo codificado: 1 = BOS_BULL, 2 = BOS_BEAR, 3 = CHOCH_BULL, 4 = CHOCH_BEAR.
    Devuelve (event_indices, event_levels, event_types).
    """
    close = np.asarray(close, dtype=np.float64)
    swing_high = np.asarray(swing_high, dtype=np.float64)
    swing_low = np.asarray(swing_low, dtype=np.float64)
    fvg_set = set(int(x) for x in fvg_bar_indices)
    n = len(close)

    ev_idx, ev_lvl, ev_type = [], [], []

    for i in range(lookback, n):
        if not np.isfinite(swing_high[i]) or not np.isfinite(swing_low[i]):
            continue
        has_fvg = any(j in fvg_set for j in range(max(0, i - 1), i + 2))
        if not has_fvg:
            continue
        c = close[i]
        sh_prev = swing_high[i - lookback] if i >= lookback else np.nan
        sl_prev = swing_low[i - lookback] if i >= lookback else np.nan

        if c > swing_high[i] * bos_confirm_mult:
            etype = 3 if (np.isfinite(sl_prev) and swing_low[i] < sl_prev) else 1
            ev_idx.append(i)
            ev_lvl.append(swing_high[i])
            ev_type.append(etype)
        elif c < swing_low[i] * (2.0 - bos_confirm_mult):
            etype = 4 if (np.isfinite(sh_prev) and swing_high[i] > sh_prev) else 2
            ev_idx.append(i)
            ev_lvl.append(swing_low[i])
            ev_type.append(etype)

    return (
        np.array(ev_idx, dtype=np.int64),
        np.array(ev_lvl, dtype=np.float64),
        np.array(ev_type, dtype=np.int8),
    )


# ─────────────────────────────────────────────────────────────────────────────
# §5  LIQUIDITY SWEEPS
# ─────────────────────────────────────────────────────────────────────────────


def detect_liquidity_sweeps(
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    vol_mean: np.ndarray,
    window: int = _LIQUIDITY_WINDOW,
    vol_factor: float = _LIQ_VOL_FACTOR,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Detecta barridos de liquidez por volumen extremo sobre máximos/mínimos previos.

    Returns
    -------
    sweep_bar_indices, sweep_levels, sweep_type : ndarray
        sweep_type: 1 = BSL (bullish), 2 = SSL (bearish).
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    vol_mean = np.asarray(vol_mean, dtype=np.float64)
    n = len(high)

    sw_idx, sw_lvl, sw_type = [], [], []
    for i in range(window, n):
        if volume[i] <= vol_mean[i] * vol_factor:
            continue
        rh = float(high[i - window : i].max())
        rl = float(low[i - window : i].min())
        if high[i] > rh:
            sw_idx.append(i)
            sw_lvl.append(rh)
            sw_type.append(1)  # BSL
        elif low[i] < rl:
            sw_idx.append(i)
            sw_lvl.append(rl)
            sw_type.append(2)  # SSL

    return (
        np.array(sw_idx, dtype=np.int64),
        np.array(sw_lvl, dtype=np.float64),
        np.array(sw_type, dtype=np.int8),
    )


# ─────────────────────────────────────────────────────────────────────────────
# §6  OTE ZONE (Optimal Trade Entry — Fibonacci 61.8–78.6 %)
# ─────────────────────────────────────────────────────────────────────────────


def compute_ote_zone(
    high: np.ndarray,
    low: np.ndarray,
    pivot_index: int,
    pivot_bars: int = _OTE_PIVOT_BARS,
) -> tuple[float | None, float | None]:
    """Calcula la zona OTE (Fibonacci 61.8–78.6 %) a partir de un pivote de estructura.

    Returns
    -------
    ote_top, ote_bottom : float | None
    """
    start = max(0, pivot_index - pivot_bars)
    h_slice = np.asarray(high[start:pivot_index], dtype=np.float64)
    l_slice = np.asarray(low[start:pivot_index], dtype=np.float64)
    if len(h_slice) == 0:
        return None, None
    sh = float(h_slice.max())
    sl = float(l_slice.min())
    impulse = sh - sl
    return round(sh - _OTE_LOW * impulse, 8), round(sh - _OTE_HIGH * impulse, 8)


# ─────────────────────────────────────────────────────────────────────────────
# §7  ROLLING VOLUME MEAN (utilidad)
# ─────────────────────────────────────────────────────────────────────────────


def rolling_volume_mean(volume: np.ndarray, window: int) -> np.ndarray:
    """Media rodante de volumen sin pandas."""
    volume = np.asarray(volume, dtype=np.float64)
    n = len(volume)
    result = np.full(n, np.nan, dtype=np.float64)
    cumsum = np.cumsum(np.insert(volume, 0, 0))
    for i in range(window - 1, n):
        result[i] = (cumsum[i + 1] - cumsum[i - window + 1]) / window
    return result
