"""Telemetry-only fill/slippage simulation for live Alpaca paths. # [SEC-001][PD-2]"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from backend.backtesting.fill_models import RealisticOptionFillModel
from backend.backtesting.slippage_models import VolumeShareSlippageModel, bur_zone_from_slippage_pct
from backend.models.option_contract import OptionContract
from backend.services.alpaca_pre_trade_risk_gate import PreTradeRiskGate

logger = logging.getLogger(__name__)


def log_fill_slippage_telemetry(
    *,
    module: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    limit_or_market_price: Decimal,
    contract: OptionContract | None = None,
    order_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute and log simulated fill/slippage; feeds BUR zone into risk gate."""
    record: dict[str, Any] = {
        "module": module,
        "symbol": symbol,
        "side": side,
        "quantity": str(quantity),
        "intended_price": str(limit_or_market_price),
    }
    slippage_pct = 0.0
    if contract is not None:
        fill = RealisticOptionFillModel.simulate(
            contract, quantity, side="BUY" if side.upper() == "BUY" else "SELL"
        )
        slip = VolumeShareSlippageModel.estimate(
            quantity,
            contract.volume,
            contract.mid_price if contract.mid_price > 0 else limit_or_market_price,
            contract.implied_volatility,
        )
        slippage_pct = slip.slippage_pct
        record.update(
            {
                "simulated_fill_price": str(fill.fill_price),
                "simulated_filled_qty": str(fill.filled_qty),
                "partial_fill": fill.partial,
                "estimated_slippage": str(slip.slippage),
                "estimated_slippage_pct": slip.slippage_pct,
            }
        )
    else:
        slip = VolumeShareSlippageModel.estimate(
            quantity,
            market_volume=10_000,
            mid_price=limit_or_market_price,
            implied_volatility=0.25,
        )
        slippage_pct = slip.slippage_pct
        record.update(
            {
                "estimated_slippage": str(slip.slippage),
                "estimated_slippage_pct": slip.slippage_pct,
            }
        )

    bur_proxy, buffer_zone = bur_zone_from_slippage_pct(slippage_pct)
    record["bur"] = bur_proxy
    record["buffer_zone"] = buffer_zone
    gate = PreTradeRiskGate.instance()
    gate.update_bur(bur_proxy)
    logger.info("fill_slippage.telemetry %s", record)
    return record


__all__ = ["log_fill_slippage_telemetry"]
