"""Motor de riesgo pre-ejecución del módulo Options Strategy. # [PD-3][TH]"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.models.options_strategy import (
    NormalizedFeatures,
    OptionsExecutionPayload,
    PlaybookDecision,
    RiskEvaluation,
    RiskSessionState,
    StrategyDecision,
)
from backend.services.options_strategy._scoring import clamp01
from backend.services.options_strategy.portfolio_heat import (
    portfolio_heat_allowed,
    sector_correlation_size_mult,
    sector_heat_allowed,
    symbol_sector,
)
from backend.services.options_strategy.sizing_engine import (
    dispersion_size_multiplier,
    volatility_regime_scalar,
    vix_proxy_from_features,
)

_CONSECUTIVE_LOSS_LIMIT = 3


def _direction_exposure(
    session: RiskSessionState,
    direction: str,
) -> float:
    if direction == "bullish":
        return session.bullish_exposure_pct
    if direction == "bearish":
        return session.bearish_exposure_pct
    return 0.0


class RiskEngine:
    """Valida límites de cartera y ajusta sizing antes de ejecución."""

    @classmethod
    def evaluate_entry(
        cls,
        decision: PlaybookDecision,
        payload: OptionsExecutionPayload | None,
        features: NormalizedFeatures,
        *,
        session: RiskSessionState | None = None,
        config: OptionsStrategyConfigBundle | None = None,
    ) -> RiskEvaluation:
        active = config or get_options_strategy_config()
        state = session or RiskSessionState()
        risk = active.risk

        if decision.decision != StrategyDecision.EXECUTE or payload is None:
            return RiskEvaluation(
                passed=True,
                adjusted_risk_budget_pct=decision.risk_budget_pct,
            )

        if state.open_positions >= risk.max_open_positions:
            return RiskEvaluation(
                passed=False,
                veto_code="max_open_positions",
                reason_codes=("portfolio_max_open_positions",),
            )

        if state.daily_loss_pct >= risk.max_daily_loss_pct:
            return RiskEvaluation(
                passed=False,
                veto_code="daily_loss_limit",
                reason_codes=("portfolio_daily_loss_limit",),
            )

        exposure = _direction_exposure(state, decision.direction)
        if exposure >= risk.max_same_direction_exposure_pct:
            return RiskEvaluation(
                passed=False,
                veto_code="direction_exposure_limit",
                reason_codes=("portfolio_direction_exposure_limit",),
            )

        playbook = decision.playbook_family or ""
        losses = state.consecutive_losses_by_playbook.get(playbook, 0)
        if losses >= _CONSECUTIVE_LOSS_LIMIT:
            return RiskEvaluation(
                passed=False,
                veto_code="playbook_loss_streak",
                reason_codes=("playbook_consecutive_loss_cooldown",),
            )

        sector = symbol_sector(decision.symbol)
        if not portfolio_heat_allowed(state.total_risk_budget_pct, decision.risk_budget_pct):
            return RiskEvaluation(
                passed=False,
                veto_code="portfolio_heat_limit",
                reason_codes=("portfolio_heat_limit",),
            )
        if not sector_heat_allowed(sector, state.sector_risk_budget_pct, decision.risk_budget_pct):
            return RiskEvaluation(
                passed=False,
                veto_code="sector_heat_limit",
                reason_codes=("sector_heat_limit",),
            )

        disp_mult = dispersion_size_multiplier(features)
        regime_mult = volatility_regime_scalar(vix_proxy_from_features(features))
        corr_mult = sector_correlation_size_mult(decision.symbol, state.open_symbols)
        size_mult = clamp01(disp_mult * regime_mult * corr_mult)
        adjusted_budget = decision.risk_budget_pct * size_mult
        codes: list[str] = []
        if disp_mult < 1.0:
            codes.append("dispersion_size_reduction")
        if regime_mult < 1.0:
            codes.append("volatility_regime_reduction")
        if corr_mult < 1.0:
            codes.append("correlation_size_reduction")

        premium_cap = payload.max_premium_usd * Decimal(str(size_mult))
        premium_cap = premium_cap.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if premium_cap < Decimal("1.00"):
            return RiskEvaluation(
                passed=False,
                veto_code="premium_too_small_after_sizing",
                reason_codes=tuple(codes + ["premium_below_minimum"]),
            )

        return RiskEvaluation(
            passed=True,
            size_multiplier=size_mult,
            adjusted_risk_budget_pct=adjusted_budget,
            reason_codes=tuple(codes),
        )

    @classmethod
    def apply_to_payload(
        cls,
        payload: OptionsExecutionPayload,
        evaluation: RiskEvaluation,
    ) -> OptionsExecutionPayload:
        """Aplica multiplicador de sizing al payload de ejecución."""
        if not evaluation.passed:
            return payload
        scaled_premium = (
            payload.max_premium_usd * Decimal(str(evaluation.size_multiplier))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return payload.model_copy(
            update={
                "max_premium_usd": max(scaled_premium, Decimal("1.00")),
                "risk_budget_pct": evaluation.adjusted_risk_budget_pct,
                "audit_metadata": {
                    **payload.audit_metadata,
                    "size_multiplier": evaluation.size_multiplier,
                },
            }
        )


__all__ = ["RiskEngine"]
