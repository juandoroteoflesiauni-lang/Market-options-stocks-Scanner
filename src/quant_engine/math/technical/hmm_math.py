"""Núcleo matemático de HMM (Hidden Markov Model) — Sector Técnico.

Funciones puras numpy para inferencia online de un HMM Gaussiano multivariante
en espacio logarítmico, entropía normalizada y clasificación de régimen.

Restricciones:
- Exclusivamente numpy.  Sin pandas, pydantic, logging ni capas de dominio.
- Toda división regularizada con _EPS = 1e-12.
- log-sum-exp numéricamente estable para evitar underflow.
"""

from __future__ import annotations

from math import exp, isfinite, log

import numpy as np

_EPS: float = 1e-12

# Umbrales de clasificación de régimen (del plan de migración §3.5)
_ENTROPY_CRITICAL: float = 0.70  # H > 0.70 → CRITICAL
_ENTROPY_SHIFTING: float = 0.40  # H > 0.40 → SHIFTING
# H <= 0.40 → STABLE

REGIME_STABLE = 0
REGIME_SHIFTING = 1
REGIME_CRITICAL = 2


# ─────────────────────────────────────────────────────────────────────────────
# §1  LOG-SUM-EXP (estabilidad numérica)
# ─────────────────────────────────────────────────────────────────────────────


def log_sum_exp(values: np.ndarray) -> float:
    """Log-sum-exp numéricamente estable.

    LSE(v) = max(v) + log Σ exp(v_i - max(v))

    Returns
    -------
    lse : float.  -inf si todos los valores son -inf.
    """
    values = np.asarray(values, dtype=np.float64)
    max_val = float(np.max(values))
    if not isfinite(max_val):
        return float(-np.inf)
    return max_val + log(sum(exp(float(v - max_val)) for v in values))


# ─────────────────────────────────────────────────────────────────────────────
# §2  ENTROPÍA NORMALIZADA
# ─────────────────────────────────────────────────────────────────────────────


def normalised_entropy(probabilities: np.ndarray) -> float:
    """Entropía normalizada de una distribución discreta.

    H_norm = -Σ p_i log(p_i) / log(K)

    Returns
    -------
    entropy : float en [0, 1].  0.0 si K == 1.
    """
    p = np.clip(np.asarray(probabilities, dtype=np.float64), 1e-12, 1.0)
    entropy = -float(np.sum(p * np.log(p)))
    k = len(p)
    return entropy / log(k) if k > 1 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# §3  LOG-EMISSION PROBABILITY (Gaussiana multivariante)
# ─────────────────────────────────────────────────────────────────────────────


def log_gaussian_emission(
    observation: np.ndarray,
    mean: np.ndarray,
    inverse_covariance: np.ndarray,
    log_norm_const: float,
) -> float:
    """Log-probabilidad de emisión Gaussiana multivariante.

    log P(x | μ, Σ) = -0.5 · (x-μ)ᵀ Σ⁻¹ (x-μ) - log_norm_const

    Parameters
    ----------
    observation         : ndarray shape (d,).
    mean                : ndarray shape (d,).
    inverse_covariance  : ndarray shape (d, d).
    log_norm_const      : 0.5·d·log(2π) + 0.5·log|Σ|.

    Returns
    -------
    log_prob : float.
    """
    diff = np.asarray(observation, dtype=np.float64) - np.asarray(mean, dtype=np.float64)
    mahalanobis_sq = float(diff.T @ inverse_covariance @ diff)
    return -0.5 * mahalanobis_sq - log_norm_const


# ─────────────────────────────────────────────────────────────────────────────
# §4  PRECOMPUTACIÓN DE CONSTANTES DE EMISIÓN
# ─────────────────────────────────────────────────────────────────────────────


def precompute_emission_cache(
    means: list[np.ndarray],
    covariances: list[np.ndarray],
) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """Pre-computa (mean, inv_cov, log_norm_const) por estado del HMM.

    Returns
    -------
    cache : lista de tuplas (mean, inverse_covariance, log_norm_const) por estado.

    Raises
    ------
    ValueError si alguna matriz de covarianza no es positiva definida.
    """
    cache = []
    d = len(means[0])
    for state, (mu, cov) in enumerate(zip(means, covariances)):
        cov_arr = np.asarray(cov, dtype=np.float64)
        cov_arr = cov_arr + np.eye(d) * 1e-10  # regularización Tikhonov
        sign, log_det = np.linalg.slogdet(cov_arr)
        if sign <= 0 or not isfinite(float(log_det)):
            raise ValueError(f"Covariance matrix for state {state} is not positive definite")
        inv_cov = np.linalg.inv(cov_arr)
        log_norm = 0.5 * d * log(2 * np.pi) + 0.5 * float(log_det)
        cache.append((np.asarray(mu, dtype=np.float64), inv_cov, log_norm))
    return cache


# ─────────────────────────────────────────────────────────────────────────────
# §5  FORWARD FILTER STEP (log-space)
# ─────────────────────────────────────────────────────────────────────────────


def forward_step(
    log_alpha: np.ndarray,
    log_transition: np.ndarray,
    log_emissions: np.ndarray,
    initialised: bool,
) -> np.ndarray:
    """Paso del filtro forward en espacio logarítmico.

    log α_t(j) = log P(x_t | s_j) + log-sum-exp_i [log α_{t-1}(i) + log A_{ij}]

    Parameters
    ----------
    log_alpha      : ndarray shape (K,) — log-alpha del paso anterior.
    log_transition : ndarray shape (K, K) — log de la matriz de transición.
    log_emissions  : ndarray shape (K,) — log P(x_t | s_j) por estado.
    initialised    : bool — si False, omite la convolución de transición.

    Returns
    -------
    new_log_alpha : ndarray shape (K,) normalizado.
    """
    K = len(log_alpha)
    log_alpha = np.asarray(log_alpha, dtype=np.float64)
    log_transition = np.asarray(log_transition, dtype=np.float64)
    log_emissions = np.asarray(log_emissions, dtype=np.float64)

    if not initialised:
        new_log_alpha = log_alpha + log_emissions
    else:
        new_log_alpha = np.empty(K, dtype=np.float64)
        for j in range(K):
            new_log_alpha[j] = log_emissions[j] + log_sum_exp(
                log_alpha + log_transition[:, j]
            )

    # Normalización en log-space
    partition = log_sum_exp(new_log_alpha)
    return new_log_alpha - partition


# ─────────────────────────────────────────────────────────────────────────────
# §6  FULL SEQUENCE INFERENCE
# ─────────────────────────────────────────────────────────────────────────────


def infer_hmm_sequence(
    observations: np.ndarray,
    initial_log_probs: np.ndarray,
    log_transition: np.ndarray,
    emission_cache: list[tuple[np.ndarray, np.ndarray, float]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inferencia forward completa sobre una secuencia de observaciones.

    Parameters
    ----------
    observations       : ndarray shape (T, d).
    initial_log_probs  : ndarray shape (K,) — log de probabilidades iniciales.
    log_transition     : ndarray shape (K, K).
    emission_cache     : lista de (mean, inv_cov, log_norm) por estado.

    Returns
    -------
    state_sequence     : ndarray int shape (T,) — estado más probable en cada paso.
    state_probs        : ndarray float shape (T, K) — P(s_j | x_{1:t}).
    transition_risks   : ndarray float shape (T,) — entropía normalizada por paso.
    """
    observations = np.asarray(observations, dtype=np.float64)
    T = len(observations)
    K = len(emission_cache)

    log_alpha = np.asarray(initial_log_probs, dtype=np.float64)
    state_probs = np.empty((T, K), dtype=np.float64)
    state_sequence = np.empty(T, dtype=np.int64)
    transition_risks = np.empty(T, dtype=np.float64)

    for t in range(T):
        obs = observations[t]
        log_em = np.array(
            [log_gaussian_emission(obs, mu, inv_cov, lnc) for mu, inv_cov, lnc in emission_cache],
            dtype=np.float64,
        )
        log_alpha = forward_step(log_alpha, log_transition, log_em, initialised=(t > 0))
        probs = np.exp(log_alpha)
        state_probs[t] = probs
        state_sequence[t] = int(np.argmax(probs))
        transition_risks[t] = normalised_entropy(probs)

    return state_sequence, state_probs, transition_risks


# ─────────────────────────────────────────────────────────────────────────────
# §7  CLASIFICACIÓN DE RÉGIMEN
# ─────────────────────────────────────────────────────────────────────────────


def classify_regime(entropy: float) -> int:
    """Clasifica el estado de régimen del mercado por entropía normalizada.

    Returns
    -------
    regime_code : int (REGIME_STABLE=0, REGIME_SHIFTING=1, REGIME_CRITICAL=2).
    """
    if entropy > _ENTROPY_CRITICAL:
        return REGIME_CRITICAL
    if entropy > _ENTROPY_SHIFTING:
        return REGIME_SHIFTING
    return REGIME_STABLE


# ─────────────────────────────────────────────────────────────────────────────
# §8  MATRIX UTILITIES
# ─────────────────────────────────────────────────────────────────────────────


def to_log_matrix(matrix: np.ndarray) -> np.ndarray:
    """Convierte una matriz de probabilidad a espacio logarítmico."""
    arr = np.asarray(matrix, dtype=np.float64)
    return np.where(arr > 0, np.log(arr), -np.inf)


def to_log_vector(vector: np.ndarray) -> np.ndarray:
    """Convierte un vector de probabilidad a espacio logarítmico."""
    arr = np.asarray(vector, dtype=np.float64)
    return np.where(arr > 0, np.log(arr), -np.inf)


def build_ohlcv_features(
    close: np.ndarray,
    volume: np.ndarray,
    vol_window: int = 20,
    ret_window: int = 20,
) -> np.ndarray:
    """Construye features [log_return, realized_volatility, volume_zscore] desde OHLCV.

    Returns
    -------
    features : ndarray shape (n, 3) — rows con NaN en prefijo descartables.
    """
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    n = len(close)

    log_ret = np.full(n, np.nan)
    log_ret[1:] = np.log(close[1:] / (close[:-1] + _EPS))

    rvol = np.full(n, np.nan)
    for i in range(vol_window - 1, n):
        rvol[i] = log_ret[max(0, i - vol_window + 1) : i + 1].std()

    vol_zscore = np.full(n, np.nan)
    for i in range(vol_window - 1, n):
        w = volume[i - vol_window + 1 : i + 1]
        mu, sigma = w.mean(), w.std()
        vol_zscore[i] = (volume[i] - mu) / (sigma + _EPS)

    return np.column_stack([log_ret, rvol, vol_zscore])
