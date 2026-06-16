"""Selector de estructura MVP y candidato con payoff. # [PD-3][TH]"""

from __future__ import annotations

from backend.config.logger_setup import get_logger
from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.models.options_strategy import (
    NormalizedFeatures,
    OptionsLayerOutput,
    OptionsStrategyCandidate,
    OptionsStrategyInput,
    OptionsStructure,
    StructureSelection,
    TradeDirection,
)
from backend.quant_engine.domain.options.strategy_models import (
    OptionLeg,
    OptionPayoffScenario,
    OptionStrategy,
)
from backend.quant_engine.engines.options.strategy_payoff import StrategyPayoffEngine
from backend.services.options_strategy._bars import resolve_spot_price
from backend.services.options_strategy._scoring import clamp01, clamp11
from backend.services.options_strategy._chain import chain_rows
from backend.services.options_strategy.contract_selector import ContractSelector

logger = get_logger(__name__)

_DEFAULT_RATE = 0.04


def _direction_from_bias(bias: float) -> TradeDirection:
    if bias > 0.12:
        return "bullish"
    if bias < -0.12:
        return "bearish"
    return "neutral"


def _composite_bias(features: NormalizedFeatures) -> float:
    return clamp11(
        0.35 * features.technical_direction_bias
        + 0.35 * features.predictive_direction_bias
        + 0.30 * features.options_direction_bias
    )


def _resolve_structure_r2(features: NormalizedFeatures) -> OptionsStructure:
    """R2: solo sesgo técnico — long call/put, short put, put credit spread."""
    bias = features.technical_direction_bias
    trend_q = features.trend_quality_score
    align = features.structure_alignment_score
    if bias < -0.12:
        return OptionsStructure.LONG_PUT
    if abs(bias) < 0.10:
        return OptionsStructure.NO_TRADE
    if bias < 0.12:
        return OptionsStructure.LONG_CALL if bias > 0 else OptionsStructure.LONG_PUT
    if bias >= 0.30 and trend_q >= 0.45:
        return OptionsStructure.SHORT_PUT
    if trend_q >= 0.40 and align >= 0.35:
        return OptionsStructure.PUT_CREDIT_SPREAD
    return OptionsStructure.LONG_CALL


def _resolve_structure(
    features: NormalizedFeatures,
    options: OptionsLayerOutput,
    *,
    profile: str = "full",
) -> OptionsStructure:
    if profile == "r2_basic":
        return _resolve_structure_r2(features)
    pref = options.structure_preference
    if pref not in {OptionsStructure.NO_TRADE, OptionsStructure.LONG_CALL, OptionsStructure.LONG_PUT}:
        if pref in {
            OptionsStructure.CALL_DEBIT_SPREAD,
            OptionsStructure.BULL_CALL_SPREAD,
            OptionsStructure.CALL_BUTTERFLY,
        }:
            return pref
    bias = _composite_bias(features)
    if options.dealer_regime == "pinning" and abs(bias) < 0.22:
        if features.iv_state in {"fair", "rich", "extreme"}:
            return OptionsStructure.CALL_BUTTERFLY
    if abs(bias) < 0.15:
        return OptionsStructure.NO_TRADE
    rich = features.iv_state in {"rich", "extreme"}
    if bias > 0:
        return OptionsStructure.BULL_CALL_SPREAD if rich else OptionsStructure.LONG_CALL
    return OptionsStructure.PUT_DEBIT_SPREAD if rich else OptionsStructure.LONG_PUT


def _reason_codes(
    features: NormalizedFeatures,
    options: OptionsLayerOutput,
    structure: OptionsStructure,
) -> tuple[str, ...]:
    codes: list[str] = []
    if structure == OptionsStructure.NO_TRADE:
        codes.append("insufficient_directional_edge")
        return tuple(codes)
    if structure in {
        OptionsStructure.CALL_DEBIT_SPREAD,
        OptionsStructure.PUT_DEBIT_SPREAD,
        OptionsStructure.BULL_CALL_SPREAD,
    }:
        codes.append("iv_rich_debit_spread")
    if structure == OptionsStructure.CALL_BUTTERFLY:
        codes.append("pinning_butterfly_regime")
    if structure in {OptionsStructure.SHORT_PUT, OptionsStructure.PUT_CREDIT_SPREAD}:
        codes.append("r2_income_structure")
    if options.dealer_regime == "pinning":
        codes.append("dealer_pinning_regime")
    if options.flow_conviction_score >= 0.5:
        codes.append("flow_conviction_support")
    if features.chain_liquidity_score < 0.3:
        codes.append("thin_chain_liquidity")
    return tuple(codes)


def _legs_to_payoff_strategy(
    inp: OptionsStrategyInput,
    legs: tuple,
) -> OptionStrategy | None:
    spot = resolve_spot_price(inp, None)
    if spot <= 0 or not legs:
        return None
    option_legs: list[OptionLeg] = []
    for leg in legs:
        if leg.mark is None or leg.mark <= 0:
            continue
        option_legs.append(
            OptionLeg(
                symbol=leg.contract_symbol or f"{leg.underlying}{leg.expiry:%y%m%d}{leg.right[0].upper()}{int(leg.strike * 1000):08d}",
                expiry=leg.expiry,
                strike=leg.strike,
                right=leg.right,
                side=leg.side,
                quantity=getattr(leg, "ratio", 1),
                entry_price=leg.mark,
                iv=leg.iv,
                delta=leg.delta,
            )
        )
    if not option_legs:
        return None
    return OptionStrategy(underlying=inp.symbol, spot=spot, legs=option_legs)


def _payoff_metrics(
    inp: OptionsStrategyInput,
    legs: tuple,
) -> tuple[float | None, float | None, tuple[float, ...], tuple[str, ...]]:
    strategy = _legs_to_payoff_strategy(inp, legs)
    if strategy is None:
        return None, None, (), ("payoff_unavailable_missing_marks",)
    spot = strategy.spot
    curve = StrategyPayoffEngine().compute_payoff(
        strategy,
        OptionPayoffScenario(
            spot_min=spot * 0.85,
            spot_max=spot * 1.15,
            steps=80,
            risk_free_rate=_DEFAULT_RATE,
        ),
    )
    return (
        curve.max_profit,
        curve.max_loss,
        tuple(curve.break_evens),
        tuple(curve.limitations),
    )


class StructureSelector:
    """Elige estructura MVP y arma ``OptionsStrategyCandidate`` (sin orden)."""

    @classmethod
    def build_candidate(
        cls,
        inp: OptionsStrategyInput,
        features: NormalizedFeatures,
        options: OptionsLayerOutput,
        *,
        config: OptionsStrategyConfigBundle | None = None,
    ) -> OptionsStrategyCandidate:
        active = config or get_options_strategy_config()
        profile = active.structure_profile
        structure = _resolve_structure(features, options, profile=profile)
        bias = _composite_bias(features)
        direction = _direction_from_bias(bias)
        confidence = clamp01(
            abs(bias) * 0.5
            + options.flow_conviction_score * 0.25
            + features.chain_liquidity_score * 0.25
        )
        selection = StructureSelection(
            symbol=inp.symbol,
            as_of=inp.as_of,
            structure=structure,
            direction=direction,
            reason_codes=_reason_codes(features, options, structure),
            confidence=confidence,
        )

        if structure == OptionsStructure.NO_TRADE:
            return OptionsStrategyCandidate(
                symbol=inp.symbol,
                as_of=inp.as_of,
                selection=selection,
                limitations=("no_trade_structure",),
            )

        r2_mode = profile == "r2_basic"
        if options.insufficient_data and not r2_mode:
            return OptionsStrategyCandidate(
                symbol=inp.symbol,
                as_of=inp.as_of,
                selection=selection,
                limitations=("options_layer_insufficient",),
            )
        if r2_mode and not chain_rows(inp):
            return OptionsStrategyCandidate(
                symbol=inp.symbol,
                as_of=inp.as_of,
                selection=selection.model_copy(
                    update={
                        "structure": OptionsStructure.NO_TRADE,
                        "reason_codes": selection.reason_codes + ("chain_unavailable",),
                    }
                ),
                limitations=("chain_unavailable",),
            )

        legs = ContractSelector.select(inp, structure, config=active)
        if not legs:
            return OptionsStrategyCandidate(
                symbol=inp.symbol,
                as_of=inp.as_of,
                selection=selection.model_copy(
                    update={
                        "structure": OptionsStructure.NO_TRADE,
                        "reason_codes": selection.reason_codes + ("contract_selection_failed",),
                    }
                ),
                limitations=("contract_selection_failed",),
            )

        max_profit, max_loss, break_evens, payoff_limits = _payoff_metrics(inp, legs)
        limitations = selection.reason_codes + payoff_limits
        return OptionsStrategyCandidate(
            symbol=inp.symbol,
            as_of=inp.as_of,
            selection=selection,
            legs=legs,
            max_profit=max_profit,
            max_loss=max_loss,
            break_evens=break_evens,
            limitations=limitations,
        )


__all__ = ["StructureSelector"]
