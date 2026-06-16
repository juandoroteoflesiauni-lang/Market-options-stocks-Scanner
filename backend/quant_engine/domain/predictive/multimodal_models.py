"""
backend/layer_3_specialists/ia_probabilistico/domain/multimodal_models.py
════════════════════════════════════════════════════════════════════════════════
Domain contracts for multimodal event-driven predictive modeling.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from enum import Enum

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, field_validator

FloatArray = npt.NDArray[np.float64]


class PredictionTargetMode(str, Enum):
    OPEN_TO_OPEN = "open_to_open"
    CLOSE_TO_CLOSE = "close_to_close"
    OPEN_TO_CLOSE = "open_to_close"


class MultimodalModelConfig(BaseModel):
    """Immutable runtime configuration for the multimodal predictive engine."""

    model_config = ConfigDict(frozen=True)

    sequence_length: int = Field(default=20, ge=1)
    news_lookback_days: int = Field(default=6, ge=1)
    normalize: bool = True
    label_target: PredictionTargetMode = PredictionTargetMode.OPEN_TO_CLOSE
    prediction_horizon: int = Field(default=6, ge=1)

    hidden_channels: int = Field(default=32, ge=1)
    event_dim: int = Field(default=5, ge=1)
    n_layers: int = Field(default=2, ge=1)
    n_classes: int = Field(default=3, ge=1)
    dropout: float = Field(default=0.2, ge=0.0, le=0.9)
    kernel_size: int = Field(default=1, ge=1)
    # MIGRATION: device — resolver via torch.device("cuda" if available else "cpu")
    device: str = "cpu"


class MultimodalBatch(BaseModel):
    """Prepared tensor batch for sequence inference/training."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    ticker: str
    sequence_length: int = Field(..., ge=1)
    prediction_horizon: int = Field(..., ge=1)
    tensor_sequence: FloatArray | None = None
    event_sequence: FloatArray | None = None
    labels: FloatArray | None = None
    sample_dates_utc: tuple[str, ...] = Field(default_factory=tuple)
    feature_names_fundamental: tuple[str, ...] = Field(default_factory=tuple)
    feature_names_news: tuple[str, ...] = Field(default_factory=tuple)
    is_valid_sequence: bool
    error: str | None = None

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker cannot be empty")
        return normalized


class MultimodalPredictionResult(BaseModel):
    """Immutable prediction envelope emitted by multimodal inference."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    logits: FloatArray | None = None
    probabilities: FloatArray | None = None
    n_samples: int = Field(default=0, ge=0)
    n_classes: int = Field(default=0, ge=0)
    device: str = "cpu"

    # Staging Alignment (QuantumAlphaResult)
    direction_prob: float = 0.5
    signal: str = "WATCH"
    confidence: float = 0.0
    inference_latency_ms: float = 0.0

    is_valid: bool
    error: str | None = None


class SentimentResult(BaseModel):
    """Refined sentiment result from institutional aggregator."""

    model_config = ConfigDict(frozen=True)

    score: float = 0.0  # [-1, 1]
    consensus: str = "NEUTRAL"
    confidence: float = 0.0
    sentiment_score: float = 0.5  # Normalized [0, 1] for MIC
    news_count: int = 0
    top_themes: list[str] = Field(default_factory=list)

    # UI Compatibility Fields
    buzz_score: float = 0.0
    twitter_impact: float = 0.0
    is_hot: bool = False


__all__ = [
    "MultimodalBatch",
    "MultimodalModelConfig",
    "MultimodalPredictionResult",
    "PredictionTargetMode",
    "SentimentResult",
]

# ────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: IA / PROBABILÍSTICO
# Archivo        : multimodal_models.py
# Sub-capa       : Modelo (Domain Contracts)
# Framework ML   : Pydantic | numpy
# Eliminado      : Referencias legacy a quantumbeta/domain path.
# Preservado     : MultimodalBatch, MultimodalModelConfig, MultimodalPredictionResult
# Pendientes     : Device assignment a resolver en infra.
# ────────────────────────────────────────────────────────────────
