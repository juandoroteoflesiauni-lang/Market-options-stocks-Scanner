"""Volume-share slippage model for options backtesting. # [PD-2][TH]"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

_TWO_PLACES = Decimal("0.01")
_DEFAULT_POWER = Decimal("0.6")


class SlippageEstimate(BaseModel):
    """Estimated slippage in price units."""

    model_config = ConfigDict(frozen=True)

    slippage: Decimal = Field(ge=Decimal("0"))
    slippage_pct: float = Field(ge=0.0)


class VolumeShareSlippageModel:
    """Power-law slippage: grows with order size / volume and IV."""

    @staticmethod
    def estimate(
        order_size: Decimal,
        market_volume: int,
        mid_price: Decimal,
        implied_volatility: float,
        *,
        power: Decimal = _DEFAULT_POWER,
    ) -> SlippageEstimate:
        """Estimate slippage; guards zero volume and NaN IV."""
        if mid_price <= Decimal("0") or order_size <= Decimal("0"):
            return SlippageEstimate(slippage=Decimal("0"), slippage_pct=0.0)
        if math.isnan(implied_volatility) or implied_volatility < 0:
            implied_volatility = 0.0

        vol = max(market_volume, 1)
        share = float(order_size) / float(vol)
        iv_factor = 1.0 + min(implied_volatility, 2.0)
        raw = float(mid_price) * (share ** float(power)) * iv_factor
        slippage = Decimal(str(raw)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        pct = float(slippage / mid_price) if mid_price > 0 else 0.0
        return SlippageEstimate(slippage=slippage, slippage_pct=pct)


def bur_zone_from_slippage_pct(slippage_pct: float) -> tuple[float, str]:
    """Map slippage pct to BUR proxy and buffer zone for pre-trade gate."""
    bur = min(1.0, max(0.0, slippage_pct * 5.0))
    if bur < 0.5:
        return bur, "GREEN"
    if bur < 0.8:
        return bur, "YELLOW"
    return bur, "RED"


__all__ = ["SlippageEstimate", "VolumeShareSlippageModel", "bur_zone_from_slippage_pct"]
