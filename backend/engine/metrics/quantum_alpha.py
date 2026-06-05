"""
backend/engine/metrics/quantum_alpha.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Quantum Alpha Engine — Multimodal Predictive ML Facade distilled strictly for inference.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import torch
import torch.nn as nn
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger(__name__)

FloatArray = npt.NDArray[np.float64]
FeatureRow = tuple[float, ...]


class MultimodalModelConfig(BaseModel):
    """Configuration for the Multimodal LSTM Model."""
    model_config = ConfigDict(frozen=True)

    event_dim: int = 5
    hidden_channels: int = 32
    n_layers: int = 2
    n_classes: int = 3
    dropout: float = 0.0


class MultimodalPredictionResult(BaseModel):
    """Directional prediction result containing logits, probabilities, and confidence."""
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    logits: np.ndarray
    probabilities: np.ndarray
    n_samples: int
    n_classes: int
    direction_prob: float
    signal: str
    confidence: float
    inference_latency_ms: float


class SelfAttention(nn.Module):
    """
    Self-Attention mechanism to capture long-term dependencies in sequence data.
    """
    def __init__(self, hidden_dim: int) -> None:
        super().__init__()
        self.query = nn.Linear(hidden_dim, hidden_dim)
        self.key = nn.Linear(hidden_dim, hidden_dim)
        self.value = nn.Linear(hidden_dim, hidden_dim)
        self.scale = float(np.sqrt(hidden_dim))

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        # x shape: (batch, seq_len, hidden_dim)
        q = self.query(x)
        k = self.key(x)
        v = self.value(x)

        # Attention scores: (batch, seq_len, seq_len)
        scores = torch.bmm(q, k.transpose(1, 2)) / self.scale
        attn_weights = torch.softmax(scores, dim=-1)

        # Context vector: (batch, seq_len, hidden_dim)
        context = torch.bmm(attn_weights, v)
        return context, attn_weights


class QuantumAlphaLSTM(nn.Module):
    """
    Multimodal LSTM architecture for fusing Financial Time Series and Sentiment.
    """
    def __init__(self, config: MultimodalModelConfig) -> None:
        super().__init__()
        self.config = config

        self.lstm = nn.LSTM(
            input_size=config.event_dim,
            hidden_size=config.hidden_channels,
            num_layers=config.n_layers,
            batch_first=True,
            dropout=config.dropout if config.n_layers > 1 else 0.0
        )
        self.attention = SelfAttention(config.hidden_channels)
        self.fc = nn.Linear(config.hidden_channels, config.n_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        lstm_out, _ = self.lstm(x)
        attn_out, _ = self.attention(lstm_out)
        # Global average pooling over the sequence dimension
        context = attn_out.mean(dim=1)
        return cast(torch.Tensor, self.fc(context))


class CalibrationProfile:
    """Market-specific calibration profiles."""
    @staticmethod
    def get_profile(ticker: str) -> dict[str, Any]:
        # Simple heuristic: high vol assets (Argentine/Crypto) vs standard US equities
        if ticker.endswith(".BA") or ticker in ["GGAL", "YPF", "PAMP", "BTC", "ETH"]:
            return {"long_threshold": 0.60, "cash_threshold": 0.45, "vol_scaler": 1.5}
        return {"long_threshold": 0.65, "cash_threshold": 0.40, "vol_scaler": 1.0}


class QuantumAlphaEngine:
    """
    Facade for the Quantum Alpha (LSTM + Attention) predictive engine,
    distilled strictly for inference and using the Result monad.
    """
    def __init__(self, weights_path: str, config: MultimodalModelConfig | None = None) -> None:
        # Override event_dim to 5 as we hardcode OHLCV features in analyze()
        if config and (config.event_dim != 5 or config.n_classes != 3):
            config = MultimodalModelConfig(
                hidden_channels=config.hidden_channels,
                event_dim=5,
                n_layers=config.n_layers,
                n_classes=3,
                dropout=config.dropout
            )

        self.config = config or MultimodalModelConfig(
            hidden_channels=32,
            event_dim=5,  # OHLCV
            n_layers=2,
            n_classes=3   # 0: BEARISH, 1: NEUTRAL, 2: BULLISH
        )
        self.model = QuantumAlphaLSTM(self.config)
        self.model.eval()

        if not load_pretrained_weights(self.model, weights_path):
            raise RuntimeError(f"Failed to load pretrained weights from {weights_path}")

        self._initialized = True
        logger.info(
            "QuantumAlphaEngine initialized with weights from %s (Mode: CPU Inference)",
            weights_path,
        )

    @staticmethod
    @lru_cache(maxsize=64)
    def _normalize_features_cached(features_tuple: tuple[FeatureRow, ...]) -> FloatArray:
        """Cached feature normalization to avoid recomputation."""
        try:
            features_array = np.asarray(features_tuple, dtype=np.float64)
            mean = np.mean(features_array, axis=0)
            std = np.std(features_array, axis=0)
            features_norm = (features_array - mean) / (std + 1e-9)
            return np.asarray(features_norm, dtype=np.float64)
        except Exception as e:
            logger.error("Feature normalization failed: %s", e)
            raise

    def analyze(
        self,
        ticker: str,
        ohlcv: FloatArray,
        sentiment_score: float = 0.0
    ) -> Result[MultimodalPredictionResult]:
        """
        Runs the predictive pipeline for a given ticker.
        """
        if not getattr(self, "_initialized", False):
            return Result.failure(reason="Engine not initialized")

        start_time = time.perf_counter()

        try:
            # 1. Prepare Data
            if ohlcv.ndim != 2 or ohlcv.shape[1] != 5:
                return Result.failure(
                    reason="OHLCV matrix must be a 2D numpy array of shape (N, 5)"
                )

            if len(ohlcv) < 20:
                return Result.failure(reason="Insufficient history")

            features = ohlcv[-20:]

            # Use cached normalization
            features_tuple = tuple(map(tuple, features))
            features_norm = QuantumAlphaEngine._normalize_features_cached(features_tuple)
            x_tensor = torch.from_numpy(features_norm).float().unsqueeze(0)  # (B=1, T=20, I=5)

            # 2. Predict
            with torch.no_grad():
                logits = self.model(x_tensor)
                probs = torch.softmax(logits, dim=-1).squeeze().cpu().numpy()

            latency = (time.perf_counter() - start_time) * 1000

            # 3. Map to signals (Staging thresholds with CalibrationProfile)
            profile = CalibrationProfile.get_profile(ticker)
            bullish_prob = float(probs[2])
            signal = "WATCH"
            if bullish_prob >= profile["long_threshold"]:
                signal = "LONG"
            elif bullish_prob < profile["cash_threshold"]:
                signal = "CASH"

            prediction = MultimodalPredictionResult(
                logits=logits.cpu().numpy(),
                probabilities=probs[np.newaxis, :],
                n_samples=1,
                n_classes=len(probs),
                direction_prob=round(bullish_prob, 4),
                signal=signal,
                confidence=round(abs(bullish_prob - 0.5) * 2.0, 4),
                inference_latency_ms=round(latency, 2)
            )
            return Result.success(prediction)

        except Exception as e:
            logger.error("QuantumAlphaEngine runtime error: %s", e)
            return Result.failure(reason=str(e))


def load_pretrained_weights(model: nn.Module, path: str) -> bool:
    """Loads weights from a .pth file."""
    try:
        state_dict = torch.load(path, map_location="cpu")
        model.load_state_dict(state_dict)
        return True
    except Exception as e:
        logger.warning("Could not load weights from %s: %s", path, e)
        return False
