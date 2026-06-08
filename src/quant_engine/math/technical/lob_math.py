"""Núcleo matemático de LOB (Limit Order Book) Dynamics — Sector Técnico.

Funciones puras numpy para cálculo de desequilibrio de book, ratios
cancel-to-trade y métricas de microestructura de orden.

Restricciones:
- Exclusivamente numpy.  Sin pandas, pydantic, logging ni capas de dominio.
- Toda división regularizada con _EPS = 1e-12.
"""

from __future__ import annotations

from math import inf, isfinite

import numpy as np

_EPS: float = 1e-12

# Umbrales por defecto
_DEFAULT_RHO_SPOOFING_THRESHOLD: float = 0.3
_DEFAULT_CTR_SPOOFING_MULTIPLIER: float = 4.0
_DEFAULT_DEPTH_LEVELS: int = 5


# ─────────────────────────────────────────────────────────────────────────────
# §1  DEPTH IMBALANCE (ρ)
# ─────────────────────────────────────────────────────────────────────────────


def compute_depth_imbalance(
    bid_quantities: np.ndarray,
    ask_quantities: np.ndarray,
) -> float:
    """Calcula el ratio de desequilibrio de profundidad del libro.

    ρ = (Q_bid - Q_ask) / (Q_bid + Q_ask)

    Parameters
    ----------
    bid_quantities, ask_quantities : arrays de cantidades en los mejores N niveles.

    Returns
    -------
    rho : float en [-1, +1].  0.0 si el libro está vacío.
    """
    bid_sum = float(np.sum(np.asarray(bid_quantities, dtype=np.float64)))
    ask_sum = float(np.sum(np.asarray(ask_quantities, dtype=np.float64)))
    total = bid_sum + ask_sum
    return 0.0 if total <= 0 else (bid_sum - ask_sum) / total


# ─────────────────────────────────────────────────────────────────────────────
# §2  CANCEL-TO-TRADE RATIO (CTR)
# ─────────────────────────────────────────────────────────────────────────────


def compute_ctr(
    cancelled_volume: float,
    traded_volume: float,
) -> float:
    """Ratio de cancelación sobre volumen negociado.

    CTR = cancelled_volume / traded_volume

    Returns
    -------
    ctr : float.  math.inf cuando traded_volume == 0.
    """
    if traded_volume <= 0:
        return inf
    return cancelled_volume / traded_volume


# ─────────────────────────────────────────────────────────────────────────────
# §3  SPOOFING CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

# Códigos de estado
SPOOFING_NORMAL = 0
SPOOFING_BID = 1
SPOOFING_ASK = 2


def classify_spoofing(
    rho: float,
    ctr_bid: float,
    ctr_ask: float,
    rho_threshold: float = _DEFAULT_RHO_SPOOFING_THRESHOLD,
    ctr_multiplier: float = _DEFAULT_CTR_SPOOFING_MULTIPLIER,
) -> int:
    """Clasifica el estado de manipulación de book (spoofing).

    Returns
    -------
    state : int (SPOOFING_NORMAL=0, SPOOFING_BID=1, SPOOFING_ASK=2).
    """
    if abs(rho) < rho_threshold:
        return SPOOFING_NORMAL

    bid_base = ctr_bid if (isfinite(ctr_bid) and ctr_bid > 0) else 1.0
    ask_base = ctr_ask if (isfinite(ctr_ask) and ctr_ask > 0) else 1.0

    if rho > 0 and (ctr_bid / ask_base) >= ctr_multiplier:
        return SPOOFING_BID
    if rho < 0 and (ctr_ask / bid_base) >= ctr_multiplier:
        return SPOOFING_ASK
    return SPOOFING_NORMAL


# ─────────────────────────────────────────────────────────────────────────────
# §4  ORDER FLOW IMBALANCE (OFI) — vectorizado
# ─────────────────────────────────────────────────────────────────────────────


def compute_ofi(
    bid_price: np.ndarray,
    bid_size: np.ndarray,
    ask_price: np.ndarray,
    ask_size: np.ndarray,
) -> np.ndarray:
    """Order Flow Imbalance (OFI) vectorizado sobre series de snapshots.

    OFI_t = ΔQ_bid - ΔQ_ask  donde el delta se calcula al nivel de mejor precio.

    Parameters
    ----------
    bid_price, bid_size : arrays de mejor bid y su cantidad, shape (n,).
    ask_price, ask_size : arrays de mejor ask y su cantidad, shape (n,).

    Returns
    -------
    ofi : ndarray shape (n,), primer elemento es 0.0.
    """
    bp = np.asarray(bid_price, dtype=np.float64)
    bs = np.asarray(bid_size, dtype=np.float64)
    ap = np.asarray(ask_price, dtype=np.float64)
    as_ = np.asarray(ask_size, dtype=np.float64)

    n = len(bp)
    ofi = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        d_bid = bs[i] if bp[i] >= bp[i - 1] else -bs[i - 1]
        d_ask = as_[i] if ap[i] <= ap[i - 1] else -as_[i - 1]
        ofi[i] = d_bid - d_ask
    return ofi


# ─────────────────────────────────────────────────────────────────────────────
# §5  QUEUE IMBALANCE
# ─────────────────────────────────────────────────────────────────────────────


def compute_queue_imbalance(
    bid_sizes: np.ndarray,
    ask_sizes: np.ndarray,
    depth: int = _DEFAULT_DEPTH_LEVELS,
) -> np.ndarray:
    """Queue Imbalance por timestep sobre los primeros `depth` niveles.

    QI_t = (Σ Q_bid_k - Σ Q_ask_k) / (Σ Q_bid_k + Σ Q_ask_k)

    Parameters
    ----------
    bid_sizes : ndarray shape (n, depth) o (n, m).
    ask_sizes : ndarray shape (n, depth) o (n, m).
    depth     : número de niveles a considerar.

    Returns
    -------
    qi : ndarray shape (n,) en [-1, 1].
    """
    bids = np.asarray(bid_sizes, dtype=np.float64)
    asks = np.asarray(ask_sizes, dtype=np.float64)
    if bids.ndim == 1:
        bids = bids[:, np.newaxis]
    if asks.ndim == 1:
        asks = asks[:, np.newaxis]

    bid_sum = bids[:, :depth].sum(axis=1)
    ask_sum = asks[:, :depth].sum(axis=1)
    total = bid_sum + ask_sum
    return np.where(total > 0, (bid_sum - ask_sum) / total, 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# §6  ROLLING CTR (ventana temporal en ms — implementación discreta por índice)
# ─────────────────────────────────────────────────────────────────────────────


def rolling_ctr(
    timestamps: np.ndarray,
    cancelled: np.ndarray,
    traded: np.ndarray,
    window_ms: int,
) -> np.ndarray:
    """CTR calculado sobre una ventana deslizante temporal.

    Parameters
    ----------
    timestamps : int64 ndarray en milisegundos.
    cancelled  : float64 ndarray — volumen cancelado por evento.
    traded     : float64 ndarray — volumen negociado por evento.
    window_ms  : ancho de la ventana en milisegundos.

    Returns
    -------
    ctr : ndarray float64, shape (n,). inf donde traded == 0.
    """
    timestamps = np.asarray(timestamps, dtype=np.int64)
    cancelled = np.asarray(cancelled, dtype=np.float64)
    traded = np.asarray(traded, dtype=np.float64)

    n = len(timestamps)
    ctr = np.empty(n, dtype=np.float64)
    left = 0

    for i in range(n):
        cutoff = timestamps[i] - window_ms
        while left < i and timestamps[left] < cutoff:
            left += 1
        sc = cancelled[left : i + 1].sum()
        st = traded[left : i + 1].sum()
        ctr[i] = inf if st <= 0 else sc / st

    return ctr
