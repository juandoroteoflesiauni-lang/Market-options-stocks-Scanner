"""Núcleo matemático de Volume Spread Analysis (VSA) — Sector Técnico.

Funciones vectorizadas puras numpy para clasificación de barras VSA,
anomalías de absorción, MFI Kinético y Weis Wave.

Restricciones:
- Exclusivamente numpy.  Sin pandas, pydantic, logging ni capas de dominio.
- Toda división regularizada con _EPS = 1e-12.
"""

from __future__ import annotations

import numpy as np

_EPS: float = 1e-12

# Parámetros por defecto calibrados
_DEFAULT_VOL_WINDOW: int = 20
_DEFAULT_ABSORPTION_WINDOW: int = 20
_DEFAULT_ABSORPTION_THRESHOLD: float = 2.0
_DEFAULT_CLIMAX_VOL_PERCENTILE: float = 90.0
_DEFAULT_CLIMAX_VOL_WINDOW: int = 50
_DEFAULT_WEIS_WAVE_THRESHOLD: float = 0.02
_VZ_CLIMAX: float = 2.5
_VZ_LOW: float = -1.0
_VZ_EFFORT: float = 1.5
_CLOSE_RATIO_HIGH: float = 0.70
_CLOSE_RATIO_LOW: float = 0.70
_SPREAD_NARROW_RATIO: float = 0.70


# ─────────────────────────────────────────────────────────────────────────────
# §1  VOLUME Z-SCORE
# ─────────────────────────────────────────────────────────────────────────────


def compute_volume_zscore(
    volume: np.ndarray,
    window: int = _DEFAULT_VOL_WINDOW,
) -> np.ndarray:
    """Z-score de volumen rodante sobre una ventana deslizante.

    Returns
    -------
    vz : ndarray shape (n,) con NaN en las primeras (window-1) posiciones.
    """
    volume = np.asarray(volume, dtype=np.float64)
    n = len(volume)
    vz = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        w = volume[i - window + 1 : i + 1]
        mu = w.mean()
        sigma = w.std(ddof=1)
        vz[i] = (volume[i] - mu) / (sigma + _EPS)
    return vz


# ─────────────────────────────────────────────────────────────────────────────
# §2  CLOSE LOCATION & SPREAD
# ─────────────────────────────────────────────────────────────────────────────


def compute_close_location(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Ubicación del cierre dentro del rango de la barra y spread porcentual.

    Returns
    -------
    close_location : float en [0, 1]  (0 = cierre en mínimo, 1 = cierre en máximo)
    spread_pct     : (H-L) / C
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    spread = high - low
    cl = np.clip((close - low) / (spread + _EPS), 0.0, 1.0)
    sp = spread / (close + _EPS)
    return cl, sp


# ─────────────────────────────────────────────────────────────────────────────
# §3  ABSORPTION INDEX (A-Index)
# ─────────────────────────────────────────────────────────────────────────────


def compute_absorption_index(
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    window: int = _DEFAULT_ABSORPTION_WINDOW,
    threshold: float = _DEFAULT_ABSORPTION_THRESHOLD,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Índice de absorción (Vol / Spread) y su z-score rodante.

    Returns
    -------
    a_index     : absorción por barra
    a_zscore    : z-score rodante del a_index
    is_anomalous: bool array — True cuando a_index > mu + threshold * sigma
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)

    spread = high - low
    a_index = volume / (np.clip(spread, 0.0, None) + _EPS)
    n = len(a_index)
    a_zscore = np.full(n, np.nan, dtype=np.float64)
    is_anomalous = np.zeros(n, dtype=bool)

    for i in range(window - 1, n):
        w = a_index[i - window + 1 : i + 1]
        mu = w.mean()
        sigma = w.std(ddof=1)
        a_zscore[i] = (a_index[i] - mu) / (sigma + _EPS)
        is_anomalous[i] = a_index[i] > (mu + threshold * sigma)

    return a_index, a_zscore, is_anomalous


# ─────────────────────────────────────────────────────────────────────────────
# §4  BUYING CLIMAX DETECTION
# ─────────────────────────────────────────────────────────────────────────────


def detect_buying_climax(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    climax_percentile: float = _DEFAULT_CLIMAX_VOL_PERCENTILE,
    window: int = _DEFAULT_CLIMAX_VOL_WINDOW,
) -> np.ndarray:
    """Identifica velas con firma de clímax de compra institucional.

    Criterio:
    1. Volumen > percentil `climax_percentile` en ventana de `window` barras.
    2. Máximo superior al máximo de la barra anterior.
    3. Cierre <= punto medio (H+L)/2.

    Returns
    -------
    is_climax : bool ndarray shape (n,).
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    n = len(close)
    is_climax = np.zeros(n, dtype=bool)

    for i in range(window, n):
        pct = float(np.percentile(volume[i - window : i], climax_percentile))
        if volume[i] > pct and high[i] > high[i - 1] and close[i] <= (high[i] + low[i]) / 2.0:
            is_climax[i] = True

    return is_climax


# ─────────────────────────────────────────────────────────────────────────────
# §5  MFI KINÉTICO (Money Flow Index simplificado)
# ─────────────────────────────────────────────────────────────────────────────


def compute_mfi_kinetic(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    period: int = 3,
) -> np.ndarray:
    """MFI Kinético — versión comprimida de Money Flow Index.

    Returns
    -------
    mfi : ndarray shape (n,) en rango [0, 100], NaN en prefijo.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)

    tp = (high + low + close) / 3.0
    rmf = tp * volume
    tp_prev = np.empty_like(tp)
    tp_prev[0] = np.nan
    tp_prev[1:] = tp[:-1]

    pmf = np.where(tp > tp_prev, rmf, 0.0)
    nmf = np.where(tp < tp_prev, rmf, 0.0)

    n = len(close)
    mfi = np.full(n, np.nan, dtype=np.float64)
    for i in range(period - 1, n):
        sp = pmf[i - period + 1 : i + 1].sum()
        sn = nmf[i - period + 1 : i + 1].sum()
        mfr = sp / (sn + _EPS)
        mfi[i] = np.clip(100.0 - (100.0 / (1.0 + mfr)), 0.0, 100.0)
    return mfi


# ─────────────────────────────────────────────────────────────────────────────
# §6  WEIS WAVE VOLUME
# ─────────────────────────────────────────────────────────────────────────────


def compute_weis_wave(
    close: np.ndarray,
    volume: np.ndarray,
    threshold: float = _DEFAULT_WEIS_WAVE_THRESHOLD,
) -> tuple[np.ndarray, np.ndarray]:
    """Acumula volumen en ondas unidireccionales (metodología Weis Wave).

    Una onda cambia de dirección cuando el precio se desplaza más de `threshold`
    (fracción del precio) en dirección contraria.

    Returns
    -------
    wave_volume    : ndarray — volumen acumulado en la onda actual.
    wave_direction : ndarray int8 — +1 alcista, -1 bajista.
    """
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    n = len(close)
    wave_vol = np.zeros(n, dtype=np.float64)
    wave_dir = np.ones(n, dtype=np.int8)

    if n == 0:
        return wave_vol, wave_dir

    cv = volume[0]
    cd = 1
    wsp = close[0]
    wave_vol[0] = cv
    wave_dir[0] = cd

    for i in range(1, n):
        pch = (close[i] - wsp) / (wsp + _EPS)
        if (cd == 1 and pch < -threshold) or (cd == -1 and pch > threshold):
            cd = -cd
            wsp = close[i]
            cv = volume[i]
        else:
            cv += volume[i]
        wave_vol[i] = cv
        wave_dir[i] = cd

    return wave_vol, wave_dir


# ─────────────────────────────────────────────────────────────────────────────
# §7  CLASSIFY VSA BARS (vectorizado)
# ─────────────────────────────────────────────────────────────────────────────

# Códigos de etiqueta enteros para eficiencia
VSA_NORMAL = 0
VSA_STOPPING_VOLUME = 1
VSA_CLIMAX_BUY = 2
VSA_CLIMAX_SELL = 3
VSA_NO_SUPPLY = 4
VSA_NO_DEMAND = 5
VSA_EFFORT_VS_RESULT = 6


def classify_vsa_bars(
    close: np.ndarray,
    open_: np.ndarray,
    vz: np.ndarray,
    close_location: np.ndarray,
    spread: np.ndarray,
    spread_mean: np.ndarray,
    vz_climax: float = _VZ_CLIMAX,
    vz_low: float = _VZ_LOW,
    vz_effort: float = _VZ_EFFORT,
    close_ratio_high: float = _CLOSE_RATIO_HIGH,
    close_ratio_low: float = _CLOSE_RATIO_LOW,
    spread_narrow_ratio: float = _SPREAD_NARROW_RATIO,
) -> np.ndarray:
    """Clasifica cada barra según las etiquetas canónicas de Tom Williams.

    Returns
    -------
    labels : ndarray int8 con códigos VSA_* (0–6).
    """
    close = np.asarray(close, dtype=np.float64)
    open_ = np.asarray(open_, dtype=np.float64)
    vz = np.asarray(vz, dtype=np.float64)
    cl = np.asarray(close_location, dtype=np.float64)
    spread = np.asarray(spread, dtype=np.float64)
    spread_mean = np.asarray(spread_mean, dtype=np.float64)

    is_bull = close > open_
    cierre_alto = cl > close_ratio_high
    cierre_bajo = (1.0 - cl) > close_ratio_low
    narrow = spread < spread_mean * spread_narrow_ratio

    n = len(close)
    labels = np.zeros(n, dtype=np.int8)  # NORMAL

    labels[(vz > vz_effort) & narrow] = VSA_EFFORT_VS_RESULT
    labels[(vz < vz_low) & is_bull] = VSA_NO_DEMAND
    labels[(vz < vz_low) & ~is_bull] = VSA_NO_SUPPLY
    labels[(vz > vz_climax) & ~is_bull & cierre_bajo] = VSA_CLIMAX_SELL
    labels[(vz > vz_climax) & is_bull & cierre_bajo] = VSA_CLIMAX_BUY
    labels[(vz > vz_climax) & ~is_bull & cierre_alto] = VSA_STOPPING_VOLUME

    return labels


# ─────────────────────────────────────────────────────────────────────────────
# §8  CVD APPROXIMATION
# ─────────────────────────────────────────────────────────────────────────────


def compute_cvd_approx(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> np.ndarray:
    """Cálculo aproximado del Cumulative Volume Delta (CVD).

    CVD_i = Σ V_i × (C_i - O_i) / (H_i - L_i)

    Returns
    -------
    cvd : ndarray acumulado, shape (n,).
    """
    open_ = np.asarray(open_, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)

    spread = high - low
    diff = (close - open_) / (spread + _EPS)
    return np.cumsum(volume * diff)
