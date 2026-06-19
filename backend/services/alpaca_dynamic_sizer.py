"""Dynamic position sizer for Alpaca dual-route bots. # [PD-2][TH][IM]"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

_TWO_PLACES = Decimal("0.01")
_MIN_RR = 1.5


class DynamicSizeResult(BaseModel):
    """Result of multi-factor sizing overlay."""

    model_config = ConfigDict(frozen=True)

    quantity: int = Field(ge=0)
    notional_usd: float = Field(ge=0.0)
    kelly_factor: float = Field(ge=0.0, le=1.0)
    vol_factor: float = Field(ge=0.0, le=1.0)
    dd_factor: float = Field(ge=0.0, le=1.0)
    signal_factor: float = Field(ge=0.0, le=1.0)
    convergence_ok: bool = True
    reason_codes: tuple[str, ...] = ()


class DynamicSizer:
    """Kelly x F_vol x F_dd x F_signal with R:R convergence gate."""

    @staticmethod
    def _kelly_factor(win_prob: float, avg_win: float, avg_loss: float) -> float:
        if avg_loss <= 0 or win_prob <= 0:
            return 0.0
        b = avg_win / avg_loss
        kelly = win_prob - (1.0 - win_prob) / b if b > 0 else 0.0
        return max(0.0, min(0.25, kelly * 0.25))

    @staticmethod
    def _vol_factor(atr_pct: float | None) -> float:
        if atr_pct is None or atr_pct <= 0:
            return 1.0
        if atr_pct > 0.05:
            return 0.5
        if atr_pct > 0.03:
            return 0.75
        return 1.0

    @staticmethod
    def _dd_factor(bur: float) -> float:
        if bur >= 0.8:
            return 0.25
        if bur >= 0.5:
            return 0.5
        return 1.0

    @classmethod
    def size(
        cls,
        *,
        base_quantity: int,
        reference_price: float,
        signal_score: float,
        stop_loss: float | None,
        take_profit: float | None,
        atr_pct: float | None = None,
        bur: float = 0.0,
        win_prob: float = 0.55,
        avg_win_r: float = 1.5,
        avg_loss_r: float = 1.0,
    ) -> DynamicSizeResult:
        """Apply multi-factor overlay to base quantity."""
        reasons: list[str] = []
        convergence_ok = True
        if stop_loss is not None and take_profit is not None and reference_price > 0:
            risk = reference_price - stop_loss
            reward = take_profit - reference_price
            if risk > 0:
                rr = reward / risk
                if rr < _MIN_RR:
                    convergence_ok = False
                    reasons.append("convergence_rr_below_min")

        kelly = cls._kelly_factor(win_prob, avg_win_r, avg_loss_r)
        vol_f = cls._vol_factor(atr_pct)
        dd_f = cls._dd_factor(bur)
        signal_f = max(0.25, min(1.0, signal_score))

        composite = kelly * vol_f * dd_f * signal_f
        if not convergence_ok:
            composite *= 0.5
            reasons.append("convergence_size_halved")

        raw_qty = math.floor(base_quantity * max(composite, 0.1))
        adjusted = max(1, raw_qty) if base_quantity > 0 and convergence_ok else max(0, raw_qty)
        notional = float(
            (Decimal(str(adjusted)) * Decimal(str(reference_price))).quantize(
                _TWO_PLACES, rounding=ROUND_HALF_UP
            )
        )
        return DynamicSizeResult(
            quantity=adjusted,
            notional_usd=notional,
            kelly_factor=round(kelly, 4),
            vol_factor=round(vol_f, 4),
            dd_factor=round(dd_f, 4),
            signal_factor=round(signal_f, 4),
            convergence_ok=convergence_ok,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )


__all__ = ["DynamicSizeResult", "DynamicSizer"]
