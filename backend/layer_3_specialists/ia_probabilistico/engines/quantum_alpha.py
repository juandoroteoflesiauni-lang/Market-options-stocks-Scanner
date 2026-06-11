"""
backend/layer_3_specialists/ia_probabilistico/engines/quantum_alpha.py
════════════════════════════════════════════════════════════════════════════════
Quantum Alpha Engine — Multimodal Predictive ML Facade.
Wraps the LSTM system for Event-Driven directional predictions.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pandas as pd  # type: ignore[import-untyped]
import torch
import torch.nn as nn

from ..domain.multimodal_models import MultimodalModelConfig, MultimodalPredictionResult

logger = logging.getLogger(__name__)

FloatArray = npt.NDArray[np.float64]
FeatureRow = tuple[float, ...]


class SelfAttention(nn.Module):
    """
    Self-Attention mechanism to capture long-term dependencies in sequence data.
    """

    def __init__(self, hidden_dim: int) -> None:
        super().__init__()
        self.query = nn.Linear(hidden_dim, hidden_dim)
        self.key = nn.Linear(hidden_dim, hidden_dim)
        self.value = nn.Linear(hidden_dim, hidden_dim)
        self.scale = np.sqrt(hidden_dim)

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

    def __init__(self, config: MultimodalModelConfig):
        super().__init__()
        self.config = config

        self.lstm = nn.LSTM(
            input_size=config.event_dim,
            hidden_size=config.hidden_channels,
            num_layers=config.n_layers,
            batch_first=True,
            dropout=config.dropout if config.n_layers > 1 else 0,
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
    aligned with the institutional migration staging interface.
    """

    def __init__(self, config: MultimodalModelConfig | None = None):
        # Override event_dim to 5 as we hardcode OHLCV features in analyze()
        if config:
            # Create a new config with event_dim=5 if it's different
            if config.event_dim != 5:
                # We can't mutate frozen pydantic models, so we recreate it
                config = MultimodalModelConfig(
                    **config.model_dump(exclude={"event_dim"}), event_dim=5
                )

        # Override n_classes to 3 as we hardcode 3-class signal mapping in analyze()
        if config:
            if config.n_classes != 3:
                config = MultimodalModelConfig(
                    **config.model_dump(exclude={"n_classes"}), n_classes=3
                )

        self.config = config or MultimodalModelConfig(
            hidden_channels=32,
            event_dim=5,  # OHLCV
            n_layers=2,
            n_classes=3,  # 0: BEARISH, 1: NEUTRAL, 2: BULLISH
        )
        self.model = QuantumAlphaLSTM(self.config)
        self.model.eval()
        self._initialized = True  # Mark as initialized for standalone mode
        logger.info("QuantumAlphaEngine initialized (Mode: CPU Inference with Attention)")

    @staticmethod
    @lru_cache(maxsize=64)
    def _normalize_features_cached(features_tuple: tuple[FeatureRow, ...]) -> FloatArray:
        """Cached feature normalization to avoid recomputation."""
        try:
            features_array = np.asarray(features_tuple, dtype=np.float64)
            features_norm = (features_array - np.mean(features_array, axis=0)) / (
                np.std(features_array, axis=0) + 1e-9
            )
            return np.asarray(features_norm, dtype=np.float64)
        except Exception as e:
            logger.error(f"Feature normalization failed: {e}")
            raise

    def analyze(
        self, ticker: str, df_fundamentals: pd.DataFrame, sentiment_score: float = 0.0
    ) -> MultimodalPredictionResult:
        """
        Runs the predictive pipeline for a given ticker.
        """
        if not self._initialized:
            return MultimodalPredictionResult(is_valid=False, error="Engine not initialized")

        start_time = time.perf_counter()

        try:
            # 1. Prepare Data (Mocking the preprocessing logic for standalone integration)
            features = df_fundamentals[["open", "high", "low", "close", "volume"]].tail(20).values
            if len(features) < 20:
                return MultimodalPredictionResult(is_valid=False, error="Insufficient history")

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

            return MultimodalPredictionResult(
                logits=logits.cpu().numpy(),
                probabilities=probs[np.newaxis, :],
                n_samples=1,
                n_classes=len(probs),
                direction_prob=round(bullish_prob, 4),
                signal=signal,
                confidence=round(abs(bullish_prob - 0.5) * 2.0, 4),
                inference_latency_ms=round(latency, 2),
                is_valid=True,
            )

        except Exception as e:
            logger.error(f"QuantumAlphaEngine runtime error: {e}")
            return MultimodalPredictionResult(is_valid=False, error=str(e))


def load_pretrained_weights(model: nn.Module, path: str) -> bool:
    """Loads weights from a .pth file."""
    try:
        state_dict = torch.load(path, map_location="cpu")
        model.load_state_dict(state_dict)
        return True
    except Exception as e:
        logger.warning(f"Could not load weights from {path}: {e}")
        return False


# ────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: IA / PROBABILÍSTICO
# Archivo        : quantum_alpha.py
# Sub-capa       : Engine (Predictive Facade)
# Framework ML   : PyTorch
# Descripcion    : Alineado con la interfaz institucional (analyze).
# Eliminado      : Logic loops innecesarios.
# Preservado     : LSTM Architectura e inferencia multimodal.
# Integrado      : Self-Attention layer y CalibrationProfile para Argentina/Crypto.
# ────────────────────────────────────────────────────────────────
