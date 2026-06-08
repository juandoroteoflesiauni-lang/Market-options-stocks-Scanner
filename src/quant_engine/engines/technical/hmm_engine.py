"""Motor de Inferencia HMM (Hidden Markov Model) — Sector Técnico.

Implementa un filtro forward en log-space para un HMM Gaussiano multivariante.
Procesa observaciones de mercado OHLCV y devuelve estimaciones online de régimen.
"""

from __future__ import annotations

import logging
from math import exp, isfinite, log

import numpy as np
import pandas as pd

from ...domain.technical.hmm_models import (
    HMMAnalysisOutput,
    HMMParameters,
    HMMRegimeResult,
    MarketObservation,
)

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = ("close", "volume")
_MIN_OBSERVATIONS = 25


# ─────────────────────────────────────────────────────────────────────────────
# §1  PRIVATE EMISSION CACHE (interno al motor)
# ─────────────────────────────────────────────────────────────────────────────


class _EmissionCache:
    """Pre-computed Gaussian emission constants per state (mutable, internal)."""

    __slots__ = ("mean", "inverse_covariance", "log_norm_const")

    def __init__(
        self,
        mean: np.ndarray,
        inverse_covariance: np.ndarray,
        log_norm_const: float,
    ) -> None:
        self.mean = mean
        self.inverse_covariance = inverse_covariance
        self.log_norm_const = log_norm_const


# ─────────────────────────────────────────────────────────────────────────────
# §2  HMMInferenceEngine
# ─────────────────────────────────────────────────────────────────────────────


class HMMInferenceEngine:
    """Online log-space forward filter for a Gaussian HMM."""

    def __init__(self: HMMInferenceEngine, params: HMMParameters) -> None:
        self.params = self._validate_params(params)
        self.state_count = self.params.states
        self.dimension = len(self.params.emission_means[0])
        self.state_labels = self.params.state_labels or tuple(
            f"STATE_{idx}" for idx in range(self.state_count)
        )
        self._log_transition = self._to_log_matrix(self.params.transition_matrix)
        self._emissions = self._build_emission_cache()
        self._initial_log_alpha = self._to_log_vector(self.params.initial_probabilities)
        self._log_alpha = self._initial_log_alpha.copy()
        self._initialised = False

    def step(self: HMMInferenceEngine, observation: MarketObservation) -> HMMRegimeResult:
        """Process one observation and return the filtered state distribution."""
        if len(observation.features) != self.dimension:
            raise ValueError(
                f"Feature vector length {len(observation.features)} does not match {self.dimension}"
            )

        features = np.asarray(observation.features, dtype=np.float64)
        log_emissions = np.array(
            [self._log_emission_probability(features, state) for state in range(self.state_count)],
            dtype=np.float64,
        )

        if not self._initialised:
            new_log_alpha = self._log_alpha + log_emissions
            self._initialised = True
        else:
            new_log_alpha = np.empty(self.state_count, dtype=np.float64)
            for state in range(self.state_count):
                new_log_alpha[state] = log_emissions[state] + _log_sum_exp(
                    self._log_alpha + self._log_transition[:, state]
                )

        partition = _log_sum_exp(new_log_alpha)
        new_log_alpha = new_log_alpha - partition
        self._log_alpha = new_log_alpha
        probabilities = np.exp(new_log_alpha)
        current_state = int(np.argmax(probabilities))
        transition_risk = _normalised_entropy(probabilities)

        return HMMRegimeResult(
            timestamp=observation.timestamp,
            current_state=current_state,
            current_label=self.state_labels[current_state],
            state_probabilities=tuple(round(float(value), 6) for value in probabilities),
            transition_risk=round(transition_risk, 6),
            regime_signal=_regime_signal(transition_risk),
        )

    def reset(self: HMMInferenceEngine) -> None:
        """Reset the filter to initial probabilities."""
        self._log_alpha = self._initial_log_alpha.copy()
        self._initialised = False

    def analyze_ohlcv(
        self: HMMInferenceEngine,
        df: pd.DataFrame,
        max_history: int = 120,
    ) -> HMMAnalysisOutput:
        """Build technical features from OHLCV and return the latest HMM regime."""
        try:
            observations = build_ohlcv_observations(df)
            if len(observations) < 2:
                return HMMAnalysisOutput(
                    ok=False, error=f"Insufficient observations ({len(observations)})"
                )

            self.reset()
            history = tuple(self.step(observation) for observation in observations)
            latest = history[-1]
            return HMMAnalysisOutput(
                ok=True,
                current_state=latest.current_state,
                current_label=latest.current_label,
                state_probabilities=latest.state_probabilities,
                transition_risk=latest.transition_risk,
                regime_signal=latest.regime_signal,
                history=history[-max_history:],
            )
        except Exception as exc:
            logger.warning("HMM analysis failed: %s", exc)
            return HMMAnalysisOutput(ok=False, error=str(exc))

    def _log_emission_probability(
        self: HMMInferenceEngine,
        features: np.ndarray,
        state: int,
    ) -> float:
        cache = self._emissions[state]
        diff = features - cache.mean
        mahalanobis_sq = float(diff.T @ cache.inverse_covariance @ diff)
        return -0.5 * mahalanobis_sq - cache.log_norm_const

    def _build_emission_cache(self: HMMInferenceEngine) -> tuple[_EmissionCache, ...]:
        caches: list[_EmissionCache] = []
        for state, mean_values in enumerate(self.params.emission_means):
            covariance = np.asarray(self.params.emission_covariances[state], dtype=np.float64)
            covariance = covariance + np.eye(self.dimension, dtype=np.float64) * 1e-10
            sign, log_det = np.linalg.slogdet(covariance)
            if sign <= 0 or not isfinite(float(log_det)):
                raise ValueError(f"Covariance matrix for state {state} is not positive definite")
            inverse = np.linalg.inv(covariance)
            log_norm_const = 0.5 * self.dimension * log(2 * np.pi) + 0.5 * float(log_det)
            caches.append(
                _EmissionCache(
                    mean=np.asarray(mean_values, dtype=np.float64),
                    inverse_covariance=inverse,
                    log_norm_const=log_norm_const,
                )
            )
        return tuple(caches)

    @staticmethod
    def _validate_params(params: HMMParameters) -> HMMParameters:
        if params.states < 2:
            raise ValueError("HMM requires at least two states")
        if len(params.transition_matrix) != params.states:
            raise ValueError("transition_matrix row count must equal states")
        if len(params.emission_means) != params.states:
            raise ValueError("emission_means length must equal states")
        if len(params.emission_covariances) != params.states:
            raise ValueError("emission_covariances length must equal states")
        if len(params.initial_probabilities) != params.states:
            raise ValueError("initial_probabilities length must equal states")
        if params.state_labels and len(params.state_labels) != params.states:
            raise ValueError("state_labels length must equal states")

        dimension = len(params.emission_means[0])
        if dimension < 1:
            raise ValueError("emission feature dimension must be positive")
        for row in params.transition_matrix:
            if len(row) != params.states:
                raise ValueError("transition_matrix must be square")
            if not np.isclose(sum(row), 1.0, atol=1e-6):
                raise ValueError("transition_matrix rows must sum to 1")
        if not np.isclose(sum(params.initial_probabilities), 1.0, atol=1e-6):
            raise ValueError("initial_probabilities must sum to 1")
        for mean in params.emission_means:
            if len(mean) != dimension:
                raise ValueError("all emission means must share one dimension")
        for covariance in params.emission_covariances:
            if len(covariance) != dimension or any(len(row) != dimension for row in covariance):
                raise ValueError("emission covariances must be k x k")
        return params

    @staticmethod
    def _to_log_matrix(values: tuple[tuple[float, ...], ...]) -> np.ndarray:
        matrix = np.asarray(values, dtype=np.float64)
        return np.where(matrix > 0, np.log(matrix), -np.inf)

    @staticmethod
    def _to_log_vector(values: tuple[float, ...]) -> np.ndarray:
        vector = np.asarray(values, dtype=np.float64)
        return np.where(vector > 0, np.log(vector), -np.inf)


# ─────────────────────────────────────────────────────────────────────────────
# §3  FACTORY & ORCHESTRATION FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────


def default_hmm_parameters() -> HMMParameters:
    """Default 3-state regime model for OHLCV features."""
    return HMMParameters(
        states=3,
        state_labels=("BULL_QUIET", "MEAN_REVERT", "CRISIS"),
        transition_matrix=(
            (0.94, 0.05, 0.01),
            (0.06, 0.88, 0.06),
            (0.08, 0.17, 0.75),
        ),
        emission_means=(
            (0.0005, 0.010, -0.20),
            (0.0000, 0.018, 0.20),
            (-0.0025, 0.045, 1.60),
        ),
        emission_covariances=(
            ((1e-5, 0.0, 0.0), (0.0, 5e-5, 0.0), (0.0, 0.0, 0.25)),
            ((2e-5, 0.0, 0.0), (0.0, 1e-4, 0.0), (0.0, 0.0, 0.36)),
            ((8e-5, 0.0, 0.0), (0.0, 6e-4, 0.0), (0.0, 0.0, 1.44)),
        ),
        initial_probabilities=(0.60, 0.30, 0.10),
    )


def analyze_hmm_regime_from_ohlcv(df: pd.DataFrame) -> HMMAnalysisOutput:
    """Convenience helper for the technical terminal."""
    return HMMInferenceEngine(default_hmm_parameters()).analyze_ohlcv(df)


def build_ohlcv_observations(df: pd.DataFrame) -> tuple[MarketObservation, ...]:
    """Map OHLCV bars into [log_return, realized_volatility, volume_zscore]."""
    frame = _validate_ohlcv_frame(df)
    close = frame["close"].astype(float)
    returns = np.log(close / close.shift(1))
    volatility = returns.rolling(20, min_periods=5).std()
    volume = frame["volume"].astype(float)
    volume_mean = volume.rolling(20, min_periods=5).mean()
    volume_std = volume.rolling(20, min_periods=5).std().replace(0, np.nan)
    volume_zscore = (volume - volume_mean) / volume_std

    features = pd.DataFrame(
        {
            "date": frame["date"] if "date" in frame.columns else frame.index.astype(str),
            "return": returns,
            "volatility": volatility,
            "volume_zscore": volume_zscore,
        }
    ).dropna()
    observations: list[MarketObservation] = []
    for date_value, log_return, realized_volatility, volume_zscore in features.itertuples(
        index=False,
        name=None,
    ):
        observations.append(
            MarketObservation(
                timestamp=_observation_timestamp(date_value),
                features=(float(log_return), float(realized_volatility), float(volume_zscore)),
            )
        )
    return tuple(observations)


# ─────────────────────────────────────────────────────────────────────────────
# §4  PRIVATE HELPERS
# ─────────────────────────────────────────────────────────────────────────────


def _validate_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Empty DataFrame")
    frame = df.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    missing = set(_REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    for column in _REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=list(_REQUIRED_COLUMNS))
    frame = frame[frame["volume"] > 0].copy()
    if len(frame) < _MIN_OBSERVATIONS:
        raise ValueError(f"Need at least {_MIN_OBSERVATIONS} OHLCV rows")
    if "date" in frame.columns:
        frame = frame.reset_index(drop=True).sort_values("date")
    elif isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.sort_index()
        frame["date"] = frame.index
    else:
        n = len(frame)
        frame["date"] = pd.date_range(
            end=pd.Timestamp.now("UTC").normalize(),
            periods=n,
            freq="D",
        )
    return frame.reset_index(drop=True)


def _observation_timestamp(date_value: object) -> str:
    """ISO date string for HMM history; never treat bare ordinals as datetimes."""
    if date_value is None or (isinstance(date_value, float) and pd.isna(date_value)):
        return ""
    if isinstance(date_value, pd.Timestamp):
        return str(date_value.date())
    if isinstance(date_value, str) and date_value.isdigit():
        return ""
    try:
        parsed = pd.Timestamp(date_value)
        if parsed.year < 1970:
            return ""
        return str(parsed.date())
    except (TypeError, ValueError, pd.errors.OutOfBoundsDatetime):
        return str(date_value)[:32] if date_value else ""


def _log_sum_exp(values: np.ndarray) -> float:
    max_value = float(np.max(values))
    if not isfinite(max_value):
        return -np.inf
    return max_value + log(sum(exp(float(value - max_value)) for value in values))


def _normalised_entropy(probabilities: np.ndarray) -> float:
    clean = np.clip(probabilities, 1e-12, 1.0)
    entropy = -float(np.sum(clean * np.log(clean)))
    return entropy / log(len(clean)) if len(clean) > 1 else 0.0


def _regime_signal(entropy: float) -> str:
    if entropy > 0.70:
        return "CRITICAL"
    if entropy > 0.40:
        return "SHIFTING"
    return "STABLE"
