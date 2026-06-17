"""Realistic option fill model for backtesting — bid/ask + partial fills. # [PD-2][TH]"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.option_contract import OptionContract

_TWO_PLACES = Decimal("0.01")


class FillResult(BaseModel):
    """Simulated fill outcome."""

    model_config = ConfigDict(frozen=True)

    fill_price: Decimal = Field(ge=Decimal("0"))
    filled_qty: Decimal = Field(ge=Decimal("0"))
    requested_qty: Decimal = Field(ge=Decimal("0"))
    partial: bool = False


class RealisticOptionFillModel:
    """Buys at ask, sells at bid; partial fill when size exceeds liquidity."""

    @staticmethod
    def simulate(
        contract: OptionContract,
        quantity: Decimal,
        side: Literal["BUY", "SELL"],
        *,
        max_volume_fraction: Decimal = Decimal("0.10"),
    ) -> FillResult:
        """Return fill price/qty without mutating live order state."""
        if quantity <= Decimal("0"):
            return FillResult(
                fill_price=Decimal("0"),
                filled_qty=Decimal("0"),
                requested_qty=quantity,
                partial=False,
            )

        if side == "BUY":
            raw_price = contract.ask if contract.ask > 0 else contract.mid_price
        else:
            raw_price = contract.bid if contract.bid > 0 else contract.mid_price

        fill_price = raw_price.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)

        if contract.volume <= 0:
            filled = quantity
        else:
            cap = (Decimal(contract.volume) * max_volume_fraction).quantize(
                _TWO_PLACES, rounding=ROUND_HALF_UP
            )
            filled = min(quantity, max(cap, Decimal("1")))

        return FillResult(
            fill_price=fill_price,
            filled_qty=filled,
            requested_qty=quantity,
            partial=filled < quantity,
        )


__all__ = ["FillResult", "RealisticOptionFillModel"]
