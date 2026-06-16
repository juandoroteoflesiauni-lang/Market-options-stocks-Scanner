"""Motor de vetos jerárquicos del módulo Options Strategy. # [PD-3][TH]"""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict

from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.models.options_strategy import NormalizedFeatures, OptionsStrategyInput

def _relaxed() -> bool:
    return os.getenv("OPTIONS_STRATEGY_RELAXED_VETOS", "").lower() in {"1", "true", "yes", "on"}


def _tail_threshold() -> float:
    if _relaxed():
        return float(os.getenv("OPTIONS_TAIL_RISK_THRESHOLD", "0.92"))
    return 0.85


def _flow_conviction_floor() -> float:
    if _relaxed():
        return float(os.getenv("OPTIONS_FLOW_TOXIC_CONVICTION", "0.10"))
    return 0.20


def _flow_dispersion_ceiling() -> float:
    if _relaxed():
        return float(os.getenv("OPTIONS_FLOW_TOXIC_DISPERSION", "0.82"))
    return 0.70


def _gamma_pressure_ceiling() -> float:
    if _relaxed():
        return float(os.getenv("OPTIONS_GAMMA_PRESSURE_VETO", "0.88"))
    return 0.75


_TAIL_RISK_THRESHOLD = 0.85  # legacy default; use _tail_threshold() at runtime
_FLOW_TOXIC_DISPERSION = 0.70
_FLOW_TOXIC_CONVICTION = 0.20


class VetoResult(BaseModel):
    """Resultado de evaluación de vetos."""

    model_config = ConfigDict(frozen=True)

    triggered: bool = False
    veto_code: str | None = None
    reason_codes: tuple[str, ...] = ()


def _tail_risk_veto(features: NormalizedFeatures) -> VetoResult | None:
    tail = max(features.left_tail_risk_score, features.right_tail_risk_score)
    threshold = _tail_threshold()
    if tail < threshold:
        return None
    if features.global_bias > 0 and features.left_tail_risk_score >= threshold:
        return VetoResult(
            triggered=True,
            veto_code="tail_risk_critical",
            reason_codes=("tail_risk_critical_veto",),
        )
    if features.global_bias < 0 and features.right_tail_risk_score >= threshold:
        return VetoResult(
            triggered=True,
            veto_code="tail_risk_critical",
            reason_codes=("tail_risk_critical_veto",),
        )
    if abs(features.global_bias) < 0.12 and tail >= threshold:
        return VetoResult(
            triggered=True,
            veto_code="tail_risk_critical",
            reason_codes=("tail_risk_critical_veto",),
        )
    return None


def _flow_toxic_veto(features: NormalizedFeatures) -> VetoResult | None:
    if (
        features.flow_conviction_score < _flow_conviction_floor()
        and features.forecast_dispersion_score > _flow_dispersion_ceiling()
    ):
        return VetoResult(
            triggered=True,
            veto_code="options_flow_toxic",
            reason_codes=("options_flow_toxic_veto",),
        )
    if features.dealer_regime == "unstable" and features.gamma_pressure_score >= _gamma_pressure_ceiling():
        return VetoResult(
            triggered=True,
            veto_code="options_flow_toxic",
            reason_codes=("options_flow_toxic_veto", "dealer_unstable_gamma"),
        )
    return None


def _liquidity_veto(
    features: NormalizedFeatures,
    config: OptionsStrategyConfigBundle,
) -> VetoResult | None:
    floor = config.risk.min_chain_liquidity_score
    if _relaxed():
        floor = min(
            floor,
            float(os.getenv("OPTIONS_RELAXED_MIN_CHAIN_LIQUIDITY", "0.22")),
        )
    if features.chain_liquidity_score >= floor:
        return None
    return VetoResult(
        triggered=True,
        veto_code="chain_liquidity_poor",
        reason_codes=("chain_liquidity_poor_veto",),
    )


def _event_blackout_veto(features: NormalizedFeatures) -> VetoResult | None:
    if features.regime_class in {"event", "dislocated"}:
        return VetoResult(
            triggered=True,
            veto_code="event_blackout",
            reason_codes=("event_blackout_veto",),
        )
    return None


class VetoEngine:
    """Evalúa vetos configurados en ``omni_engine.veto_rules``."""

    _HANDLERS: dict[str, str] = {
        "tail_risk_critical": "_tail",
        "options_flow_toxic": "_flow",
        "chain_liquidity_poor": "_liquidity",
        "event_blackout": "_event",
        "symbol_not_in_route1_universe": "_symbol",
    }

    @classmethod
    def evaluate(
        cls,
        features: NormalizedFeatures,
        inp: OptionsStrategyInput,
        *,
        config: OptionsStrategyConfigBundle | None = None,
    ) -> VetoResult:
        active = config or get_options_strategy_config()
        for rule in active.omni_engine.veto_rules:
            result = cls._apply_rule(rule, features, inp, active)
            if result is not None and result.triggered:
                return result
        return VetoResult()

    @classmethod
    def _apply_rule(
        cls,
        rule: str,
        features: NormalizedFeatures,
        inp: OptionsStrategyInput,
        config: OptionsStrategyConfigBundle,
    ) -> VetoResult | None:
        if rule == "tail_risk_critical":
            return _tail_risk_veto(features)
        if rule == "options_flow_toxic":
            return _flow_toxic_veto(features)
        if rule == "chain_liquidity_poor":
            return _liquidity_veto(features, config)
        if rule == "event_blackout":
            return _event_blackout_veto(features)
        if rule == "symbol_not_in_route1_universe":
            return cls._symbol_veto(inp, config)
        return None

    @staticmethod
    def _symbol_veto(
        inp: OptionsStrategyInput,
        config: OptionsStrategyConfigBundle,
    ) -> VetoResult | None:
        sym = inp.symbol.upper()
        if sym in config.resolved_symbols:
            return None
        return VetoResult(
            triggered=True,
            veto_code="symbol_not_in_route1_universe",
            reason_codes=("symbol_not_in_route1_universe",),
        )


__all__ = ["VetoEngine", "VetoResult"]
