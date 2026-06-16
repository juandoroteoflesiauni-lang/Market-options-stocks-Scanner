"""Linear instrument sizer for futures contracts. # [PD-2][PD-4][TH][IM]"""

from __future__ import annotations

import math
from decimal import Decimal
from backend.domain.builder_models import BuilderSizingDecision, BuilderRuleEvaluation


class LinearInstrumentSizer:
    """Computes sizing for futures contracts based on tick value and stop ticks."""

    @staticmethod
    def compute(
        *,
        symbol: str,
        stop_ticks: int,
        tick_value: Decimal,
        risk_usd: Decimal,
        allowed_risk_pct: Decimal,
        rules: BuilderRuleEvaluation,
        factors: dict[str, Decimal],
    ) -> BuilderSizingDecision:
        """Calculate contracts based on tick-based risk budget."""
        if stop_ticks <= 0:
            return BuilderSizingDecision(
                contracts=0,
                contract_symbol=symbol,
                stop_ticks=stop_ticks,
                capped_by="blocked",
                builder_factors=factors,
                asset_type="future",
            )

        risk_per_contract = Decimal(stop_ticks) * tick_value
        raw_contracts = math.floor(float(risk_usd / risk_per_contract))
        contracts = max(0, min(raw_contracts, rules.available_contract_cap))

        capped_by = "builder_budget"
        if contracts < raw_contracts and rules.available_contract_cap < raw_contracts:
            capped_by = "contract_cap"
        elif contracts == 0:
            capped_by = "blocked"

        risk_used = Decimal(contracts) * risk_per_contract

        return BuilderSizingDecision(
            contracts=contracts,
            contract_symbol=symbol,
            allowed_risk_pct=allowed_risk_pct,
            risk_budget_usd=risk_usd,
            risk_used_usd=risk_used,
            stop_ticks=stop_ticks,
            capped_by=capped_by,
            builder_factors=factors,
            asset_type="future",
        )
