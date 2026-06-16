"""Structured options sizer with slippage penalty and margin checks. # [PD-2][PD-4][TH][IM]"""

from __future__ import annotations

import math
from decimal import Decimal
from backend.domain.builder_models import BuilderSizingDecision, BuilderRuleEvaluation


class StructuredOptionsSizer:
    """Computes sizing for options structures utilizing premium risk and margin validation."""

    @staticmethod
    def compute(
        *,
        symbol: str,
        premium_per_contract: Decimal,
        bid_ask_spread_pct: Decimal,
        margin_required_per_contract: Decimal,
        available_buying_power: Decimal,
        max_bid_ask_spread_pct: Decimal,
        risk_usd: Decimal,
        allowed_risk_pct: Decimal,
        rules: BuilderRuleEvaluation,
        factors: dict[str, Decimal],
    ) -> BuilderSizingDecision:
        """Calculate number of option contracts using premium and buying power checks."""
        # 1. Edge case checking
        if premium_per_contract <= Decimal("0") or margin_required_per_contract <= Decimal("0"):
            return BuilderSizingDecision(
                contracts=0,
                contract_symbol=symbol,
                capped_by="blocked",
                builder_factors=factors,
                asset_type="option",
            )

        # 2. Slippage Penalty Coefficient
        if max_bid_ask_spread_pct <= Decimal("0"):
            max_bid_ask_spread_pct = Decimal("8.0")  # fallback default
            
        ratio = bid_ask_spread_pct / max_bid_ask_spread_pct
        slippage_factor = Decimal("1.0") - min(ratio, Decimal("1.0"))
        adjusted_risk_usd = risk_usd * slippage_factor

        # 3. Calculate quantity by risk budget
        qty_risk = math.floor(float(adjusted_risk_usd / premium_per_contract))

        # 4. Calculate quantity by available buying power/margin
        qty_margin = math.floor(float(available_buying_power / margin_required_per_contract))

        # 5. Apply cap constraints
        raw_contracts = min(qty_risk, qty_margin)
        contracts = max(0, min(raw_contracts, rules.available_contract_cap))

        # 6. Determine capping cause
        buying_power_limit_triggered = False
        capped_by = "builder_budget"

        if qty_margin < qty_risk:
            capped_by = "buying_power"
            buying_power_limit_triggered = True
        
        if contracts < raw_contracts and rules.available_contract_cap < raw_contracts:
            capped_by = "contract_cap"
        elif contracts == 0:
            capped_by = "blocked"

        risk_used = Decimal(contracts) * premium_per_contract
        margin_required_usd = Decimal(contracts) * margin_required_per_contract
        slippage_penalty_pct = (Decimal("1.0") - slippage_factor) * Decimal("100")

        return BuilderSizingDecision(
            contracts=contracts,
            contract_symbol=symbol,
            allowed_risk_pct=allowed_risk_pct,
            risk_budget_usd=risk_usd,
            risk_used_usd=risk_used,
            stop_ticks=0,
            capped_by=capped_by,
            builder_factors=factors,
            asset_type="option",
            margin_required_usd=margin_required_usd,
            slippage_penalty_pct=slippage_penalty_pct,
            buying_power_limit_triggered=buying_power_limit_triggered,
        )
