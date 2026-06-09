"""Núcleo matemático de TPO Profile y Skewness — Sector Técnico.

Funciones puras numpy/scipy para cálculo de estadísticas de distribución TPO,
asimetría de Fisher-Pearson y detección bimodal.

Restricciones:
- Exclusivamente numpy y scipy.  Sin pandas, pydantic, logging ni capas de dominio.
- Toda división regularizada con _EPS = 1e-12.
"""

from __future__ import annotations

from math import sqrt

import numpy as np

_EPS: float = 1e-12

# Clasificación morfológica (codificación entera)
SHAPE_NORMAL = 0          # NormalDistribution: |skewness| <= symmetry_threshold
SHAPE_PSHAPE = 1          # PShape: saturación en alto, cola abajo (skewness < -threshold)
SHAPE_BSHAPE = 2          # BShape: saturación en bajo, cola arriba (skewness > +threshold)
SHAPE_DOUBLE_DIST = 3     # DDoubleDistribution: bimodal
SHAPE_TRANSITIONAL = 4    # Transitional: skewness entre umbrales

# Umbrales por defecto (del plan de migración)
_SKEW_THRESHOLD: float = 0.50
_SYMMETRY_THRESHOLD: float = 0.15
_BIMODAL_GAP_TICKS: int = 6


# ─────────────────────────────────────────────────────────────────────────────
# §1  ESTADÍSTICAS DE DISTRIBUCIÓN TPO
# ─────────────────────────────────────────────────────────────────────────────


def compute_tpo_stats(
    prices: np.ndarray,
    tpo_counts: np.ndarray,
) -> tuple[float, float, float, float]:
    """Calcula estadísticas de primer, segundo y tercer momento de un perfil TPO.

    Parameters
    ----------
    prices     : ndarray de precios de cada nivel (shape n).
    tpo_counts : ndarray de conteos TPO por nivel (shape n).

    Returns
    -------
    mean, sigma, skewness, poc_price : float
        poc_price es el precio del nivel con mayor tpo_count.
    """
    prices = np.asarray(prices, dtype=np.float64)
    tpo_counts = np.asarray(tpo_counts, dtype=np.float64)

    total = tpo_counts.sum()
    if total <= 0:
        return 0.0, 0.0, 0.0, 0.0

    poc_idx = int(np.argmax(tpo_counts))
    poc_price = float(prices[poc_idx])

    mean = float((tpo_counts * prices).sum() / total)
    variance = float((tpo_counts * (prices - mean) ** 2).sum() / total)
    sigma = sqrt(max(variance, 0.0))

    if sigma < _EPS:
        skewness = 0.0
    else:
        third_moment = float((tpo_counts * (prices - mean) ** 3).sum())
        skewness = third_moment / (total * (sigma**3) + _EPS)

    return mean, sigma, skewness, poc_price


# ─────────────────────────────────────────────────────────────────────────────
# §2  CLASIFICACIÓN MORFOLÓGICA
# ─────────────────────────────────────────────────────────────────────────────


def classify_profile_shape(
    skewness: float,
    prices_sorted: np.ndarray,
    tpo_counts_sorted: np.ndarray,
    skew_threshold: float = _SKEW_THRESHOLD,
    symmetry_threshold: float = _SYMMETRY_THRESHOLD,
    bimodal_gap_ticks: int = _BIMODAL_GAP_TICKS,
) -> int:
    """Clasifica la forma morfológica del perfil TPO.

    Returns
    -------
    shape_code : int (SHAPE_NORMAL, SHAPE_PSHAPE, SHAPE_BSHAPE, SHAPE_DOUBLE_DIST, SHAPE_TRANSITIONAL).
    """
    if detect_bimodal(tpo_counts_sorted, bimodal_gap_ticks):
        return SHAPE_DOUBLE_DIST
    if abs(skewness) <= symmetry_threshold:
        return SHAPE_NORMAL
    if skewness > skew_threshold:
        return SHAPE_BSHAPE
    if skewness < -skew_threshold:
        return SHAPE_PSHAPE
    return SHAPE_TRANSITIONAL


def detect_bimodal(
    tpo_counts_sorted: np.ndarray,
    gap_ticks: int = _BIMODAL_GAP_TICKS,
) -> bool:
    """Detecta distribución bimodal (DDoubleDistribution).

    Una distribución bimodal tiene ≥ `gap_ticks` niveles consecutivos con
    tpo_count ≤ 1 entre dos zonas densas.

    Parameters
    ----------
    tpo_counts_sorted : tpo_counts en orden ascendente de precio.
    gap_ticks         : mínimo de niveles consecutivos bajos.
    """
    tpo = np.asarray(tpo_counts_sorted, dtype=np.float64)
    if len(tpo) < 2 * gap_ticks:
        return False
    consecutive_low = 0
    for count in tpo:
        if count <= 1:
            consecutive_low += 1
            if consecutive_low >= gap_ticks:
                return True
        else:
            consecutive_low = 0
    return False


# ─────────────────────────────────────────────────────────────────────────────
# §3  TPO BINNING — Distribución de barras OHLC en niveles de precio
# ─────────────────────────────────────────────────────────────────────────────


def build_tpo_histogram(
    high: np.ndarray,
    low: np.ndarray,
    tick_size: float,
    max_bins_per_bar: int = 500,
    max_total_levels: int = 2500,
) -> tuple[np.ndarray, np.ndarray]:
    """Construye el histograma TPO a partir de barras OHLCV.

    Cada barra contribuye a todos los niveles de precio dentro de su rango
    [low, high], cuantizados a `tick_size`.

    Parameters
    ----------
    high, low       : arrays de precio.
    tick_size       : tamaño del tick de precio.
    max_bins_per_bar: límite de bins por barra (sub-muestreo si se supera).
    max_total_levels: límite total de niveles (el histograma se trunca).

    Returns
    -------
    prices     : ndarray de centros de nivel ordenados ascendentemente.
    tpo_counts : ndarray de conteos de TPO por nivel.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")

    levels: dict[float, int] = {}
    n_bars = len(high)

    for b in range(n_bars):
        lo = low[b]
        hi = high[b]
        if not (np.isfinite(lo) and np.isfinite(hi) and hi >= lo):
            continue

        lo_bin = round(lo / tick_size) * tick_size
        hi_bin = round(hi / tick_size) * tick_size
        bin_count = max(1, round((hi_bin - lo_bin) / tick_size) + 1)
        step = max(1, bin_count // max_bins_per_bar)

        offset = 0
        while offset < bin_count:
            price = round((lo_bin + offset * tick_size) / tick_size) * tick_size
            price = round(price, 10)
            if len(levels) >= max_total_levels and price not in levels:
                break
            levels[price] = levels.get(price, 0) + 1
            offset += step

    if not levels:
        return np.array([], dtype=np.float64), np.array([], dtype=np.int64)

    sorted_prices = sorted(levels)
    prices = np.array(sorted_prices, dtype=np.float64)
    tpo_counts = np.array([levels[p] for p in sorted_prices], dtype=np.int64)
    return prices, tpo_counts


# ─────────────────────────────────────────────────────────────────────────────
# §4  TICK SIZE INFERENCE
# ─────────────────────────────────────────────────────────────────────────────


def infer_tick_size(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    max_total_levels: int = 2500,
) -> float:
    """Infiere el tick size óptimo para un perfil TPO dado el rango de precio.

    Returns
    -------
    tick_size : float > 0.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)

    price_min = float(np.nanmin(low))
    price_max = float(np.nanmax(high))
    span = max(price_max - price_min, price_max * 0.001, 0.01)
    raw_tick = span / max(max_total_levels * 0.70, 1)
    last_close = float(close[-1]) if len(close) > 0 else 1.0
    price_floor = max(abs(last_close) * 0.0001, 0.0001)
    tick = max(raw_tick, price_floor)

    if last_close >= 10:
        tick = max(round(tick, 2), 0.01)
    elif last_close >= 1:
        tick = max(round(tick, 4), 0.0001)
    else:
        tick = max(round(tick, 6), 0.000001)

    return float(tick)


# ─────────────────────────────────────────────────────────────────────────────
# §5  FULL PIPELINE HELPER
# ─────────────────────────────────────────────────────────────────────────────


def analyze_tpo(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    tick_size: float | None = None,
    max_bins_per_bar: int = 500,
    max_total_levels: int = 2500,
    skew_threshold: float = _SKEW_THRESHOLD,
    symmetry_threshold: float = _SYMMETRY_THRESHOLD,
    bimodal_gap_ticks: int = _BIMODAL_GAP_TICKS,
) -> tuple[float, float, float, float, int]:
    """Pipeline TPO completo: binning → estadísticas → clasificación.

    Returns
    -------
    mean, sigma, skewness, poc_price, shape_code : primitivos.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)

    ts = tick_size if tick_size and tick_size > 0 else infer_tick_size(high, low, close, max_total_levels)
    prices, tpo_counts = build_tpo_histogram(high, low, ts, max_bins_per_bar, max_total_levels)

    if len(prices) < 3:
        return 0.0, 0.0, 0.0, 0.0, SHAPE_TRANSITIONAL

    mean, sigma, skewness, poc_price = compute_tpo_stats(prices, tpo_counts)
    shape = classify_profile_shape(skewness, prices, tpo_counts.astype(np.float64), skew_threshold, symmetry_threshold, bimodal_gap_ticks)
    return mean, sigma, skewness, poc_price, shape
