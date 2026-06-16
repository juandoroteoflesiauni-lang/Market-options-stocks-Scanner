"""Deterministic Builder Plan backtest: replay daily PnL and score survival.

Validates whether a daily-PnL sequence survives the EOD trailing drawdown, passes
the evaluation, and remains payout-consistent — before risking a real account.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.domain.builder_models import (
    BuilderAccountState,
    BuilderProfile,
    mffu_builder_50k_profile,
)
from backend.services.builder_state_machine import trailing_dd_floor


class BuilderBacktestResult(BaseModel):
    """Outcome of replaying a daily-PnL sequence through Builder rules."""

    model_config = ConfigDict(frozen=True)

    days_simulated: int = Field(default=0, ge=0)
    survived: bool = True
    breached_on_day: int | None = None
    eval_passed: bool = False
    eval_passed_on_day: int | None = None
    final_equity: Decimal = Decimal("0")
    peak_equity: Decimal = Decimal("0")
    max_drawdown_usd: Decimal = Decimal("0")
    min_distance_to_trailing_dd: Decimal = Decimal("0")
    total_profit: Decimal = Decimal("0")
    best_day_profit: Decimal = Decimal("0")
    consistency_ratio: Decimal = Decimal("0")
    consistency_ok: bool = True
    qualified_days: int = Field(default=0, ge=0)
    daily_loss_violations: int = Field(default=0, ge=0)
    profit_factor: Decimal | None = None


class BuilderBacktestService:
    """Replay daily PnL series deterministically against a Builder profile."""

    def __init__(self, profile: BuilderProfile | None = None) -> None:
        self._profile = profile or mffu_builder_50k_profile()

    def run(self, daily_pnls: Sequence[Decimal | float | str]) -> BuilderBacktestResult:
        """Run the backtest over an ordered sequence of daily net PnL values."""
        profile = self._profile
        initial = profile.starting_balance
        equity = initial
        hwm = initial
        peak = initial
        max_dd = Decimal("0")
        min_distance = profile.max_loss
        breached_on: int | None = None
        eval_passed_on: int | None = None
        dll_violations = 0
        positives: list[Decimal] = []
        gross_profit = Decimal("0")
        gross_loss = Decimal("0")

        for index, raw in enumerate(daily_pnls, start=1):
            pnl = _to_decimal(raw)
            if pnl < Decimal("0") and -pnl >= profile.daily_loss_limit:
                dll_violations += 1
            equity += pnl
            if pnl > Decimal("0"):
                positives.append(pnl)
                gross_profit += pnl
            elif pnl < Decimal("0"):
                gross_loss += -pnl

            hwm = max(hwm, equity)
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)

            state = BuilderAccountState(
                initial_capital=initial,
                current_equity=equity,
                start_of_day_balance=equity,
                high_watermark_balance=hwm,
            )
            floor = trailing_dd_floor(state, profile)
            distance = equity - floor
            min_distance = min(min_distance, distance)

            if distance <= Decimal("0") and breached_on is None:
                breached_on = index
                break

            if eval_passed_on is None and (equity - initial) >= profile.profit_target:
                eval_passed_on = index

        days = breached_on if breached_on is not None else len(daily_pnls)
        total_profit = sum(positives, start=Decimal("0"))
        best_day = max(positives) if positives else Decimal("0")
        ratio = (
            (best_day / total_profit).quantize(Decimal("0.0001"))
            if total_profit > Decimal("0")
            else Decimal("0")
        )
        consistency_ok = len(positives) < 2 or ratio <= profile.consistency_cap
        profit_factor = (
            (gross_profit / gross_loss).quantize(Decimal("0.01"))
            if gross_loss > Decimal("0")
            else None
        )
        return BuilderBacktestResult(
            days_simulated=days,
            survived=breached_on is None,
            breached_on_day=breached_on,
            eval_passed=eval_passed_on is not None,
            eval_passed_on_day=eval_passed_on,
            final_equity=equity,
            peak_equity=peak,
            max_drawdown_usd=max_dd,
            min_distance_to_trailing_dd=max(Decimal("0"), min_distance),
            total_profit=total_profit,
            best_day_profit=best_day,
            consistency_ratio=ratio,
            consistency_ok=consistency_ok,
            qualified_days=len(positives),
            daily_loss_violations=dll_violations,
            profit_factor=profit_factor,
        )


def _to_decimal(value: Decimal | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
