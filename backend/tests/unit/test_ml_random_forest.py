"""Tests para Random Forest Model. # [TH][IM]"""

import pytest
import pandas as pd
from backend.ml_engine.models.random_forest_classifier import TradePredictor

def test_trade_predictor_empty_df():
    predictor = TradePredictor()
    metrics = predictor.train(pd.DataFrame())
    assert metrics == {}

def test_trade_predictor_training():
    predictor = TradePredictor()
    df = pd.DataFrame({
        "ind_vol": [1.0] * 10 + [0.1] * 10,
        "ind_macd": [0.5] * 10 + [-0.5] * 10,
        "target_win": [1] * 10 + [0] * 10
    })
    metrics = predictor.train(df)
    
    assert "accuracy" in metrics
    assert metrics["accuracy"] >= 0.0

    prob = predictor.predict_prob({"vol": 1.0, "macd": 0.5})
    assert isinstance(prob, float)
    assert 0.0 <= prob <= 1.0
