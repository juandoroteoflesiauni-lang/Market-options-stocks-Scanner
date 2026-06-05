"""
backend/layer_3_specialists/ia_probabilistico/engines/feedback_engine.py
════════════════════════════════════════════════════════════════════════════════
Feedback & Calibration Engine — closing the loop between projection and reality.
════════════════════════════════════════════════════════════════════════════════
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, cast

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]

logger = logging.getLogger(__name__)

class FeedbackCalibration:
    """
    Analyzes previous probabilistic projections vs realized price action
    to adaptively tune the mathematical kernels.
    """

    def __init__(self, storage_service: Any):
        self.storage = storage_service

    def calculate_model_error(self, history: list[dict[str, Any]], current_price: float) -> dict[str, float]:
        """
        Calculates the deviation between past projections and current realized price.
        """
        if not history or len(history) < 1:
            return {"bias": 0.0, "hit_rate": 0.5, "error_factor": 1.0}

        # Take the most recent analysis (usually index 0 if sorted by timestamp DESC)
        last_item = history[0]

        try:
            # We need the price at the time of that analysis.
            # If it's not in the record, we might need to fetch it or store it.
            # Assuming 'raw_json' contains the context price.
            raw_json_val = last_item.get("raw_json") or "{}"
            raw_data = cast(dict[str, Any], json.loads(raw_json_val) if isinstance(raw_json_val, str) else (raw_json_val if raw_json_val else {}))
            past_price = raw_data.get("context_price", 0.0)

            if past_price <= 0:
                return {"bias": 0.0, "hit_rate": 0.5, "error_factor": 1.0}

            realized_return = (current_price - past_price) / past_price

            # Simple bias: realized vs what the model 'expected' (kelly_prob as proxy for direction)
            # kelly_prob > 0.5 was bullish.
            model_direction = 1 if last_item.get("kelly_full", 0) > 0.1 else -1
            actual_direction = 1 if realized_return > 0 else -1

            is_hit = 1 if model_direction == actual_direction else 0

            # Calibration Factor (CF):
            # If we hit, we stay at 1.0. If we miss, we expand the uncertainty (sigma/vov).
            error_factor = 1.0 if is_hit else 1.25
            bias_adjustment = realized_return * 0.1 # Nudge drift towards reality

            return {
                "bias": float(bias_adjustment),
                "is_hit": bool(is_hit),
                "error_factor": float(error_factor),
                "realized_return": float(realized_return)
            }
        except Exception as e:
            logger.warning(f"Error calculating model feedback: {e}")
            return {"bias": 0.0, "hit_rate": 0.5, "error_factor": 1.0}

    def adapt_parameters(self, base_params: dict[str, Any], feedback: dict[str, Any]) -> dict[str, Any]:
        """
        Adjusts Merton Jump-Diffusion or Heston parameters based on feedback.
        """
        adj_params = base_params.copy()

        # 1. Adjust Drift (Mean Reversion Bias)
        if "mu_target" in adj_params:
            adj_params["mu_target"] += feedback.get("bias", 0.0)

        # 2. Adjust Uncertainty (Volatility Multiplier)
        if "vov" in adj_params:
            adj_params["vov"] *= feedback.get("error_factor", 1.0)

        # 3. Adjust Jump Intensity if we were surprised by a large move
        if abs(feedback.get("realized_return", 0)) > 0.05: # > 5% move
            if "jump_intensity" in adj_params:
                adj_params["jump_intensity"] *= 1.2

        return adj_params