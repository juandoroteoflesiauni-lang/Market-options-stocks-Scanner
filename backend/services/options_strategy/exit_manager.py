"""Gestor de salidas para posiciones de opciones MVP. # [PD-3][TH]"""

from __future__ import annotations

from decimal import Decimal

from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.models.options_strategy import (
    ExitEvaluation,
    NormalizedFeatures,
    OpenOptionsPosition,
    StrategyDecision,
    TradeDirection,
)


def _premium_loss_pct(position: OpenOptionsPosition) -> float:
    entry = float(position.entry_premium_usd)
    current = float(position.current_premium_usd)
    if entry <= 0:
        return 0.0
    return max(0.0, (entry - current) / entry * 100.0)


def _bias_against_position(
    features: NormalizedFeatures,
    direction: TradeDirection,
    threshold: float,
) -> bool:
    bias = features.global_bias
    if direction == "bullish":
        return bias <= -threshold
    if direction == "bearish":
        return bias >= threshold
    return False


class ExitManager:
    """Evalúa condiciones de salida por prima, tiempo y deterioro de tesis."""

    @classmethod
    def evaluate(
        cls,
        position: OpenOptionsPosition,
        features: NormalizedFeatures,
        *,
        config: OptionsStrategyConfigBundle | None = None,
    ) -> ExitEvaluation:
        active = config or get_options_strategy_config()
        rules = active.risk
        codes: list[str] = []

        loss_pct = _premium_loss_pct(position)
        if loss_pct >= rules.max_premium_loss_pct:
            return ExitEvaluation(
                symbol=position.symbol,
                as_of=features.as_of,
                decision=StrategyDecision.EXIT,
                reason_codes=("premium_stop_loss", f"loss_pct_{loss_pct:.1f}"),
            )

        if position.dte <= rules.min_dte_time_stop:
            return ExitEvaluation(
                symbol=position.symbol,
                as_of=features.as_of,
                decision=StrategyDecision.EXIT,
                reason_codes=("time_stop_dte",),
            )

        if _bias_against_position(
            features,
            position.direction,
            rules.thesis_bias_flip_threshold,
        ):
            codes.append("thesis_bias_flip")
            return ExitEvaluation(
                symbol=position.symbol,
                as_of=features.as_of,
                decision=StrategyDecision.EXIT,
                reason_codes=tuple(codes),
            )

        if features.dealer_regime == "suppressive" and position.direction == "bullish":
            codes.append("dealer_suppressive")
            return ExitEvaluation(
                symbol=position.symbol,
                as_of=features.as_of,
                decision=StrategyDecision.REDUCE,
                reason_codes=tuple(codes),
            )

        if features.dealer_regime == "supportive" and position.direction == "bearish":
            codes.append("dealer_supportive_against_short")
            return ExitEvaluation(
                symbol=position.symbol,
                as_of=features.as_of,
                decision=StrategyDecision.REDUCE,
                reason_codes=tuple(codes),
            )

        if features.reversal_risk_score >= 0.80:
            return ExitEvaluation(
                symbol=position.symbol,
                as_of=features.as_of,
                decision=StrategyDecision.REDUCE,
                reason_codes=("reversal_risk_elevated",),
            )

        return ExitEvaluation(
            symbol=position.symbol,
            as_of=features.as_of,
            decision=StrategyDecision.NO_TRADE,
            reason_codes=("hold_position",),
        )


__all__ = ["ExitManager"]
