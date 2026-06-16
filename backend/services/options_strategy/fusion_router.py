"""Router de fusión y decisión de playbook (Fase 4). # [PD-3][TH]"""

from __future__ import annotations

import os
import statistics
from decimal import Decimal, ROUND_HALF_UP

from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.config.r1_enrichment_thresholds import (
    FUSION_HYBRID_CONF_WEIGHT,
    FUSION_L2_CONF_WEIGHT,
)
from backend.models.options_strategy import (
    NormalizedFeatures,
    OptionsExecutionPayload,
    OptionsLegSpec,
    OptionsStrategyCandidate,
    OptionsStrategyInput,
    OptionsStructure,
    PlaybookDecision,
    StrategyDecision,
)
from backend.services.options_strategy._scoring import clamp01, clamp11
from backend.services.options_strategy.contract_selector import (
    DEFAULT_DELTA_BUY,
    DEFAULT_DELTA_SELL,
)
from backend.services.options_strategy.playbook_matcher import PlaybookMatcher, direction_from_bias
from backend.services.options_strategy.sizing_engine import compute_risk_budget_pct
from backend.services.options_strategy.veto_engine import VetoEngine

_OPTION_MULTIPLIER = Decimal("100")


def fuse_features(
    features: NormalizedFeatures,
    *,
    config: OptionsStrategyConfigBundle | None = None,
) -> NormalizedFeatures:
    """Calcula ``global_bias`` y ``global_confidence`` con penalización por dispersión."""
    active = config or get_options_strategy_config()
    omni = active.omni_engine
    weights = omni.weights or {
        "technical": 0.30,
        "predictive": 0.30,
        "options": 0.40,
    }
    wt = float(weights.get("technical", 0.30))
    wp = float(weights.get("predictive", 0.30))
    wo = float(weights.get("options", 0.40))
    total = wt + wp + wo
    if total <= 0:
        total = 1.0

    global_bias = clamp11(
        (
            wt * features.technical_direction_bias
            + wp * features.predictive_direction_bias
            + wo * features.options_direction_bias
        )
        / total
    )
    biases = [
        features.technical_direction_bias,
        features.predictive_direction_bias,
        features.options_direction_bias,
    ]
    disagreement = statistics.pstdev(biases) if len(biases) > 1 else 0.0
    tech_conf = features.trend_quality_score * features.structure_alignment_score
    if features.l2_microstructure_score > 0:
        tech_conf = clamp01(
            (1.0 - FUSION_L2_CONF_WEIGHT) * tech_conf
            + FUSION_L2_CONF_WEIGHT * features.l2_microstructure_score
        )
    pred_conf = features.expected_move_confidence * (
        1.0 - features.forecast_dispersion_score
    )
    opt_conf = features.flow_conviction_score * features.chain_liquidity_score
    if features.hybrid_confluence_score > 0:
        opt_conf = clamp01(
            (1.0 - FUSION_HYBRID_CONF_WEIGHT) * opt_conf
            + FUSION_HYBRID_CONF_WEIGHT * features.hybrid_confluence_score
        )
    raw_conf = (wt * tech_conf + wp * pred_conf + wo * opt_conf) / total
    global_confidence = clamp01(raw_conf - omni.disagreement_penalty * disagreement)

    return features.model_copy(
        update={
            "global_bias": global_bias,
            "global_confidence": global_confidence,
        }
    )


def _risk_budget_pct(
    features: NormalizedFeatures,
    config: OptionsStrategyConfigBundle,
) -> float:
    return compute_risk_budget_pct(features, config.risk.max_risk_per_trade_pct)


def _max_premium_usd(candidate: OptionsStrategyCandidate) -> Decimal:
    try:
        scale = Decimal(str(os.getenv("OPTIONS_PREMIUM_SCALE_MULT", "1.5")))
    except Exception:
        scale = Decimal("1.5")
    structure = candidate.selection.structure
    credit_structures = {
        OptionsStructure.SHORT_PUT,
        OptionsStructure.PUT_CREDIT_SPREAD,
        OptionsStructure.CALL_CREDIT_SPREAD,
    }

    def _scaled(value: Decimal) -> Decimal:
        return max(value * scale, Decimal("1.00")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    if (
        candidate.max_loss is not None
        and candidate.max_loss > 0
        and structure not in credit_structures
    ):
        return _scaled(
            Decimal(str(candidate.max_loss)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
    net = Decimal("0")
    for leg in candidate.legs:
        if leg.mark is None:
            continue
        premium = Decimal(str(leg.mark)) * _OPTION_MULTIPLIER
        if leg.side == "long":
            net += premium
        else:
            net -= premium
    if net < 0:
        return _scaled(max(abs(net), Decimal("1.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if candidate.max_loss is not None and candidate.max_loss > 0:
        return _scaled(
            Decimal(str(candidate.max_loss)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
    return _scaled(max(net, Decimal("1.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _delta_sell_target(structure: OptionsStructure) -> float | None:
    if structure in {
        OptionsStructure.CALL_DEBIT_SPREAD,
        OptionsStructure.PUT_DEBIT_SPREAD,
        OptionsStructure.BULL_CALL_SPREAD,
        OptionsStructure.PUT_CREDIT_SPREAD,
        OptionsStructure.CALL_CREDIT_SPREAD,
    }:
        return DEFAULT_DELTA_SELL
    return None


def _legs_to_specs(candidate: OptionsStrategyCandidate) -> tuple[OptionsLegSpec, ...]:
    specs: list[OptionsLegSpec] = []
    for leg in candidate.legs:
        if not leg.contract_symbol:
            continue
        specs.append(
            OptionsLegSpec(
                contract_symbol=leg.contract_symbol,
                side="buy" if leg.side == "long" else "sell",
                ratio=leg.ratio,
            )
        )
    return tuple(specs)


def _dte_target(candidate: OptionsStrategyCandidate) -> int:
    if not candidate.legs:
        return 14
    return max(leg.dte for leg in candidate.legs)


class FusionRouter:
    """Fusiona capas, aplica vetos, elige playbook y arma decisión."""

    @classmethod
    def decide(
        cls,
        inp: OptionsStrategyInput,
        features: NormalizedFeatures,
        candidate: OptionsStrategyCandidate,
        *,
        config: OptionsStrategyConfigBundle | None = None,
    ) -> tuple[PlaybookDecision, OptionsExecutionPayload | None]:
        active = config or get_options_strategy_config()
        fused = fuse_features(features, config=active)
        veto = VetoEngine.evaluate(fused, inp, config=active)
        reason_codes: list[str] = list(candidate.selection.reason_codes)

        if veto.triggered:
            return (
                PlaybookDecision(
                    symbol=inp.symbol,
                    as_of=inp.as_of,
                    decision=StrategyDecision.NO_TRADE,
                    recommended_structure=OptionsStructure.NO_TRADE,
                    direction=direction_from_bias(fused.global_bias),
                    confidence=fused.global_confidence,
                    reason_codes=tuple(reason_codes + list(veto.reason_codes)),
                    veto_triggered=veto.veto_code,
                ),
                None,
            )

        if fused.global_confidence < active.omni_engine.min_global_confidence:
            return (
                PlaybookDecision(
                    symbol=inp.symbol,
                    as_of=inp.as_of,
                    decision=StrategyDecision.NO_TRADE,
                    recommended_structure=OptionsStructure.NO_TRADE,
                    direction=direction_from_bias(fused.global_bias),
                    confidence=fused.global_confidence,
                    reason_codes=tuple(reason_codes + ["insufficient_global_confidence"]),
                ),
                None,
            )

        match = PlaybookMatcher.match(inp, fused, candidate, config=active)
        reason_codes.extend(match.reason_codes)

        structure = candidate.selection.structure
        if structure == OptionsStructure.NO_TRADE or not match.playbook_family:
            return (
                PlaybookDecision(
                    symbol=inp.symbol,
                    as_of=inp.as_of,
                    decision=StrategyDecision.NO_TRADE,
                    playbook_family=match.playbook_family,
                    recommended_structure=OptionsStructure.NO_TRADE,
                    direction=direction_from_bias(fused.global_bias),
                    confidence=fused.global_confidence,
                    reason_codes=tuple(reason_codes),
                ),
                None,
            )

        if not candidate.legs:
            return (
                PlaybookDecision(
                    symbol=inp.symbol,
                    as_of=inp.as_of,
                    decision=StrategyDecision.NO_TRADE,
                    playbook_family=match.playbook_family,
                    recommended_structure=structure,
                    direction=candidate.selection.direction,
                    confidence=fused.global_confidence,
                    reason_codes=tuple(reason_codes + ["missing_contract_legs"]),
                ),
                None,
            )

        risk_pct = _risk_budget_pct(fused, active)
        decision = PlaybookDecision(
            symbol=inp.symbol,
            as_of=inp.as_of,
            decision=StrategyDecision.EXECUTE,
            playbook_family=match.playbook_family,
            recommended_structure=structure,
            direction=candidate.selection.direction,
            confidence=fused.global_confidence,
            reason_codes=tuple(reason_codes),
            execution_ready=True,
            risk_budget_pct=risk_pct,
            candidate_contract_policy={
                "delta_buy_target": DEFAULT_DELTA_BUY,
                "delta_sell_target": DEFAULT_DELTA_SELL,
                "dte_target": _dte_target(candidate),
            },
        )
        payload = OptionsExecutionPayload(
            symbol=inp.symbol,
            timestamp=inp.as_of,
            decision=StrategyDecision.EXECUTE,
            playbook_family=match.playbook_family,
            recommended_structure=structure,
            direction=candidate.selection.direction,
            global_confidence=fused.global_confidence,
            dte_target=_dte_target(candidate),
            delta_buy_target=DEFAULT_DELTA_BUY,
            delta_sell_target=_delta_sell_target(structure),
            max_premium_usd=_max_premium_usd(candidate),
            risk_budget_pct=risk_pct,
            reason_codes=decision.reason_codes,
            legs=_legs_to_specs(candidate),
            dry_run=True,
            route=inp.route,
            audit_metadata={
                "global_bias": fused.global_bias,
                "playbook_score": match.score,
            },
        )
        return decision, payload


__all__ = ["FusionRouter", "fuse_features"]
