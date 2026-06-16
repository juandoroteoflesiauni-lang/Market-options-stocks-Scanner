"""Tests para el ML Data Pipeline. # [TH][IM]"""

import pytest
import pandas as pd
from backend.ml_engine.data_pipeline import _flatten_dict

def test_flatten_dict():
    input_d = {
        "volume_z": 1.5,
        "is_bullish": True,
        "nested": {
            "score": 0.8
        }
    }
    out = {}
    _flatten_dict(input_d, out, prefix="ind_")
    
    assert out["ind_volume_z"] == 1.5
    assert out["ind_is_bullish"] == 1.0
    assert out["ind_nested_score"] == 0.8
