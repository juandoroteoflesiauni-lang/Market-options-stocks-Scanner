import os
import tempfile

import numpy as np
import pytest
import torch

from backend.models.result import Result
from src.quant_engine.engines.predictive.multimodal_predictive import (
    FusionReport,
    MultimodalBatch,
    MultimodalPredictiveEngine,
)
from src.quant_engine.engines.predictive.quantum_alpha import (
    MultimodalModelConfig,
    QuantumAlphaLSTM,
)


@pytest.mark.asyncio
async def test_multimodal_predictive_engine():
    config = MultimodalModelConfig(
        hidden_channels=8, event_dim=5, n_layers=1, n_classes=3, dropout=0.0
    )

    # Save a temporary weights file for QuantumAlphaLSTM
    temp_model = QuantumAlphaLSTM(config)
    with tempfile.NamedTemporaryFile(suffix=".pth", delete=False) as tmp:
        weights_path = tmp.name

    try:
        torch.save(temp_model.state_dict(), weights_path)

        # Initialize engine
        engine = MultimodalPredictiveEngine(weights_path=weights_path, config=config)

        # 1. Test prepare_batch
        # N = 25 >= config.sequence_length (which is 20 by default)
        fund_data = np.random.rand(25, 5)
        news_data = np.random.rand(25, 3)

        res_batch = engine.prepare_batch(
            fund_data=fund_data, news_data=news_data, ticker="AAPL", config=config
        )
        assert isinstance(res_batch, Result)
        assert res_batch.is_success
        batch = res_batch.unwrap()
        assert isinstance(batch, MultimodalBatch)
        assert batch.ticker == "AAPL"
        assert batch.tensor_sequence.shape == (1, 20, 5, 3)
        assert batch.event_sequence.shape == (1, 20, 1)

        # Test prepare_batch with insufficient data
        fund_data_short = np.random.rand(10, 5)
        res_batch_short = engine.prepare_batch(
            fund_data=fund_data_short, news_data=news_data, ticker="AAPL", config=config
        )
        assert isinstance(res_batch_short, Result)
        assert res_batch_short.is_failure

        # 2. Test run_fusion_inference_async
        # Valid OHLCV data of shape (25, 5)
        ohlcv = np.random.rand(25, 5)
        gex_data = {"total_gex": 1500.0, "net_vanna_flow": 25.0}
        res_fusion = await engine.run_fusion_inference_async(
            symbol="AAPL", ohlcv=ohlcv, sentiment_score=0.7, gex_data=gex_data
        )
        assert isinstance(res_fusion, Result)
        assert res_fusion.is_success
        report = res_fusion.unwrap()
        assert isinstance(report, FusionReport)
        assert report.symbol == "AAPL"
        assert report.bias in ["WATCH", "LONG", "CASH"]
        assert 0.0 <= report.conviction <= 1.0
        assert report.fusion_metadata.vsa_expansion_forecast is False
        assert report.fusion_metadata.gex_gating_safe is True
        assert report.fusion_metadata.inference_latency >= 0.0

    finally:
        if os.path.exists(weights_path):
            os.remove(weights_path)
