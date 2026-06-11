"""Contratos de Dominio para el Motor HMM (Hidden Markov Model) — Sector Técnico.

Define observaciones de mercado, parámetros del modelo, estimaciones online de régimen
y la salida de análisis para clasificación estadística de regímenes de mercado.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MarketObservation(BaseModel):
    """Single market observation represented as a feature vector."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: str
    features: tuple[float, ...]


class HMMParameters(BaseModel):
    """Pre-trained HMM parameters for multivariate Gaussian emissions."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    states: int
    transition_matrix: tuple[tuple[float, ...], ...]
    emission_means: tuple[tuple[float, ...], ...]
    emission_covariances: tuple[tuple[tuple[float, ...], ...], ...]
    initial_probabilities: tuple[float, ...]
    state_labels: tuple[str, ...] = ()


class HMMRegimeResult(BaseModel):
    """Online filtered regime estimate."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    timestamp: str
    current_state: int
    current_label: str
    state_probabilities: tuple[float, ...]
    transition_risk: float
    regime_signal: str


class HMMAnalysisOutput(BaseModel):
    """Compact HMM block suitable for technical API payloads."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ok: bool = True
    error: str | None = None
    current_state: int = -1
    current_label: str = "UNKNOWN"
    state_probabilities: tuple[float, ...] = ()
    transition_risk: float = 1.0
    regime_signal: str = "CRITICAL"
    history: tuple[HMMRegimeResult, ...] = ()
