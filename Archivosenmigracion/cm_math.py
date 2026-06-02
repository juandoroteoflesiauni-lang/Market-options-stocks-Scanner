"""Institutional CM kernels (GEX, DAGEX, Kelly, Markov) — single source of truth.

Pure numpy/scipy math only (Layer 2). Import from Layer 3 via the thin adapter
``backend.layer_3_specialists.ia_probabilistico.engines.cm_math`` or directly here.
"""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

_FloatArr = npt.NDArray[np.float64]


class CMMath:
    """Stateless mathematical kernels for dealer GEX / DAGEX and tail-aware Kelly."""

    @staticmethod
    def gex_institutional(
        gamma: _FloatArr,
        oi: _FloatArr,
        spot: float,
        multiplier: float = 100.0,
        is_call: bool | _FloatArr = True,
    ) -> _FloatArr:
        """Institutional GEX (S²): Gamma * OI * mult * S² * sign(call/put)."""
        sign = np.where(is_call, 1.0, -1.0)
        return np.asarray(gamma * oi * multiplier * (spot**2) * sign, dtype=np.float64)

    @staticmethod
    def dagex(
        gamma: _FloatArr,
        delta: _FloatArr,
        oi: _FloatArr,
        spot: float,
        multiplier: float = 100.0,
        is_call: bool | _FloatArr = True,
    ) -> _FloatArr:
        """Delta-adjusted gamma exposure."""
        sign = np.where(is_call, 1.0, -1.0)
        return np.asarray(gamma * np.abs(delta) * oi * multiplier * spot * sign, dtype=np.float64)

    @staticmethod
    def proximitiy_weight(tte_years: _FloatArr) -> _FloatArr:
        """Expiry proximity weight w = exp(-TTE * 52)."""
        return np.exp(-tte_years * 52.0)

    @staticmethod
    def vrp_log_ratio(iv: _FloatArr | float, hv: _FloatArr | float) -> _FloatArr | float:
        """Log VRP ln(IV/HV) with numerical floors."""
        iv_safe = np.maximum(iv, 1e-6)
        hv_safe = np.maximum(hv, 1e-6)
        out = np.log(iv_safe / hv_safe)
        if isinstance(out, np.ndarray):
            return np.asarray(out, dtype=np.float64)
        return float(out)

    @staticmethod
    def kelly_fat_tail(
        mu: float,
        sigma: float,
        kurtosis: float,
        fraction: float = 0.5,
    ) -> float:
        """Kelly fraction damped for excess kurtosis."""
        if sigma <= 1e-9:
            return 0.0
        raw_kelly = mu / (sigma**2)
        tail_adj = 1.0 / (1.0 + max(0.0, kurtosis) / 6.0)
        return float(np.clip(raw_kelly * tail_adj * fraction, 0.0, 1.0))

    @staticmethod
    def markov_projection(transition_matrix: _FloatArr, current_state_idx: int, n_steps: int) -> _FloatArr:
        """Chapman–Kolmogorov: distribution after n_steps."""
        t_n = np.linalg.matrix_power(transition_matrix, n_steps)
        v0 = np.zeros(transition_matrix.shape[0])
        v0[current_state_idx] = 1.0
        return np.asarray(v0 @ t_n, dtype=np.float64)


def compute_vanna_vol_drift(vanna_exposure: float, iv_change: float) -> float:
    """Vol-drift contribution from vanna exposure."""
    try:
        return float(vanna_exposure * iv_change)
    except Exception as exc:
        logger.error("cm_math.vanna_drift_failed err=%s", exc)
        return 0.0


def compute_charm_price_bias(charm_exposure: float, time_decay: float) -> float:
    """Price-bias contribution from charm exposure."""
    try:
        return float(charm_exposure * time_decay)
    except Exception as exc:
        logger.error("cm_math.charm_bias_failed err=%s", exc)
        return 0.0


def calculate_probabilistic_gex_gating(
    current_gex: float,
    vanna_flow: float,
    regime_confidence: float,
    threshold: float = 0.5,
) -> bool:
    """Heuristic stability gate: positive GEX/vanna support + regime confidence."""
    try:
        gex_aligned = current_gex > 0
        vanna_aligned = vanna_flow > 0
        stability_score = (
            0.4 * float(gex_aligned) + 0.4 * float(vanna_aligned) + 0.2 * regime_confidence
        )
        return stability_score >= threshold
    except Exception as exc:
        logger.error("cm_math.gex_gating_failed err=%s", exc)
        return False
