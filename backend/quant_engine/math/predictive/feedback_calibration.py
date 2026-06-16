from __future__ import annotations
"""
backend/engine/metrics/feedback_calibration.py
Sector: Options / Feedback & Calibration Engine
[ARCH-1, PD-4]

Theoretical basis:
    Analyzes previous probabilistic projections vs realized price action
    to adaptively tune Merton Jump-Diffusion or Heston parameters.
"""


import logging

from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.feedback_calibration")


class ProjectionRecord(BaseModel):
    """Immutable projection record containing historical context price and kelly metric."""

    model_config = ConfigDict(frozen=True)

    context_price: float
    kelly_full: float


class FeedbackMetrics(BaseModel):
    """Immutable calibration metrics calculated from comparing projections to realized outcomes."""

    model_config = ConfigDict(frozen=True)

    bias: float
    is_hit: bool
    error_factor: float
    realized_return: float


class FeedbackCalibration:
    """Analyzes previous probabilistic projections vs realized price action to adapt parameters.

    Purely stateless.
    """

    def __init__(self) -> None:
        pass

    def calculate_model_error(
        self, history: list[ProjectionRecord], current_price: float
    ) -> Result[FeedbackMetrics]:
        """Calculates the deviation between past projections and current realized price."""
        if current_price <= 0.0:
            return Result.failure(reason="current_price must be greater than zero")

        if not history:
            # Return success with neutral state if history is empty
            return Result.success(
                FeedbackMetrics(bias=0.0, is_hit=False, error_factor=1.0, realized_return=0.0)
            )

        last_item = history[0]
        if last_item.context_price <= 0.0:
            return Result.failure(reason="context_price in history must be greater than zero")

        try:
            realized_return = (current_price - last_item.context_price) / last_item.context_price

            # Simple bias: realized vs what the model expected.
            # kelly_full > 0.1 was bullish in the original logic.
            model_direction = 1 if last_item.kelly_full > 0.1 else -1
            actual_direction = 1 if realized_return > 0.0 else -1

            is_hit = model_direction == actual_direction

            # Calibration Factor (CF):
            # If we hit, we stay at 1.0. If we miss, we expand the uncertainty (sigma/vov).
            error_factor = 1.0 if is_hit else 1.25
            bias_adjustment = realized_return * 0.1  # Nudge drift towards reality

            return Result.success(
                FeedbackMetrics(
                    bias=float(bias_adjustment),
                    is_hit=is_hit,
                    error_factor=float(error_factor),
                    realized_return=float(realized_return),
                )
            )
        except Exception as e:
            logger.warning(f"Error calculating model feedback: {e}")
            return Result.failure(reason=f"Failed to calculate feedback metrics: {e}")

    def adapt_parameters(
        self, base_params: dict[str, float], feedback: FeedbackMetrics
    ) -> Result[dict[str, float]]:
        """Adjusts Merton Jump-Diffusion or Heston parameters based on feedback metrics."""
        if not base_params:
            return Result.failure(reason="base_params dict cannot be empty")

        try:
            adj_params = base_params.copy()

            # 1. Adjust Drift (Mean Reversion Bias)
            if "mu_target" in adj_params:
                adj_params["mu_target"] += feedback.bias

            # 2. Adjust Uncertainty (Volatility Multiplier)
            if "vov" in adj_params:
                adj_params["vov"] *= feedback.error_factor

            # 3. Adjust Jump Intensity if we were surprised by a large move
            if abs(feedback.realized_return) > 0.05 and "jump_intensity" in adj_params:  # > 5% move
                adj_params["jump_intensity"] *= 1.2

            return Result.success(adj_params)
        except Exception as e:
            return Result.failure(reason=f"Failed to adapt parameters: {e}")
