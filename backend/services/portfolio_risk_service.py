from __future__ import annotations
from typing import Any
"""Binding portfolio risk service for prop-firm style evaluations."""


import math
from dataclasses import dataclass

from backend.config.logger_setup import get_logger
from backend.domain.portfolio_risk_models import (
    AccountStatus,
    AllowedRiskBudget,
    CandidateGateDecision,
    ChallengeSimulationResult,
    ConsistencyMetrics,
    FundingRulePreset,
    FundingSurvivalSummary,
    PortfolioMetrics,
    PortfolioRiskRequest,
    PortfolioRiskResponse,
    RuleUsage,
    TradeCandidate,
)
from backend.services.portfolio_risk.component import (
    fractional_kelly,
    historical_var_pct,
    position_notional_from_risk,
    stress_loss_pct,
)

logger = get_logger(__name__)

# Phase D — stable reason codes shared with scanner_funding_gate and UI.
KILL_SWITCH_MAX_LOSS_BREACH = "max_loss_breached"
KILL_SWITCH_DAILY_LOSS_BREACH = "daily_loss_breached"
KILL_SWITCH_DAILY_LOSS_USAGE_HIGH = "daily_loss_usage_high"
KILL_SWITCH_CONSISTENCY_CAP = "consistency_cap_violated"
KILL_SWITCH_NO_ATTEMPTS_LEFT = "no_attempts_remaining_today"
KILL_SWITCH_OVERFIT_MODULE = "overfit_module_active"
KILL_SWITCH_LIGHT_PROXY_ONLY = "light_proxy_only"
KILL_SWITCH_INSUFFICIENT_DATA = "insufficient_backtest_evidence"
KILL_SWITCH_DRAWDOWN_HIGH = "trailing_drawdown_usage_high"

_PRESET_DEFS: dict[str, FundingRulePreset] = {
    "ftmo_2_step": FundingRulePreset(
        id="ftmo_2_step",
        name="FTMO 2-Step",
        drawdown_type="static",
        daily_loss_pct=5.0,
        max_loss_pct=10.0,
        profit_target_pct=10.0,
        verification_profit_target_pct=5.0,
        consistency_cap=0.50,
        consistency_warning=0.35,
        min_trading_days=4,
        risk_per_trade_pct=0.50,
        max_position_exposure_pct=20.0,
        daily_loss_rule="equity",
        lockout_on_daily_breach=True,
    ),
    "ftmo_1_step": FundingRulePreset(
        id="ftmo_1_step",
        name="FTMO 1-Step",
        drawdown_type="trailing_eod",
        daily_loss_pct=3.0,
        max_loss_pct=10.0,
        profit_target_pct=10.0,
        consistency_cap=0.50,
        consistency_warning=0.35,
        min_trading_days=4,
        risk_per_trade_pct=0.40,
        max_position_exposure_pct=18.0,
        daily_loss_rule="equity",
        lockout_on_daily_breach=True,
    ),
    "topstep_combine": FundingRulePreset(
        id="topstep_combine",
        name="Topstep Trading Combine",
        drawdown_type="trailing_intraday",
        daily_loss_pct=2.0,
        max_loss_amount=2_000.0,
        profit_target_pct=6.0,
        consistency_cap=0.50,
        consistency_warning=0.35,
        min_trading_days=3,
        risk_per_trade_pct=0.35,
        max_position_exposure_pct=10.0,
        max_contracts=5,
        daily_loss_rule="equity",
        lockout_on_daily_breach=True,
    ),
    "custom": FundingRulePreset(
        id="custom",
        name="Custom",
        drawdown_type="static",
        daily_loss_pct=5.0,
        max_loss_pct=10.0,
        consistency_cap=0.50,
        consistency_warning=0.35,
        min_trading_days=0,
        risk_per_trade_pct=0.50,
        max_position_exposure_pct=20.0,
        daily_loss_rule="equity",
        lockout_on_daily_breach=True,
    ),
}


@dataclass(frozen=True)
class _RuleState:
    status: AccountStatus
    usage: dict[str, RuleUsage]
    breach_warnings: list[str]
    intraday_equity: float
    binding_equity: float


class PortfolioRiskService:
    """Evaluate funding-rule survival before any trade sizing."""

    def presets(self) -> list[FundingRulePreset]:
        return list(_PRESET_DEFS.values())

    def preset_map(self) -> dict[str, FundingRulePreset]:
        return dict(_PRESET_DEFS)

    def resolve_preset(
        self, incoming: FundingRulePreset, account_initial: float
    ) -> FundingRulePreset:
        base = _PRESET_DEFS.get(incoming.id, _PRESET_DEFS["custom"])
        merged = base.model_copy(update=incoming.model_dump(exclude_unset=True))
        if merged.initial_capital is None:
            merged = merged.model_copy(update={"initial_capital": account_initial})
        if merged.id == "topstep_combine" and incoming.max_loss_amount is None:
            merged = merged.model_copy(
                update={"max_loss_amount": _topstep_mll_amount(account_initial)}
            )
        return merged

    def evaluate(self, request: PortfolioRiskRequest) -> PortfolioRiskResponse:
        preset = self.resolve_preset(request.preset, request.account_state.initial_capital)
        rules = self._evaluate_rules(request, preset)
        consistency = self._consistency(request, preset)
        budget = self._risk_budget(request, preset, rules)
        portfolio = self._portfolio_metrics(request)
        decisions = [
            self._gate_candidate(candidate, request, preset, rules, consistency, budget)
            for candidate in request.candidates
        ]
        survival = self._funding_survival(
            rules=rules,
            consistency=consistency,
            budget=budget,
            preset=preset,
            decisions=decisions,
            candidates=request.candidates,
        )
        action_plan = self._action_plan(rules, consistency, budget, decisions, survival)
        simulation = self._compute_challenge_simulation(request)
        return PortfolioRiskResponse(
            account_status=rules.status,
            preset=preset,
            rule_usage=rules.usage,
            breach_warnings=rules.breach_warnings,
            allowed_risk_budget=budget,
            candidate_decisions=decisions,
            portfolio_metrics=portfolio,
            consistency_metrics=consistency,
            action_plan=action_plan,
            data_quality={
                "portfolio": "live_request" if request.positions else "insufficient_data",
                "candidates": "live_request" if request.candidates else "insufficient_data",
                "persistence": "stateless_v1",
            },
            funding_survival=survival,
            challenge_simulation=simulation,
        )

    def _evaluate_rules(
        self, request: PortfolioRiskRequest, preset: FundingRulePreset
    ) -> _RuleState:
        account = request.account_state
        initial = float(preset.initial_capital or account.initial_capital)
        intraday_equity = (
            float(account.start_of_day_balance)
            + float(request.realized_daily_pnl)
            + float(request.unrealized_pnl)
        )
        binding_equity = min(float(account.current_equity), intraday_equity)

        daily_amount = _amount_from_pct_or_value(
            initial,
            pct=preset.daily_loss_pct,
            amount=preset.daily_loss_amount,
            fallback_pct=5.0,
        )
        # Compute daily_used according to the preset's daily_loss_rule.
        if preset.daily_loss_rule == "balance":
            daily_used = max(0.0, -float(request.realized_daily_pnl))
        elif preset.daily_loss_rule == "hybrid":
            daily_used = max(
                0.0,
                -(float(request.realized_daily_pnl) + 0.5 * float(request.unrealized_pnl)),
            )
        else:  # "equity" (default)
            daily_used = max(0.0, float(account.start_of_day_balance) - intraday_equity)
        daily_usage = _usage(daily_amount, daily_used)
        daily_rule = RuleUsage(
            limit_amount=round(daily_amount, 2),
            limit_equity=round(float(account.start_of_day_balance) - daily_amount, 2),
            used_amount=round(daily_used, 2),
            remaining_amount=round(max(0.0, daily_amount - daily_used), 2),
            usage_pct=round(daily_usage * 100.0, 2),
            breached=daily_used >= daily_amount,
        )

        max_amount = _amount_from_pct_or_value(
            initial,
            pct=preset.max_loss_pct,
            amount=preset.max_loss_amount,
            fallback_pct=10.0,
        )
        max_limit = self._max_loss_limit(
            account.high_watermark_balance, initial, max_amount, preset
        )
        max_used = (
            max(0.0, initial - binding_equity)
            if preset.drawdown_type == "static"
            else max(0.0, max_limit + max_amount - binding_equity)
        )
        max_usage = _usage(max_amount, max_used)
        max_rule = RuleUsage(
            limit_amount=round(max_amount, 2),
            limit_equity=round(max_limit, 2),
            used_amount=round(max_used, 2),
            remaining_amount=round(max(0.0, binding_equity - max_limit), 2),
            usage_pct=round(max_usage * 100.0, 2),
            breached=binding_equity <= max_limit,
        )

        warnings: list[str] = []
        if daily_rule.breached:
            warnings.append("Maximum daily loss breached; trading must stop.")
        if max_rule.breached:
            warnings.append("Maximum loss breached; account is no longer eligible.")

        status: AccountStatus = "ACTIVE"
        if max_rule.breached:
            # Max loss breach always → BREACHED.
            status = "BREACHED"
        elif daily_rule.breached:
            # Daily breach → LOCKED when lockout_on_daily_breach=True, else BREACHED.
            status = "LOCKED" if preset.lockout_on_daily_breach else "BREACHED"
        elif daily_usage >= preset.daily_lock_threshold:
            status = "LOCKED"
        elif daily_usage >= 0.65 or max_usage >= 0.65:
            status = "AT_RISK"

        return _RuleState(
            status=status,
            usage={"daily_loss": daily_rule, "max_loss": max_rule, "overall_drawdown": max_rule},
            breach_warnings=warnings,
            intraday_equity=intraday_equity,
            binding_equity=binding_equity,
        )

    def _max_loss_limit(
        self,
        high_watermark: float | None,
        initial: float,
        max_amount: float,
        preset: FundingRulePreset,
    ) -> float:
        if preset.drawdown_type in {"trailing_eod", "trailing_intraday"}:
            # For trailing_intraday, high_watermark_balance should be the intraday peak equity
            # as tracked by the client. Same calculation as trailing_eod.
            hwm = max(initial, float(high_watermark or initial))
            trailing_limit = hwm - max_amount
            return max(initial - max_amount, min(initial, trailing_limit))
        return initial - max_amount

    def _risk_budget(
        self,
        request: PortfolioRiskRequest,
        preset: FundingRulePreset,
        rules: _RuleState,
    ) -> AllowedRiskBudget:
        initial = float(preset.initial_capital or request.account_state.initial_capital)
        daily_rule = rules.usage["daily_loss"]
        daily_lock_amount = daily_rule.limit_amount * preset.daily_lock_threshold
        lock_remaining = max(0.0, daily_lock_amount - daily_rule.used_amount)
        per_trade_pct = min(float(preset.risk_per_trade_pct), lock_remaining / initial * 100.0)
        per_trade_pct = max(0.0, per_trade_pct)
        return AllowedRiskBudget(
            daily_remaining_amount=round(daily_rule.remaining_amount, 2),
            daily_lock_remaining_amount=round(lock_remaining, 2),
            per_trade_pct=round(per_trade_pct, 4),
            per_trade_amount=round(initial * per_trade_pct / 100.0, 2),
            max_position_notional=round(initial * preset.max_position_exposure_pct / 100.0, 2),
            max_attempts_remaining=int(lock_remaining // max(initial * per_trade_pct / 100.0, 1.0)),
        )

    def _consistency(
        self,
        request: PortfolioRiskRequest,
        preset: FundingRulePreset,
    ) -> ConsistencyMetrics:
        positives = [max(0.0, float(item.pnl)) for item in request.trade_history]
        total_profit = sum(positives)
        best_day = max(positives) if positives else 0.0
        ratio = best_day / total_profit if total_profit > 0 else 0.0
        if not positives:
            status = "insufficient_data"
        elif ratio > preset.consistency_cap:
            status = "blocked"
        elif ratio > preset.consistency_warning:
            status = "warning"
        else:
            status = "ok"
        return ConsistencyMetrics(
            total_profit=round(total_profit, 2),
            best_day_profit=round(best_day, 2),
            best_day_ratio=round(ratio, 4),
            trading_days=len(request.trade_history),
            status=status,
        )

    def _portfolio_metrics(self, request: PortfolioRiskRequest) -> PortfolioMetrics:
        notionals: dict[str, float] = {}
        long_exposure = 0.0
        short_exposure = 0.0
        for pos in request.positions:
            notional = abs(float(pos.quantity) * float(pos.mark_price))
            notionals[pos.symbol] = notionals.get(pos.symbol, 0.0) + notional
            if pos.side == "LONG":
                long_exposure += notional
            else:
                short_exposure += notional
        gross = long_exposure + short_exposure
        weights = [value / gross for value in notionals.values()] if gross > 0 else []
        hhi = sum(w * w for w in weights)
        largest = max(weights) * 100.0 if weights else 0.0
        var95 = historical_var_pct(request.returns_pct, alpha=0.05) if request.returns_pct else None
        stress: dict[str, float] = {}
        if request.returns_pct and len(request.returns_pct) > 2:
            mean = sum(request.returns_pct) / len(request.returns_pct)
            variance = sum((x - mean) ** 2 for x in request.returns_pct) / len(request.returns_pct)
            stress = stress_loss_pct(mean, math.sqrt(max(variance, 0.0)))
        return PortfolioMetrics(
            positions_count=len(request.positions),
            gross_exposure=round(gross, 2),
            net_exposure=round(long_exposure - short_exposure, 2),
            long_exposure=round(long_exposure, 2),
            short_exposure=round(short_exposure, 2),
            concentration_hhi=round(hhi, 4),
            largest_symbol_weight_pct=round(largest, 2),
            hist_var_95_pct=round(var95, 4) if var95 is not None else None,
            stress=stress,
        )

    def _gate_candidate(
        self,
        candidate: TradeCandidate,
        request: PortfolioRiskRequest,
        preset: FundingRulePreset,
        rules: _RuleState,
        consistency: ConsistencyMetrics,
        budget: AllowedRiskBudget,
    ) -> CandidateGateDecision:
        """Gate a trade candidate through the 4-tier deterministic size ladder.

        Tier 4 (BLOCK, 0.0x) → Tier 3 (micro, 0.25x) → Tier 2 (reduced, 0.50x) → Tier 1 (normal, 1.0x).
        Highest restriction wins: tiers are evaluated from most to least severe and the
        first match sets the floor.  All existing signal/backtest penalties are then
        multiplied in and scanner_recommended_size_multiplier is applied as a final cap.
        """
        reasons: list[str] = []
        warnings: list[str] = []
        stop_pct = _stop_distance_pct(candidate)
        max_loss_at_stop = budget.max_position_notional * stop_pct / 100.0

        # Per-candidate remaining risk percentages (for UI and per-candidate display).
        initial_ref = float(preset.initial_capital or request.account_state.initial_capital)
        daily_rule = rules.usage.get("daily_loss")
        max_rule = rules.usage.get("max_loss")
        daily_usage_pct = float(daily_rule.usage_pct) if daily_rule else 0.0
        remaining_daily_risk_pct = (
            max(0.0, float(daily_rule.remaining_amount)) / initial_ref * 100.0
            if daily_rule and initial_ref > 0
            else 0.0
        )
        remaining_max_loss_pct = (
            max(0.0, float(max_rule.remaining_amount)) / initial_ref * 100.0
            if max_rule and initial_ref > 0
            else 0.0
        )

        # ------------------------------------------------------------------ #
        # TIER 4 — BLOCK (0.0x)                                              #
        # Any one condition is sufficient.                                    #
        # ------------------------------------------------------------------ #
        tier4 = False
        funding_suitability = str(candidate.funding_suitability or "").strip().lower()

        if funding_suitability == "block":
            tier4 = True
            reasons.append(
                f"Market Scanner funding suitability is {funding_suitability}; candidate blocked."
            )
            reasons.extend(
                f"scanner:{reason}" for reason in candidate.funding_reason_codes if reason
            )

        if not tier4 and rules.status in {"BREACHED", "LOCKED"}:
            tier4 = True
            if rules.status == "BREACHED":
                reasons.append("Funding rule breached; no new risk is allowed.")
            else:
                reasons.append("Daily loss usage is above lock threshold; no new entries.")

        if not tier4 and budget.per_trade_amount <= 0:
            tier4 = True
            reasons.append("No remaining daily risk budget.")

        if not tier4 and consistency.status == "blocked":
            tier4 = True
            reasons.append("Consistency cap exceeded by best trading day.")

        if not tier4 and daily_usage_pct >= 80.0:
            tier4 = True
            reasons.append(
                f"Daily loss usage at {daily_usage_pct:.1f}% (>= 80%); all new entries blocked."
            )

        if not tier4 and candidate.stop is None:
            tier4 = True
            reasons.append("Candidate has no stop; risk cannot be bounded.")
        elif not tier4 and stop_pct > max(0.0001, remaining_daily_risk_pct):
            # Stop exceeds remaining risk budget — hard block.
            tier4 = True
            reasons.append(
                "Candidate stop risk exceeds remaining daily risk budget; candidate blocked."
            )

        if not tier4 and candidate.module_backtest_grade == "overfit_risk":
            tier4 = True
            reasons.append("Historical backtest shows overfit risk; candidate blocked.")

        if not tier4:
            # Check critical module (options_gex or technical) suitability == "block".
            critical_modules = {"options_gex", "technical"}
            for mod_name, mod_ev in (candidate.evidence_by_module or {}).items():
                if (
                    mod_name.lower() in critical_modules
                    and str(mod_ev.get("suitability", "")).lower() == "block"
                ):
                    tier4 = True
                    reasons.append(
                        f"Critical module '{mod_name}' has suitability=block; candidate blocked."
                    )
                    break

        if tier4:
            decision = "BLOCK"
            size_multiplier = 0.0
            # Apply signal penalty for warnings even on BLOCK (informational only).
            _ = self._signal_penalty(candidate, warnings)
            self._backtest_grade_penalty_warnings_only(candidate, warnings)
            return self._build_gate_decision(
                candidate=candidate,
                decision=decision,
                size_multiplier=size_multiplier,
                reasons=reasons,
                warnings=warnings,
                budget=budget,
                stop_pct=stop_pct,
                max_loss_at_stop=max_loss_at_stop,
                remaining_daily_risk_pct=remaining_daily_risk_pct,
                remaining_max_loss_pct=remaining_max_loss_pct,
                binding_equity=rules.binding_equity,
            )

        # ------------------------------------------------------------------ #
        # TIER 3 — MICRO (0.25x)                                             #
        # ------------------------------------------------------------------ #
        tier3 = False

        if consistency.status == "warning":
            tier3 = True
            warnings.append("Best-day consistency above warning threshold; micro-size applied.")

        weakest = candidate.weakest_link_module
        if not tier3 and weakest:
            weakest_ev = (candidate.evidence_by_module or {}).get(weakest, {})
            dqs = weakest_ev.get("data_quality_score")
            if dqs is not None and float(dqs) < 0.25:
                tier3 = True
                warnings.append(
                    f"Weakest-link module '{weakest}' has data_quality_score={dqs:.2f} < 0.25;"
                    " micro-size applied."
                )

        if not tier3 and daily_usage_pct >= 60.0:
            tier3 = True
            warnings.append(
                f"Daily loss usage at {daily_usage_pct:.1f}% (>= 60%); micro-size applied."
            )

        if not tier3 and (
            candidate.module_backtest_grade == "weak_edge"
            and candidate.scanner_recommended_size_multiplier is not None
            and candidate.scanner_recommended_size_multiplier < 0.5
        ):
            tier3 = True
            warnings.append(
                "Weak-edge backtest combined with low scanner size recommendation;"
                " micro-size applied."
            )

        if tier3:
            size_multiplier = 0.25
            decision = "SIZE_DOWN"
            # Still apply backtest penalty (informational, block already handled above).
            backtest_penalty = self._backtest_grade_penalty(candidate, reasons, warnings)
            size_multiplier *= backtest_penalty
            signal_penalty = self._signal_penalty(candidate, warnings, reasons)
            if signal_penalty == 0.0:
                decision = "BLOCK"
                size_multiplier = 0.0
            else:
                size_multiplier = max(0.0, size_multiplier * signal_penalty)
                if size_multiplier == 0.0:
                    decision = "BLOCK"
            # Scanner cap.
            if decision != "BLOCK" and candidate.scanner_recommended_size_multiplier is not None:
                size_multiplier = min(
                    size_multiplier, candidate.scanner_recommended_size_multiplier
                )
            return self._build_gate_decision(
                candidate=candidate,
                decision=decision,
                size_multiplier=size_multiplier,
                reasons=reasons,
                warnings=warnings,
                budget=budget,
                stop_pct=stop_pct,
                max_loss_at_stop=max_loss_at_stop,
                remaining_daily_risk_pct=remaining_daily_risk_pct,
                remaining_max_loss_pct=remaining_max_loss_pct,
                binding_equity=rules.binding_equity,
            )

        # ------------------------------------------------------------------ #
        # TIER 2 — REDUCED (0.50x)                                           #
        # ------------------------------------------------------------------ #
        tier2 = False
        if funding_suitability == "size_down":
            tier2 = True
            warnings.append("Market Scanner funding suitability=size_down; reduced-size applied.")

        non_critical_modules = {
            "macro_micro",
            "argentina",
            "forensic",
            "sentiment",
            "microstructure",
        }
        for mod_name, mod_ev in (candidate.evidence_by_module or {}).items():
            if (
                not tier2
                and mod_name.lower() in non_critical_modules
                and str(mod_ev.get("suitability", "")).lower() == "size_down"
            ):
                tier2 = True
                warnings.append(
                    f"Non-critical module '{mod_name}' has suitability=size_down;"
                    " reduced-size applied."
                )
                break

        if not tier2 and (
            candidate.scanner_recommended_size_multiplier is not None
            and candidate.scanner_recommended_size_multiplier < 0.75
        ):
            tier2 = True
            warnings.append(
                f"Scanner recommended size multiplier"
                f" {candidate.scanner_recommended_size_multiplier:.2f} < 0.75;"
                " reduced-size applied."
            )

        if not tier2 and (candidate.conflict_score is not None and candidate.conflict_score >= 0.5):
            tier2 = True
            warnings.append(
                "Market Scanner vs Predictive conflict is elevated; reduced-size applied."
            )

        if not tier2 and (candidate.tail_risk is not None and candidate.tail_risk >= 0.7):
            tier2 = True
            warnings.append("Predictive tail risk is elevated; reduced-size applied.")

        if tier2:
            size_multiplier = 0.50
            decision = "SIZE_DOWN"
            backtest_penalty = self._backtest_grade_penalty(candidate, reasons, warnings)
            size_multiplier *= backtest_penalty
            signal_penalty = self._signal_penalty(candidate, warnings, reasons)
            if signal_penalty == 0.0:
                decision = "BLOCK"
                size_multiplier = 0.0
            else:
                size_multiplier = max(0.0, size_multiplier * signal_penalty)
                if size_multiplier == 0.0:
                    decision = "BLOCK"
            if decision != "BLOCK" and candidate.scanner_recommended_size_multiplier is not None:
                size_multiplier = min(
                    size_multiplier, candidate.scanner_recommended_size_multiplier
                )
            return self._build_gate_decision(
                candidate=candidate,
                decision=decision,
                size_multiplier=size_multiplier,
                reasons=reasons,
                warnings=warnings,
                budget=budget,
                stop_pct=stop_pct,
                max_loss_at_stop=max_loss_at_stop,
                remaining_daily_risk_pct=remaining_daily_risk_pct,
                remaining_max_loss_pct=remaining_max_loss_pct,
                binding_equity=rules.binding_equity,
            )

        # ------------------------------------------------------------------ #
        # TIER 1 — NORMAL (1.0x)                                             #
        # ------------------------------------------------------------------ #
        size_multiplier = 1.0
        decision = "ALLOW"

        backtest_penalty = self._backtest_grade_penalty(candidate, reasons, warnings)
        if backtest_penalty == 0.0:
            decision = "BLOCK"
        elif backtest_penalty < 1.0:
            decision = "SIZE_DOWN"
        size_multiplier *= backtest_penalty

        signal_penalty = self._signal_penalty(candidate, warnings, reasons)
        if signal_penalty == 0.0:
            decision = "BLOCK"
        elif signal_penalty < 1.0 and decision == "ALLOW":
            decision = "SIZE_DOWN"
        size_multiplier *= signal_penalty

        if size_multiplier == 0.0:
            decision = "BLOCK"

        # Final scanner cap.
        if decision != "BLOCK" and candidate.scanner_recommended_size_multiplier is not None:
            size_multiplier = min(size_multiplier, candidate.scanner_recommended_size_multiplier)
            if size_multiplier < 1.0 and decision == "ALLOW":
                decision = "SIZE_DOWN"

        if not reasons and decision == "ALLOW":
            reasons.append("Funding rules, stop distance and risk budget permit the trade.")

        return self._build_gate_decision(
            candidate=candidate,
            decision=decision,
            size_multiplier=size_multiplier,
            reasons=reasons,
            warnings=warnings,
            budget=budget,
            stop_pct=stop_pct,
            max_loss_at_stop=max_loss_at_stop,
            remaining_daily_risk_pct=remaining_daily_risk_pct,
            remaining_max_loss_pct=remaining_max_loss_pct,
            binding_equity=rules.binding_equity,
        )

    def _build_gate_decision(
        self,
        *,
        candidate: TradeCandidate,
        decision: str,
        size_multiplier: float,
        reasons: list[str],
        warnings: list[str],
        budget: AllowedRiskBudget,
        stop_pct: float,
        max_loss_at_stop: float,
        remaining_daily_risk_pct: float,
        remaining_max_loss_pct: float,
        binding_equity: float | None = None,
    ) -> CandidateGateDecision:
        """Assemble the final CandidateGateDecision from computed values."""
        size_multiplier = max(0.0, min(1.0, size_multiplier))
        # Reconstruct equity from budget when not provided.
        equity = (
            binding_equity
            if binding_equity is not None
            else (
                budget.per_trade_amount / max(budget.per_trade_pct / 100.0, 1e-9)
                if budget.per_trade_pct > 0
                else 0.0
            )
        )
        raw_notional = (
            position_notional_from_risk(
                equity,
                budget.per_trade_pct,
                max(stop_pct, 1e-9),
            )
            or 0.0
        )
        kelly_cap = (
            equity
            * fractional_kelly(
                candidate.expected_win_prob or 0.5,
                win_payoff=candidate.rr_ratio or 1.0,
                loss_payoff=1.0,
                shrink=0.25,
                cap=0.25,
            )
            if equity > 0
            else budget.max_position_notional
        )
        suggested = min(
            raw_notional, budget.max_position_notional, kelly_cap or budget.max_position_notional
        )
        if decision == "BLOCK":
            suggested = 0.0
            size_multiplier = 0.0
        else:
            suggested *= size_multiplier

        return CandidateGateDecision(
            symbol=candidate.symbol,
            direction=candidate.direction,
            decision=decision,

            allowed_risk_pct=round(budget.per_trade_pct, 4),
            suggested_notional=round(max(0.0, suggested), 2),
            size_multiplier=round(size_multiplier, 4),
            max_loss_at_stop=round(max_loss_at_stop, 2),
            reasons=reasons,
            warnings=warnings,
            remaining_daily_risk_after=round(
                max(0.0, budget.daily_lock_remaining_amount - max_loss_at_stop), 2
            ),
            module_backtest_grade=candidate.module_backtest_grade,
            options_gex_source_tier=candidate.options_gex_source_tier,
            options_gex_data_quality_score=candidate.options_gex_data_quality_score,
            funding_suitability=candidate.funding_suitability,
            funding_reason_codes=list(candidate.funding_reason_codes or []),
            evidence_by_module=candidate.evidence_by_module or {},
            best_supporting_module=candidate.best_supporting_module,
            weakest_link_module=candidate.weakest_link_module,
            scanner_recommended_size_multiplier=candidate.scanner_recommended_size_multiplier,
            remaining_daily_risk_pct=round(remaining_daily_risk_pct, 4),
            remaining_max_loss_pct=round(remaining_max_loss_pct, 4),
        )

    def _backtest_grade_penalty_warnings_only(
        self,
        candidate: TradeCandidate,
        warnings: list[str],
    ) -> None:
        """Add informational warnings for backtest grade without returning a block."""
        grade = candidate.module_backtest_grade
        if grade == "insufficient_data":
            warnings.append("Insufficient historical validation from module backtest.")
        elif grade == "overfit_risk":
            warnings.append("Historical backtest shows overfit risk.")

    def _backtest_grade_penalty(
        self,
        candidate: TradeCandidate,
        reasons: list[str],
        warnings: list[str],
    ) -> float:
        grade = candidate.module_backtest_grade
        if grade is None or grade == "validated":
            return 1.0
        if grade == "insufficient_data":
            reasons.append(
                "Insufficient historical validation from module backtest; candidate blocked."
            )
            return 0.0
        if grade == "overfit_risk":
            reasons.append("Historical backtest shows overfit risk; candidate blocked.")
            return 0.0
        warnings.append("Historical backtest shows weak edge; size reduced aggressively.")
        return 0.25

    def _signal_penalty(
        self, candidate: TradeCandidate, warnings: list[str], reasons: list[str] | None = None
    ) -> float:
        if reasons is None:
            reasons = []
        penalty = 1.0
        if candidate.tail_risk is not None and candidate.tail_risk >= 0.70:
            penalty *= 0.5
            warnings.append("Predictive tail risk is elevated.")
        if candidate.jump_risk is not None and candidate.jump_risk >= 0.50:
            penalty *= 0.7
            warnings.append("Predictive jump risk is elevated.")
        if candidate.conflict_score is not None:
            if candidate.conflict_score >= 0.75:
                reasons.append(
                    "Market Scanner vs Predictive conflict is critical; candidate blocked."
                )
                return 0.0
            elif candidate.conflict_score >= 0.50:
                penalty *= 0.5
                warnings.append("Market Scanner vs Predictive conflict is elevated; size reduced.")
        gamma = (candidate.gamma_regime or "").upper()
        if gamma in {"NEGATIVE_GAMMA", "BEARISH", "SHOCK"}:
            penalty *= 0.5
            warnings.append("Options/GEX reports adverse gamma regime.")
        term = (candidate.iv_term_structure or "").lower()
        if "backward" in term:
            penalty *= 0.75
            warnings.append("Options term structure is in backwardation.")
        if candidate.squeeze_probability is not None and candidate.squeeze_probability >= 0.75:
            penalty *= 0.75
            warnings.append("Squeeze probability is high; size reduced.")
        tier = (candidate.options_gex_source_tier or "").lower()
        if tier == "light_proxy":
            penalty *= 0.5
            warnings.append("Options/GEX source is a light proxy; size reduced aggressively.")
        elif tier == "snapshot_chain":
            penalty *= 0.8
            warnings.append("Options/GEX source is snapshot-chain only; size kept conservative.")
        if candidate.options_gex_data_quality_score is not None:
            if candidate.options_gex_data_quality_score < 0.35:
                reasons.append(
                    "Options/GEX data quality is fatally below validation threshold; candidate blocked."
                )
                return 0.0
            elif candidate.options_gex_data_quality_score < 0.75:
                penalty *= 0.5
                warnings.append("Options/GEX data quality is mediocre; size reduced aggressively.")
        return max(0.0, min(1.0, penalty))

    def _compute_challenge_simulation(
        self, request: PortfolioRiskRequest
    ) -> list[ChallengeSimulationResult]:
        """Simulate how the same account state looks under 3 canonical presets."""
        sim_preset_ids = ["ftmo_2_step", "topstep_combine", "custom"]
        results: list[ChallengeSimulationResult] = []

        for preset_id in sim_preset_ids:
            base = _PRESET_DEFS.get(preset_id, _PRESET_DEFS["custom"])
            # Resolve with the actual account capital so percentages are meaningful.
            preset = base.model_copy(
                update={"initial_capital": request.account_state.initial_capital}
            )
            if preset_id == "topstep_combine" and preset.max_loss_amount is None:
                preset = preset.model_copy(
                    update={
                        "max_loss_amount": _topstep_mll_amount(
                            request.account_state.initial_capital
                        )
                    }
                )

            try:
                rules = self._evaluate_rules(request, preset)
                consistency = self._consistency(request, preset)
            except Exception as exc:
                logger.warning(
                    "portfolio_risk.challenge_simulation_failed preset=%s error=%s",
                    preset_id,
                    str(exc)[:180],
                )
                results.append(
                    ChallengeSimulationResult(
                        preset_id=preset_id,
                        preset_name=preset.name,
                        account_status="ACTIVE",
                        first_breach_rule=None,
                        daily_loss_usage_pct=0.0,
                        max_loss_usage_pct=0.0,
                        notes=["simulation_error"],
                    )
                )
                continue

            daily = rules.usage.get("daily_loss")
            max_loss = rules.usage.get("max_loss")
            daily_usage_pct = float(daily.usage_pct) if daily else 0.0
            max_loss_usage_pct = float(max_loss.usage_pct) if max_loss else 0.0

            first_breach: str | None = None
            notes: list[str] = []

            if max_loss and max_loss.breached:
                first_breach = "max_loss"
                notes.append("max_loss rule breached under this preset.")
            elif daily and daily.breached:
                first_breach = "daily_loss"
                notes.append("daily_loss rule breached under this preset.")
            elif consistency.status == "blocked":
                first_breach = "consistency"
                notes.append("Consistency cap would fire under this preset.")

            results.append(
                ChallengeSimulationResult(
                    preset_id=preset_id,
                    preset_name=preset.name,
                    account_status=rules.status,
                    first_breach_rule=first_breach,
                    daily_loss_usage_pct=round(daily_usage_pct, 2),
                    max_loss_usage_pct=round(max_loss_usage_pct, 2),
                    consistency_ratio=round(consistency.best_day_ratio, 4),
                    notes=notes,
                )
            )

        return results

    def _action_plan(
        self,
        rules: _RuleState,
        consistency: ConsistencyMetrics,
        budget: AllowedRiskBudget,
        decisions: list[CandidateGateDecision],
        survival: FundingSurvivalSummary | None = None,
    ) -> list[str]:
        plan: list[str] = []

        # Kill switch message must be first when any hard reason fires.
        if survival and survival.kill_switch_reasons:
            reasons_str = ", ".join(survival.kill_switch_reasons)
            plan.append(f"KILL SWITCH ACTIVE: {reasons_str}. Stop all new entries.")

        if rules.status == "BREACHED":
            plan.append("Stop trading: at least one funding rule is breached.")
            return plan
        if rules.status == "LOCKED":
            plan.append(
                "Stop opening new trades today; daily loss usage is above the lock threshold."
            )
            return plan

        # Daily usage at 80%+ but not yet a breach.
        daily_rule = rules.usage.get("daily_loss")
        daily_usage_pct = float(daily_rule.usage_pct) if daily_rule else 0.0
        if daily_usage_pct >= 80.0 and not (daily_rule and daily_rule.breached):
            plan.append(
                f"Daily loss budget at {daily_usage_pct:.1f}% — no new standard-size entries."
                " Micro-size manual entries only if custom preset."
            )

        plan.extend(
            [
                f"Risk no more than {budget.per_trade_pct:.2f}% per new idea.",
                f"Keep at least ${budget.daily_lock_remaining_amount:,.0f} before the daily lock threshold.",
            ]
        )
        if consistency.status == "warning":
            plan.append(
                "Reduce size until best-day consistency falls back under the warning threshold."
            )
        if any(d.decision == "BLOCK" for d in decisions):
            plan.append(
                "Blocked candidates need tighter stops, lower size, or a new session risk budget."
            )
        if not decisions:
            plan.append("No candidates submitted; desk is monitoring portfolio survival only.")
        return plan

    def _funding_survival(
        self,
        *,
        rules: _RuleState,
        consistency: ConsistencyMetrics,
        budget: AllowedRiskBudget,
        preset: FundingRulePreset,
        decisions: list[CandidateGateDecision],
        candidates: list[TradeCandidate],
    ) -> FundingSurvivalSummary:
        """Compute the top-level survival summary that the Risk Desk renders.

        This method **never authorizes**. It only summarises what the other
        evaluators have already decided, into a single grade + reason list +
        recommended risk-per-trade cap. The final ALLOW/BLOCK lives on each
        ``CandidateGateDecision`` — this object is for the operational cockpit.
        """
        reasons: list[str] = []

        daily = rules.usage.get("daily_loss")
        max_loss = rules.usage.get("max_loss")
        daily_usage_pct = float(daily.usage_pct) if daily else 0.0
        max_usage_pct = float(max_loss.usage_pct) if max_loss else 0.0

        if max_loss and max_loss.breached:
            reasons.append(KILL_SWITCH_MAX_LOSS_BREACH)
        if daily and daily.breached:
            reasons.append(KILL_SWITCH_DAILY_LOSS_BREACH)
        if daily_usage_pct >= preset.daily_lock_threshold * 100.0 and not (
            daily and daily.breached
        ):
            reasons.append(KILL_SWITCH_DAILY_LOSS_USAGE_HIGH)
        if max_usage_pct >= 80.0 and not (max_loss and max_loss.breached):
            reasons.append(KILL_SWITCH_DRAWDOWN_HIGH)
        if consistency.status == "blocked":
            reasons.append(KILL_SWITCH_CONSISTENCY_CAP)
        if budget.max_attempts_remaining <= 0:
            reasons.append(KILL_SWITCH_NO_ATTEMPTS_LEFT)

        # Inspect candidates for source-tier / overfit pressure flagged upstream.
        for candidate in candidates:
            grade = (candidate.module_backtest_grade or "").lower()
            tier = (candidate.options_gex_source_tier or "").lower()
            if grade == "overfit_risk" and KILL_SWITCH_OVERFIT_MODULE not in reasons:
                reasons.append(KILL_SWITCH_OVERFIT_MODULE)
            if grade == "insufficient_data" and KILL_SWITCH_INSUFFICIENT_DATA not in reasons:
                reasons.append(KILL_SWITCH_INSUFFICIENT_DATA)
            if tier == "light_proxy" and KILL_SWITCH_LIGHT_PROXY_ONLY not in reasons:
                reasons.append(KILL_SWITCH_LIGHT_PROXY_ONLY)

        # Survival score: weighted composite of 5 runway sub-scores.
        # Compute headroom metrics first so we can reuse them below.
        daily_limit = float(daily.limit_amount) if daily else 0.0
        daily_used_amt = float(daily.used_amount) if daily else 0.0
        max_limit_amt = float(max_loss.limit_amount) if max_loss else 0.0
        max_used_amt = float(max_loss.used_amount) if max_loss else 0.0
        initial_ref = float(preset.initial_capital or 1.0)

        remaining_daily_risk_pct = (
            max(0.0, daily_limit - daily_used_amt) / initial_ref * 100.0 if initial_ref > 0 else 0.0
        )
        remaining_max_loss_pct = (
            max(0.0, max_limit_amt - max_used_amt) / initial_ref * 100.0 if initial_ref > 0 else 0.0
        )
        best_day_ratio = consistency.best_day_ratio if consistency else 0.0
        cap = float(preset.consistency_cap)
        consistency_headroom_pct = max(0.0, (cap - best_day_ratio) * 100.0)

        # Sub-score components (each in [0, 1]).
        daily_loss_runway_score = max(0.0, 1.0 - daily_usage_pct / 100.0)
        max_loss_runway_score = max(0.0, 1.0 - max_usage_pct / 100.0)
        # Consistency runway: proportion of cap still available.
        if consistency.status == "blocked":
            consistency_runway_score = 0.0
        elif consistency.status == "warning":
            consistency_runway_score = 0.3
        elif consistency.status == "insufficient_data":
            consistency_runway_score = 0.5
        else:
            consistency_runway_score = (
                max(0.0, min(1.0, consistency_headroom_pct / (cap * 100.0))) if cap > 0 else 1.0
            )
        # Evidence quality: from best_supporting_module's data_quality_score, or 0.5 if no candidates.
        evidence_quality_score = 0.5
        if candidates:
            quality_scores: list[float] = []
            for candidate in candidates:
                best_mod = candidate.best_supporting_module
                if best_mod and candidate.evidence_by_module:
                    mod_ev = candidate.evidence_by_module.get(best_mod, {})
                    dqs = mod_ev.get("data_quality_score")
                    if dqs is not None:
                        quality_scores.append(float(dqs))
                elif candidate.options_gex_data_quality_score is not None:
                    quality_scores.append(float(candidate.options_gex_data_quality_score))
            if quality_scores:
                evidence_quality_score = sum(quality_scores) / len(quality_scores)
        # Conflict pressure: 1 - max conflict_score across all candidates.
        max_conflict = 0.0
        for candidate in candidates:
            if candidate.conflict_score is not None:
                max_conflict = max(max_conflict, float(candidate.conflict_score))
        conflict_pressure_score = max(0.0, 1.0 - max_conflict)

        score = (
            0.30 * daily_loss_runway_score
            + 0.25 * max_loss_runway_score
            + 0.20 * consistency_runway_score
            + 0.15 * evidence_quality_score
            + 0.10 * conflict_pressure_score
        ) * 100.0

        # Hard override: breach always → 0.
        if any(r in {KILL_SWITCH_MAX_LOSS_BREACH, KILL_SWITCH_DAILY_LOSS_BREACH} for r in reasons):
            score = 0.0
        score = round(max(0.0, min(100.0, score)), 2)

        # Recommended risk-per-trade — degrade preset cap by survival pressure.
        recommended = float(budget.per_trade_pct)
        if consistency.status == "warning":
            recommended *= 0.5
        if consistency.status == "blocked":
            recommended = 0.0
        if KILL_SWITCH_LIGHT_PROXY_ONLY in reasons:
            recommended *= 0.5
        if KILL_SWITCH_OVERFIT_MODULE in reasons or KILL_SWITCH_INSUFFICIENT_DATA in reasons:
            recommended *= 0.25
        if reasons and any(
            r
            in {
                KILL_SWITCH_MAX_LOSS_BREACH,
                KILL_SWITCH_DAILY_LOSS_BREACH,
                KILL_SWITCH_DAILY_LOSS_USAGE_HIGH,
                KILL_SWITCH_DRAWDOWN_HIGH,
                KILL_SWITCH_CONSISTENCY_CAP,
                KILL_SWITCH_NO_ATTEMPTS_LEFT,
            }
            for r in reasons
        ):
            recommended = 0.0
        recommended = max(0.0, min(recommended, float(preset.risk_per_trade_pct)))

        # Grade: derives from rules.status + reasons.
        grade: str
        if rules.status == "BREACHED":
            grade = "breached"
        elif rules.status == "LOCKED":
            grade = "locked"
        elif rules.status == "AT_RISK":
            grade = "at_risk"
        elif score >= 80.0 and not reasons:
            grade = "safe"
        elif score >= 60.0:
            grade = "monitor"
        else:
            grade = "at_risk"

        return FundingSurvivalSummary(
            funding_survival_score=round(score, 2),
            max_attempts_remaining_today=int(max(0, budget.max_attempts_remaining)),
            recommended_risk_per_trade_pct=round(recommended, 4),
            kill_switch_reasons=reasons,
            funding_grade=grade,

            remaining_daily_risk_pct=round(remaining_daily_risk_pct, 4),
            remaining_max_loss_pct=round(remaining_max_loss_pct, 4),
            consistency_headroom_pct=round(consistency_headroom_pct, 4),
        )


def portfolio_risk_service() -> PortfolioRiskService:
    return PortfolioRiskService()


def _float_or_none(value: object) -> float | None:
    """Return float(value) or None when value is None or unconvertible."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def scanner_factor_exposure_from_weights(
    weights: dict[str, float],
    loadings_by_symbol: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Gross factor exposure for portfolio diagnostics (delegates to scanner_factor_constraints)."""
    from backend.services.scanner_factor_constraints import compute_factor_exposure

    return compute_factor_exposure(weights, loadings_by_symbol)


def candidate_from_scanner_row(row_dict: dict[str, Any]) -> TradeCandidate:
    """Construct a TradeCandidate from a scanner row dict.

    Convenience helper that maps the scanner's multi-module evidence fields onto the
    TradeCandidate fields introduced in Task 4.  Callers that already have a
    TradeCandidate do not need this function.

    Expected scanner row keys (all optional):
    - ``symbol``, ``direction``, ``entry``, ``stop``, ``target`` — core fields.
    - ``evidence_by_module`` — dict keyed by module name.
    - ``best_supporting_module`` / ``weakest_link_module`` — str or None.
    - ``recommended_size_multiplier`` — float 0–1 from scanner gate evaluation.
    - Standard TradeCandidate fields (``confidence``, ``conflict_score``, etc.)
      are passed through transparently when present in ``row_dict``.
    """
    evidence_by_module: dict[str, Any] = row_dict.get("evidence_by_module") or {}
    best_supporting_module: str | None = row_dict.get("best_supporting_module")
    weakest_link_module: str | None = row_dict.get("weakest_link_module")
    scanner_recommended_size_multiplier = _float_or_none(
        row_dict.get("scanner_recommended_size_multiplier")
    )
    if scanner_recommended_size_multiplier is None:
        scanner_recommended_size_multiplier = _float_or_none(
            row_dict.get("recommended_size_multiplier")
        )
    production_mult = _float_or_none(row_dict.get("production_size_multiplier"))
    if production_mult is not None:
        if scanner_recommended_size_multiplier is not None:
            scanner_recommended_size_multiplier = min(
                production_mult, scanner_recommended_size_multiplier
            )
        else:
            scanner_recommended_size_multiplier = production_mult

    # Build the constructor kwargs from the row, stripping unknown keys.
    passthrough_keys = {
        "symbol",
        "direction",
        "entry",
        "stop",
        "target",
        "confidence",
        "source_module",
        "expected_win_prob",
        "rr_ratio",
        "scanner_score",
        "conflict_score",
        "tail_risk",
        "jump_risk",
        "gamma_regime",
        "iv_term_structure",
        "squeeze_probability",
        "atr_pct",
        "module_backtest_grade",
        "module_backtest_trades",
        "module_backtest_sharpe",
        "module_backtest_profit_factor",
        "options_gex_source_tier",
        "options_gex_data_quality_score",
        "options_gex_missing_components",
        "funding_suitability",
        "funding_reason_codes",
    }
    kwargs: dict[str, Any] = {k: v for k, v in row_dict.items() if k in passthrough_keys}
    kwargs["evidence_by_module"] = evidence_by_module
    kwargs["best_supporting_module"] = best_supporting_module
    kwargs["weakest_link_module"] = weakest_link_module
    if scanner_recommended_size_multiplier is not None:
        kwargs["scanner_recommended_size_multiplier"] = float(
            max(0.0, min(1.0, scanner_recommended_size_multiplier))
        )

    # Provide safe defaults for required fields not present in some scanner rows.
    kwargs.setdefault("symbol", "UNKNOWN")
    kwargs.setdefault("direction", "LONG")
    kwargs.setdefault("entry", 1.0)

    logger.debug(
        "portfolio_risk.candidate_from_scanner_row symbol=%s modules=%s",
        kwargs.get("symbol"),
        list(evidence_by_module.keys()),
    )
    return TradeCandidate(**kwargs)


def _topstep_mll_amount(initial: float) -> float:
    if initial >= 150_000:
        return 4_500.0
    if initial >= 100_000:
        return 3_000.0
    return 2_000.0


def _amount_from_pct_or_value(
    base: float,
    *,
    pct: float | None,
    amount: float | None,
    fallback_pct: float,
) -> float:
    if amount is not None and amount > 0:
        return float(amount)
    return float(base) * float(pct if pct is not None else fallback_pct) / 100.0


def _usage(limit_amount: float, used_amount: float) -> float:
    return max(0.0, used_amount) / max(limit_amount, 1e-9)


def _stop_distance_pct(candidate: TradeCandidate) -> float:
    if candidate.stop is None or candidate.entry <= 0:
        return 0.0
    return abs(float(candidate.entry) - float(candidate.stop)) / float(candidate.entry) * 100.0
