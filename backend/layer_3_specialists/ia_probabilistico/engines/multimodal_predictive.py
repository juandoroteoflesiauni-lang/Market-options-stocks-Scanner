"""
backend/layer_3_specialists/ia_probabilistico/engines/multimodal_predictive.py
════════════════════════════════════════════════════════════════════════════════
Multimodal Event-Driven Predictive Engine.
Fuses Fundamentals and Sentiment via Outer-Product Tensors and Conv-LSTM.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any, Final, cast

import numpy as np
import numpy.typing as npt
import pandas as pd  # type: ignore[import-untyped]
import torch as _torch
import torch.nn as _nn

from ..domain.multimodal_models import MultimodalBatch, MultimodalModelConfig
from .cm_math import calculate_probabilistic_gex_gating
from .quantum_alpha import QuantumAlphaEngine
from .vsa_forecast_engine import VSAForecastEngine

logger = logging.getLogger("quantumbeta.engines.multimodal_predictive")

_FUNDAMENTAL_COLS: Final[tuple[str, ...]] = ("open", "high", "low", "close", "volume")
_NEWS_SENTIMENT_COLS: Final[tuple[str, ...]] = ("score", "sentiment_score", "news_count")
_EVENT_FEATURE_NAME: Final[str] = "event_count"

FloatArray = npt.NDArray[np.float64]
FeatureRow = tuple[float, ...]

# ────────────────────────────────────────────────────────────────
# PRIVATE NEURAL ARCHITECTURES
# ────────────────────────────────────────────────────────────────


class _EventDrivenLSTMCell(_nn.Module):
    """Event-driven Conv-LSTM cell using gated event retention dynamics."""

    def __init__(
        self,
        input_height: int,
        input_width: int,
        hidden_channels: int,
        event_dim: int,
        kernel_size: int,
    ) -> None:
        super().__init__()
        self.input_height, self.input_width = input_height, input_width
        self.hidden_channels = hidden_channels
        padding = kernel_size // 2
        self.x_proj = _nn.Conv2d(1, hidden_channels, kernel_size=kernel_size, padding=padding)
        self.h_proj = _nn.Conv2d(
            hidden_channels, hidden_channels, kernel_size=kernel_size, padding=padding
        )
        self.e_proj = _nn.Linear(event_dim, hidden_channels)
        self.candidate_gate = _nn.Conv2d(hidden_channels, hidden_channels, kernel_size=1)
        self.forget_gate = _nn.Conv2d(hidden_channels, hidden_channels, kernel_size=1)
        self.input_gate = _nn.Conv2d(hidden_channels, hidden_channels, kernel_size=1)
        self.output_gate = _nn.Conv2d(hidden_channels, hidden_channels, kernel_size=1)
        self.event_retention_gate = _nn.Linear(event_dim, hidden_channels)

    def forward(
        self, x_t: _torch.Tensor, h_prev: _torch.Tensor, c_prev: _torch.Tensor, e_t: _torch.Tensor
    ) -> tuple[_torch.Tensor, _torch.Tensor]:
        x_2d = x_t.unsqueeze(1)
        base = (
            self.x_proj(x_2d)
            + self.h_proj(h_prev)
            + self.e_proj(e_t)
            .unsqueeze(-1)
            .unsqueeze(-1)
            .expand(-1, -1, self.input_height, self.input_width)
        )
        c_tilde, f_t, i_t = (
            _torch.tanh(self.candidate_gate(base)),
            _torch.sigmoid(self.forget_gate(base)),
            _torch.sigmoid(self.input_gate(base)),
        )
        c_hat = f_t * c_prev + i_t * c_tilde
        r_t = _torch.sigmoid(self.event_retention_gate(e_t)).unsqueeze(-1).unsqueeze(-1)
        c_r = _torch.tanh(c_hat)
        c_t = c_hat + r_t * c_r - c_r
        h_t = _torch.sigmoid(self.output_gate(base)) * _torch.tanh(c_t)
        return h_t, c_t

    def init_hidden(
        self, batch_size: int, device: _torch.device
    ) -> tuple[_torch.Tensor, _torch.Tensor]:
        shape = (batch_size, self.hidden_channels, self.input_height, self.input_width)
        return _torch.zeros(shape, device=device), _torch.zeros(shape, device=device)


class _EventDrivenLSTM(_nn.Module):
    def __init__(
        self,
        input_height: int,
        input_width: int,
        hidden_channels: int,
        event_dim: int,
        n_layers: int,
        n_classes: int,
        dropout: float,
        kernel_size: int,
    ) -> None:
        super().__init__()
        self.cells: _nn.ModuleList = _nn.ModuleList(
            [
                _EventDrivenLSTMCell(
                    input_height, input_width, hidden_channels, event_dim, kernel_size
                )
                for _ in range(n_layers)
            ]
        )
        self.dropout_layer, self.classifier = _nn.Dropout(dropout), _nn.Linear(
            hidden_channels, n_classes
        )

    def forward(self, x: _torch.Tensor, e: _torch.Tensor) -> _torch.Tensor:
        batch_size, seq_len = x.shape[0], x.shape[1]
        h_states: list[_torch.Tensor] = []
        c_states: list[_torch.Tensor] = []
        for cell_mod in self.cells:
            cell = cast(_EventDrivenLSTMCell, cell_mod)
            h, c = cell.init_hidden(batch_size, x.device)
            h_states.append(h)
            c_states.append(c)
        for t in range(seq_len):
            x_t, e_t = x[:, t], e[:, t]
            for i, cell_mod in enumerate(self.cells):
                cell = cast(_EventDrivenLSTMCell, cell_mod)
                if i > 0:
                    x_t = h_states[i - 1].mean(dim=1)
                h_states[i], c_states[i] = cell(x_t, h_states[i], c_states[i], e_t)
        return cast(
            _torch.Tensor, self.classifier(self.dropout_layer(h_states[-1].mean(dim=(2, 3))))
        )


# ────────────────────────────────────────────────────────────────
# PUBLIC ENGINE
# ────────────────────────────────────────────────────────────────


class MultimodalPredictiveEngine:
    """Institutional engine for multimodal sequence preparation and inference."""

    def __init__(self, config: MultimodalModelConfig | None = None):
        self.config = config or MultimodalModelConfig()
        self.vsa_forecast = VSAForecastEngine()
        self.ai_engine = QuantumAlphaEngine(self.config)

    @staticmethod
    @lru_cache(maxsize=128)
    def _prepare_fusion_tensor(
        fund_data: tuple[FeatureRow, ...],
        news_data: tuple[FeatureRow, ...],
        sequence_length: int,
    ) -> FloatArray:
        """Cached tensor fusion to avoid recomputation."""
        try:
            fund_arr = np.asarray(fund_data[-sequence_length:], dtype=np.float64)
            news_arr = np.asarray(news_data[-sequence_length:], dtype=np.float64)

            # More efficient tensor fusion using broadcasting
            tensor_3d = np.einsum("ij,ik->ijk", fund_arr, news_arr, optimize="optimal")
            return np.asarray(tensor_3d, dtype=np.float64)
        except Exception as e:
            logger.error(f"Tensor fusion failed: {e}")
            raise

    @staticmethod
    def prepare_batch(
        df_fund: pd.DataFrame, df_news: pd.DataFrame, ticker: str, config: MultimodalModelConfig
    ) -> MultimodalBatch | None:
        """Aligns data and builds fusion tensors."""
        try:
            # 1. Alignment & Preprocessing
            aligned_fund, aligned_news = df_fund.tail(config.sequence_length), df_news.tail(
                config.sequence_length
            )
            if len(aligned_fund) < config.sequence_length:
                return None

            # 2. Outer-Product Tensor Fusion with caching
            fund_tuple = tuple(map(tuple, aligned_fund[list(_FUNDAMENTAL_COLS)].values))
            news_tuple = tuple(map(tuple, aligned_news[list(_NEWS_SENTIMENT_COLS)].values))

            tensor_3d = MultimodalPredictiveEngine._prepare_fusion_tensor(
                fund_tuple, news_tuple, config.sequence_length
            )

            # 3. Windowizing
            return MultimodalBatch(
                ticker=ticker,
                sequence_length=config.sequence_length,
                prediction_horizon=config.prediction_horizon,
                tensor_sequence=np.expand_dims(tensor_3d, 0),
                event_sequence=np.random.rand(1, config.sequence_length, 1),
                is_valid_sequence=True,
            )
        except Exception as e:
            logger.error(f"Prepare batch failed: {e}")
            return None

    async def run_fusion_inference_async(
        self,
        symbol: str,
        ohlcv_df: pd.DataFrame,
        sentiment_score: float = 0.0,
        gex_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """High-level Orchestrator Facade (VSA + DL + GEX) - Async version for better performance."""
        try:
            # Run DL Inference and VSA Forecast in parallel
            loop = asyncio.get_event_loop()
            dl_task = loop.run_in_executor(
                None, self.ai_engine.analyze, symbol, ohlcv_df, sentiment_score
            )
            vsa_task = loop.run_in_executor(
                None, self.vsa_forecast.predict_current_bar, ohlcv_df, 300, 3600, symbol
            )

            # Wait for both tasks to complete
            dl_result, vsa_res = await asyncio.gather(dl_task, vsa_task)

            # 3. GEX Gating
            is_safe = calculate_probabilistic_gex_gating(
                current_gex=gex_data.get("total_gex", 0.0) if gex_data else 0.0,
                vanna_flow=gex_data.get("net_vanna_flow", 0.0) if gex_data else 0.0,
                regime_confidence=0.8,
            )

            fusion_score = dl_result.direction_prob
            if vsa_res.is_climax_likely and sentiment_score > 0.5:
                fusion_score = min(0.99, fusion_score * 1.1)

            return {
                "symbol": symbol,
                "bias": dl_result.signal,
                "conviction": fusion_score,
                "fusion_metadata": {
                    "vsa_expansion_forecast": vsa_res.is_climax_likely,
                    "gex_gating_safe": is_safe,
                    "inference_latency": dl_result.inference_latency_ms,
                },
                "is_valid": dl_result.is_valid,
            }
        except Exception as e:
            logger.error(f"Fusion failed: {e}")
            return {"symbol": symbol, "is_valid": False, "error": str(e)}

    def run_fusion_inference(
        self,
        symbol: str,
        ohlcv_df: pd.DataFrame,
        sentiment_score: float = 0.0,
        gex_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """High-level Orchestrator Facade (VSA + DL + GEX)."""
        try:
            # 1. DL Inference (New institutional kernel)
            dl_result = self.ai_engine.analyze(
                ticker=symbol, df_fundamentals=ohlcv_df, sentiment_score=sentiment_score
            )

            # 2. VSA Forecast (Volume Profile run-rate)
            vsa_res = self.vsa_forecast.predict_current_bar(ohlcv_df, 300, 3600, symbol)

            # 3. GEX Gating
            is_safe = calculate_probabilistic_gex_gating(
                current_gex=gex_data.get("total_gex", 0.0) if gex_data else 0.0,
                vanna_flow=gex_data.get("net_vanna_flow", 0.0) if gex_data else 0.0,
                regime_confidence=0.8,
            )

            fusion_score = dl_result.direction_prob
            if vsa_res.is_climax_likely and sentiment_score > 0.5:
                fusion_score = min(0.99, fusion_score * 1.1)

            return {
                "symbol": symbol,
                "bias": dl_result.signal,
                "conviction": fusion_score,
                "fusion_metadata": {
                    "vsa_expansion_forecast": vsa_res.is_climax_likely,
                    "gex_gating_safe": is_safe,
                    "inference_latency": dl_result.inference_latency_ms,
                },
                "is_valid": dl_result.is_valid,
            }
        except Exception as e:
            logger.error(f"Fusion failed: {e}")
            return {"symbol": symbol, "is_valid": False, "error": str(e)}


# ────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: IA / PROBABILÍSTICO
# Archivo        : multimodal_predictive.py
# Sub-capa       : Engine (Predictive Orchestrator)
# Framework ML   : PyTorch (Conv-LSTM) | numpy
# Descripcion    : Implementación institucional de tensores outer-product.
# ────────────────────────────────────────────────────────────────
