import os
import tempfile
import numpy as np
import pytest
import torch

from src.quant_engine.engines.predictive.quantum_alpha import (
    QuantumAlphaEngine,
    MultimodalModelConfig,
    MultimodalPredictionResult,
)
from backend.models.result import Result


def test_engine_missing_weights_raises_error():
    # If the weights file does not exist, it should raise a RuntimeError
    with pytest.raises(RuntimeError):
        QuantumAlphaEngine(weights_path="non_existent_weights_file.pth")


def test_engine_initialization_and_prediction():
    # Create a dummy weights file
    config = MultimodalModelConfig(
        hidden_channels=8,
        event_dim=5,
        n_layers=1,
        n_classes=3,
        dropout=0.0
    )
    
    # Instantiate temporary model to save weights
    # We must use the same structure
    from src.quant_engine.engines.predictive.quantum_alpha import QuantumAlphaLSTM
    temp_model = QuantumAlphaLSTM(config)
    
    with tempfile.NamedTemporaryFile(suffix=".pth", delete=False) as tmp:
        weights_path = tmp.name
    
    try:
        torch.save(temp_model.state_dict(), weights_path)
        
        # Initialize engine
        engine = QuantumAlphaEngine(weights_path=weights_path, config=config)
        
        # Test analyze with insufficient history (N = 10 < 20)
        ohlcv_short = np.random.rand(10, 5)
        res_short = engine.analyze(ticker="AAPL", ohlcv=ohlcv_short)
        assert isinstance(res_short, Result)
        assert res_short.is_failure
        assert "Insufficient history" in res_short.reason
        
        # Test analyze with invalid dimensions (20, 4)
        ohlcv_wrong_dim = np.random.rand(20, 4)
        res_wrong = engine.analyze(ticker="AAPL", ohlcv=ohlcv_wrong_dim)
        assert isinstance(res_wrong, Result)
        assert res_wrong.is_failure
        assert "OHLCV matrix must be a 2D" in res_wrong.reason
        
        # Test analyze with correct history (N = 25 >= 20)
        ohlcv_ok = np.random.rand(25, 5)
        res_ok = engine.analyze(ticker="AAPL", ohlcv=ohlcv_ok)
        assert isinstance(res_ok, Result)
        assert res_ok.is_success
        
        pred_result = res_ok.unwrap()
        assert isinstance(pred_result, MultimodalPredictionResult)
        assert pred_result.logits.shape == (1, 3)
        assert pred_result.probabilities.shape == (1, 3)
        assert pred_result.n_samples == 1
        assert pred_result.n_classes == 3
        assert 0.0 <= pred_result.direction_prob <= 1.0
        assert pred_result.signal in ["WATCH", "LONG", "CASH"]
        assert 0.0 <= pred_result.confidence <= 1.0
        assert pred_result.inference_latency_ms >= 0.0
        
    finally:
        if os.path.exists(weights_path):
            os.remove(weights_path)
