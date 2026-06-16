from __future__ import annotations
from typing import Any
"""
backend/engine/metrics/multimodal_predictive.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Multimodal Event-Driven Predictive Engine distilled strictly for async inference.
Fuses Fundamentals and Sentiment via Outer-Product Tensors.
"""


import asyncio
import logging
from functools import lru_cache

import numpy as np
import numpy.typing as npt
import torch as _torch
import torch.nn as _nn
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result
from backend.quant_engine.math.technical.matrix_ops import calculate_probabilistic_gex_gating

from .quantum_alpha import MultimodalModelConfig, QuantumAlphaEngine

logger = logging.getLogger("quantumbeta.engines.multimodal_predictive")

FloatArray = npt.NDArray[np.float64]
FeatureRow = tuple[float, ...]


class MultimodalBatch(BaseModel):
    """Pydantic model representing a batch of aligned multimodal inputs."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    ticker: str
    sequence_length: int
    prediction_horizon: int
    tensor_sequence: np.ndarray[Any, Any]
    event_sequence: np.ndarray[Any, Any]
    is_valid_sequence: bool


class FusionMetadata(BaseModel):
    """Metadata for the multimodal fusion report."""

    model_config = ConfigDict(frozen=True)

    vsa_expansion_forecast: bool
    gex_gating_safe: bool
    inference_latency: float


class FusionReport(BaseModel):
    """Consolidated report of the multimodal prediction fusion."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    bias: str
    conviction: float
    fusion_metadata: FusionMetadata


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
        self,
        x_t: _torch.Tensor,
        h_prev: _torch.Tensor,
        c_prev: _torch.Tensor,
        e_t: _torch.Tensor,
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
        c_tilde = _torch.tanh(self.candidate_gate(base))
        f_t = _torch.sigmoid(self.forget_gate(base))
        i_t = _torch.sigmoid(self.input_gate(base))
        c_hat = f_t * c_prev + i_t * c_tilde
        r_t = _torch.sigmoid(self.event_retention_gate(e_t)).unsqueeze(-1).unsqueeze(-1)
        c_r = _torch.tanh(c_hat)
        c_t = c_hat + r_t * c_r - c_r
        h_t = _torch.sigmoid(self.output_gate(base)) * _torch.tanh(c_t)
        return h_t, c_t

    def init_hidden(
        self,
        batch_size: int,
        device: _torch.device,
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
        self.dropout_layer = _nn.Dropout(dropout)
        self.classifier = _nn.Linear(hidden_channels, n_classes)

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
            _torch.Tensor,
            self.classifier(self.dropout_layer(h_states[-1].mean(dim=(2, 3)))),
        )


# ────────────────────────────────────────────────────────────────
# PUBLIC ENGINE
# ────────────────────────────────────────────────────────────────


class MultimodalPredictiveEngine:
    """Institutional engine for multimodal sequence preparation and inference."""

    def __init__(self, weights_path: str, config: MultimodalModelConfig | None = None) -> None:
        self.config = config or MultimodalModelConfig()
        self.ai_engine = QuantumAlphaEngine(weights_path=weights_path, config=self.config)

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
            logger.error("Tensor fusion failed: %s", e)
            raise

    @staticmethod
    def prepare_batch(
        fund_data: FloatArray,
        news_data: FloatArray,
        ticker: str,
        config: MultimodalModelConfig,
    ) -> Result[MultimodalBatch]:
        """Aligns data and builds fusion tensors using numpy arrays."""
        try:
            if len(fund_data) < config.sequence_length:
                return Result.failure(reason="Insufficient fundamental data history")
            if len(news_data) < config.sequence_length:
                return Result.failure(reason="Insufficient news data history")

            aligned_fund = fund_data[-config.sequence_length :]
            aligned_news = news_data[-config.sequence_length :]

            # Outer-Product Tensor Fusion with caching
            fund_tuple = tuple(map(tuple, aligned_fund))
            news_tuple = tuple(map(tuple, aligned_news))

            tensor_3d = MultimodalPredictiveEngine._prepare_fusion_tensor(
                fund_tuple, news_tuple, config.sequence_length
            )

            batch = MultimodalBatch(
                ticker=ticker,
                sequence_length=config.sequence_length,
                prediction_horizon=config.prediction_horizon,
                tensor_sequence=np.expand_dims(tensor_3d, 0),
                event_sequence=np.random.rand(1, config.sequence_length, 1),
                is_valid_sequence=True,
            )
            return Result.success(batch)
        except Exception as e:
            logger.error("Prepare batch failed: %s", e)
            return Result.failure(reason=str(e))

    async def run_fusion_inference_async(
        self,
        symbol: str,
        ohlcv: FloatArray,
        sentiment_score: float = 0.0,
        gex_data: dict[str, Any] | None = None,
    ) -> Result[FusionReport]:
        """High-level Orchestrator Facade (VSA + DL + GEX).

        Async version for better performance.
        """
        try:
            # Run DL Inference in executor
            loop = asyncio.get_running_loop()
            dl_result_wrapped = await loop.run_in_executor(
                None, self.ai_engine.analyze, symbol, ohlcv, sentiment_score
            )

            if dl_result_wrapped.is_failure:
                return Result.failure(reason=f"DL analysis failed: {dl_result_wrapped.reason}")

            dl_result = dl_result_wrapped.unwrap()

            # 3. GEX Gating
            is_safe_wrapped = calculate_probabilistic_gex_gating(
                current_gex=gex_data.get("total_gex", 0.0) if gex_data else 0.0,
                vanna_flow=gex_data.get("net_vanna_flow", 0.0) if gex_data else 0.0,
                regime_confidence=0.8,
            )

            if is_safe_wrapped.is_failure:
                return Result.failure(
                    reason=f"GEX gating calculation failed: {is_safe_wrapped.reason}"
                )
            is_safe = is_safe_wrapped.unwrap()

            # TODO: Integrar VSAForecastEngine cuando se migre el Sector 3
            vsa_expansion_forecast = False

            fusion_score = dl_result.direction_prob
            if vsa_expansion_forecast and sentiment_score > 0.5:
                fusion_score = min(0.99, fusion_score * 1.1)

            report = FusionReport(
                symbol=symbol,
                bias=dl_result.signal,
                conviction=fusion_score,
                fusion_metadata=FusionMetadata(
                    vsa_expansion_forecast=vsa_expansion_forecast,
                    gex_gating_safe=is_safe,
                    inference_latency=dl_result.inference_latency_ms,
                ),
            )
            return Result.success(report)
        except Exception as e:
            logger.error("Fusion failed: %s", e)
            return Result.failure(reason=str(e))
