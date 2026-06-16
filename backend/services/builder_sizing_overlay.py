"""Builder sizing overlay: percent risk budget to futures contracts via tick value."""

from __future__ import annotations

import math
from decimal import Decimal

from backend.config.builder_contracts_loader import resolve_builder_contract
from backend.config.funding_thresholds import FundingThresholds
from backend.domain.builder_models import (
    BuilderAccountState,
    BuilderPhase,
    BuilderProfile,
    BuilderRuleEvaluation,
    BuilderSizingDecision,
)
from backend.services.builder_rule_engine import BuilderRuleEngine
from backend.services.builder_state_machine import effective_equity

_SIM_PHASES: frozenset[BuilderPhase] = frozenset(
    {
        "SIM_ACTIVE",
        "SIM_BUFFER_BUILDING",
        "SIM_PAYOUT_ELIGIBLE",
    }
)


class BuilderSizingOverlay:
    """Translate Builder risk budget into integer contracts (futures or options)."""

    def __init__(
        self,
        thresholds: FundingThresholds | None = None,
        rule_engine: BuilderRuleEngine | None = None,
    ) -> None:
        self._thresholds = thresholds or FundingThresholds()
        self._rule_engine = rule_engine or BuilderRuleEngine(self._thresholds)

    def compute_contracts(
        self,
        state: BuilderAccountState,
        profile: BuilderProfile,
        *,
        symbol: str,
        stop_ticks: int = 0,
        rule_eval: BuilderRuleEvaluation | None = None,
        f_signal: Decimal = Decimal("1.0"),
        f_regime: Decimal = Decimal("1.0"),
        consistency_ratio_live: Decimal = Decimal("0"),
        open_mini_contracts: int = 0,
        open_micro_contracts: int = 0,
        prefer_micro: bool = False,
        asset_type: str = "future",
        premium_per_contract: Decimal = Decimal("0"),
        bid_ask_spread_pct: Decimal = Decimal("0"),
        margin_required_per_contract: Decimal = Decimal("0"),
        available_buying_power: Decimal = Decimal("0"),
        max_bid_ask_spread_pct: Decimal = Decimal("0"),
    ) -> BuilderSizingDecision:
        """Compute allowed contracts for a Builder trade candidate."""
        if asset_type == "option":
            rules = rule_eval or self._rule_engine.evaluate(
                state,
                profile,
                contract_is_micro=False,
                open_mini_contracts=open_mini_contracts,
                open_micro_contracts=open_micro_contracts,
            )
        else:
            contract = resolve_builder_contract(symbol, prefer_micro=prefer_micro)
            rules = rule_eval or self._rule_engine.evaluate(
                state,
                profile,
                contract_is_micro=contract.is_micro,
                open_mini_contracts=open_mini_contracts,
                open_micro_contracts=open_micro_contracts,
            )

        factors = _builder_factors(
            state=state,
            profile=profile,
            rules=rules,
            consistency_ratio_live=consistency_ratio_live,
            thresholds=self._thresholds,
        )

        if asset_type == "option":
            if rules.blocks_new_entries:
                return BuilderSizingDecision(
                    contracts=0,
                    contract_symbol=symbol,
                    capped_by="blocked",
                    builder_factors=factors,
                    asset_type="option",
                )
        else:
            if stop_ticks <= 0 or rules.blocks_new_entries:
                return _zero_decision(contract.symbol, stop_ticks, capped_by="blocked", factors=factors)

        equity = effective_equity(state)
        if equity <= Decimal("0"):
            if asset_type == "option":
                return BuilderSizingDecision(
                    contracts=0,
                    contract_symbol=symbol,
                    capped_by="invalid_equity",
                    builder_factors=factors,
                    asset_type="option",
                )
            else:
                return _zero_decision(contract.symbol, stop_ticks, capped_by="invalid_equity", factors=factors)

        base_pct = profile.base_risk_per_trade_pct * f_signal * f_regime
        builder_multiplier = (
            factors["drawdown"]
            * factors["daily_buffer"]
            * factors["payout_consistency"]
            * factors["phase"]
        )
        budget_pct = base_pct * builder_multiplier
        remaining_daily_pct = _pct_of_equity(rules.remaining_daily_risk, equity)
        remaining_trailing_pct = _pct_of_equity(rules.distance_to_trailing_dd, equity)

        pct_caps = {
            "builder_budget": budget_pct,
            "remaining_daily": remaining_daily_pct,
            "remaining_trailing": remaining_trailing_pct,
        }
        allowed_risk_pct = min(pct_caps.values())
        pct_capped_by = min(pct_caps, key=pct_caps.__getitem__)

        budget_usd = equity * allowed_risk_pct / Decimal("100")
        usd_caps = {
            "builder_budget": budget_usd,
            "remaining_daily": rules.remaining_daily_risk,
            "remaining_cycle": rules.remaining_cycle_risk,
        }
        risk_usd = min(usd_caps.values())
        usd_capped_by = min(usd_caps, key=usd_caps.__getitem__)

        if asset_type == "option":
            from backend.services.structured_options_sizer import StructuredOptionsSizer

            decision = StructuredOptionsSizer.compute(
                symbol=symbol,
                premium_per_contract=premium_per_contract,
                bid_ask_spread_pct=bid_ask_spread_pct,
                margin_required_per_contract=margin_required_per_contract,
                available_buying_power=available_buying_power,
                max_bid_ask_spread_pct=max_bid_ask_spread_pct,
                risk_usd=risk_usd,
                allowed_risk_pct=allowed_risk_pct,
                rules=rules,
                factors=factors,
            )

            # Adjust capped_by if it was tighter in overlay limits
            if decision.capped_by == "builder_budget":
                if usd_capped_by != "builder_budget":
                    decision = decision.model_copy(update={"capped_by": usd_capped_by})
                elif pct_capped_by != "builder_budget":
                    decision = decision.model_copy(update={"capped_by": pct_capped_by})

            return decision
        else:
            from backend.services.linear_instrument_sizer import LinearInstrumentSizer

            decision = LinearInstrumentSizer.compute(
                symbol=contract.symbol,
                stop_ticks=stop_ticks,
                tick_value=contract.tick_value,
                risk_usd=risk_usd,
                allowed_risk_pct=allowed_risk_pct,
                rules=rules,
                factors=factors,
            )

            # Adjust capped_by if it was tighter in overlay limits
            if decision.capped_by == "builder_budget":
                if usd_capped_by != "builder_budget":
                    decision = decision.model_copy(update={"capped_by": usd_capped_by})
                elif pct_capped_by != "builder_budget":
                    decision = decision.model_copy(update={"capped_by": pct_capped_by})

            return decision


def _builder_factors(
    *,
    state: BuilderAccountState,
    profile: BuilderProfile,
    rules: BuilderRuleEvaluation,
    consistency_ratio_live: Decimal,
    thresholds: FundingThresholds,
) -> dict[str, Decimal]:
    drawdown = _factor_from_distance(rules.distance_to_trailing_dd, profile.max_loss)
    daily_buffer = _factor_from_distance(
        rules.distance_to_dll_soft_pause,
        profile.daily_loss_limit,
    )
    payout_consistency = _consistency_factor(
        consistency_ratio_live,
        profile.consistency_cap,
        thresholds.builder_consistency_penalty_factor,
    )
    phase = _phase_factor(state.phase, thresholds)
    return {
        "drawdown": drawdown,
        "daily_buffer": daily_buffer,
        "payout_consistency": payout_consistency,
        "phase": phase,
    }


def _factor_from_distance(distance: Decimal, limit: Decimal) -> Decimal:
    if limit <= Decimal("0"):
        return Decimal("0")
    ratio = distance / limit
    return max(Decimal("0"), min(Decimal("1"), ratio))


def _consistency_factor(
    ratio_live: Decimal,
    consistency_cap: Decimal,
    penalty: Decimal,
) -> Decimal:
    if ratio_live <= Decimal("0"):
        return Decimal("1")
    warning = consistency_cap * Decimal("0.70")
    if ratio_live >= consistency_cap:
        return penalty
    if ratio_live >= warning:
        return Decimal("0.75")
    return Decimal("1")


def _phase_factor(phase: BuilderPhase, thresholds: FundingThresholds) -> Decimal:
    if phase == "EVAL_ACTIVE":
        return thresholds.builder_phase_factor_eval
    if phase in _SIM_PHASES:
        return thresholds.builder_phase_factor_sim
    if phase == "LIVE_ACTIVE":
        return thresholds.builder_phase_factor_live
    return Decimal("0")


def _pct_of_equity(amount: Decimal, equity: Decimal) -> Decimal:
    if equity <= Decimal("0"):
        return Decimal("0")
    return amount / equity * Decimal("100")


def _zero_decision(symbol: str, stop_ticks: int, *, capped_by: str, factors: dict[str, Decimal] | None = None) -> BuilderSizingDecision:
    return BuilderSizingDecision(
        contracts=0,
        contract_symbol=symbol,
        stop_ticks=max(stop_ticks, 0),
        capped_by=capped_by,
        builder_factors=factors or {},
    )
