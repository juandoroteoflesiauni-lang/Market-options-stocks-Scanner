"""Options multi-leg payoff engine."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from backend.domain.strategy_models import (
    OptionLeg,
    OptionPayoffScenario,
    OptionStrategy,
    PayoffCurve,
)
from backend.layer_3_specialists.opciones_gex.bsm import BlackScholesPricer, OptionType


class StrategyPayoffEngine:
    """Computes expiration and BSM-repriced P/L for multi-leg option strategies."""

    def compute_payoff(
        self: StrategyPayoffEngine,
        strategy: OptionStrategy,
        scenario: OptionPayoffScenario,
    ) -> PayoffCurve:
        """Compute expiration payoff, repriced scenarios, break-evens and Greeks."""
        spots = np.linspace(scenario.spot_min, scenario.spot_max, scenario.steps)
        limitations = list(strategy.limitations) + list(scenario.limitations)
        if any(leg.iv is None for leg in strategy.legs):
            limitations.append(
                "Missing IV for at least one leg; BSM repricing and computed Greeks are partial."
            )
        if any(leg.entry_price is None for leg in strategy.legs):
            limitations.append(
                "Missing entry price for at least one leg; zero premium assumed for P/L."
            )

        points: list[dict[str, float]] = []
        for spot in spots:
            expiration_pl = strategy.underlying_quantity * (spot - strategy.spot)
            theoretical_pl = strategy.underlying_quantity * (spot - strategy.spot)
            iv_up_pl = strategy.underlying_quantity * (spot - strategy.spot)
            iv_down_pl = strategy.underlying_quantity * (spot - strategy.spot)
            time_shift_pl = strategy.underlying_quantity * (spot - strategy.spot)
            for leg in strategy.legs:
                expiration_pl += self._leg_pl_at_expiry(leg, float(spot))
                theoretical_pl += self._leg_repriced_pl(leg, float(spot), scenario)
                iv_up_pl += self._leg_repriced_pl(
                    leg,
                    float(spot),
                    scenario,
                    iv_shift=abs(float(scenario.iv_shift)),
                )
                iv_down_pl += self._leg_repriced_pl(
                    leg,
                    float(spot),
                    scenario,
                    iv_shift=-abs(float(scenario.iv_shift)),
                )
                time_shift_pl += self._leg_repriced_pl(
                    leg,
                    float(spot),
                    scenario,
                    dte_shift_days=max(0, int(scenario.dte_shift_days)),
                )
            points.append(
                {
                    "spot": round(float(spot), 8),
                    "pl": round(float(expiration_pl), 8),
                    "expiration_pl": round(float(expiration_pl), 8),
                    "theoretical_pl": round(float(theoretical_pl), 8),
                    "iv_up_pl": round(float(iv_up_pl), 8),
                    "iv_down_pl": round(float(iv_down_pl), 8),
                    "time_shift_pl": round(float(time_shift_pl), 8),
                }
            )
        pls = [point["pl"] for point in points]
        greeks = self._net_greeks(strategy, scenario)
        return PayoffCurve(
            points=points,
            max_profit=round(max(pls), 8) if pls else None,
            max_loss=round(min(pls), 8) if pls else None,
            break_evens=self._break_evens(points),
            net_delta=round(greeks["delta"], 8),
            net_gamma=round(greeks["gamma"], 8),
            net_theta=round(greeks["theta"], 8),
            net_vega=round(greeks["vega"], 8),
            limitations=list(dict.fromkeys(limitations)),
        )

    def _leg_pl_at_expiry(self: StrategyPayoffEngine, leg: OptionLeg, spot: float) -> float:
        intrinsic = max(spot - leg.strike, 0.0) if leg.right == "call" else max(leg.strike - spot, 0.0)
        premium = float(leg.entry_price or 0.0)
        unit_pl = intrinsic - premium if leg.side == "long" else premium - intrinsic
        return unit_pl * leg.quantity * leg.multiplier

    def _side_sign(self: StrategyPayoffEngine, leg: OptionLeg) -> float:
        return 1.0 if leg.side == "long" else -1.0

    def _leg_repriced_pl(
        self: StrategyPayoffEngine,
        leg: OptionLeg,
        spot: float,
        scenario: OptionPayoffScenario,
        *,
        iv_shift: float = 0.0,
        dte_shift_days: int = 0,
    ) -> float:
        premium = float(leg.entry_price or 0.0)
        if leg.iv is None:
            return self._leg_pl_at_expiry(leg, spot)
        sigma = max(float(leg.iv) + iv_shift, 1e-4)
        ttm = self._time_to_expiry_years(leg, scenario, dte_shift_days=dte_shift_days)
        opt = OptionType.CALL if leg.right == "call" else OptionType.PUT
        adjusted_spot = spot * float(np.exp(-float(scenario.dividend_yield) * ttm))
        price = BlackScholesPricer.price(
            adjusted_spot,
            leg.strike,
            ttm,
            scenario.risk_free_rate,
            sigma,
            opt,
        )
        unit_pl = price - premium if leg.side == "long" else premium - price
        return unit_pl * leg.quantity * leg.multiplier

    def _net_greeks(
        self: StrategyPayoffEngine,
        strategy: OptionStrategy,
        scenario: OptionPayoffScenario,
    ) -> dict[str, float]:
        totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        for leg in strategy.legs:
            sign_qty = self._side_sign(leg) * leg.quantity
            totals["delta"] += (
                float(leg.delta) if leg.delta is not None else self._computed_delta(leg, strategy, scenario)
            ) * sign_qty
            totals["gamma"] += (
                float(leg.gamma) if leg.gamma is not None else self._computed_gamma(leg, strategy, scenario)
            ) * sign_qty
            totals["theta"] += (
                float(leg.theta) if leg.theta is not None else self._computed_theta(leg, strategy, scenario)
            ) * sign_qty
            totals["vega"] += (
                float(leg.vega) if leg.vega is not None else self._computed_vega(leg, strategy, scenario)
            ) * sign_qty
        return totals

    def _computed_delta(
        self: StrategyPayoffEngine,
        leg: OptionLeg,
        strategy: OptionStrategy,
        scenario: OptionPayoffScenario,
    ) -> float:
        if leg.iv is None:
            return 0.0
        opt = OptionType.CALL if leg.right == "call" else OptionType.PUT
        ttm = self._time_to_expiry_years(leg, scenario)
        adjusted_spot = strategy.spot * float(np.exp(-float(scenario.dividend_yield) * ttm))
        return BlackScholesPricer.delta(
            adjusted_spot,
            leg.strike,
            ttm,
            scenario.risk_free_rate,
            float(leg.iv),
            opt,
        )

    def _computed_gamma(
        self: StrategyPayoffEngine,
        leg: OptionLeg,
        strategy: OptionStrategy,
        scenario: OptionPayoffScenario,
    ) -> float:
        if leg.iv is None:
            return 0.0
        ttm = self._time_to_expiry_years(leg, scenario)
        adjusted_spot = strategy.spot * float(np.exp(-float(scenario.dividend_yield) * ttm))
        return BlackScholesPricer.gamma(
            adjusted_spot,
            leg.strike,
            ttm,
            scenario.risk_free_rate,
            float(leg.iv),
        )

    def _computed_theta(
        self: StrategyPayoffEngine,
        leg: OptionLeg,
        strategy: OptionStrategy,
        scenario: OptionPayoffScenario,
    ) -> float:
        if leg.iv is None:
            return 0.0
        opt = OptionType.CALL if leg.right == "call" else OptionType.PUT
        ttm = self._time_to_expiry_years(leg, scenario)
        adjusted_spot = strategy.spot * float(np.exp(-float(scenario.dividend_yield) * ttm))
        return BlackScholesPricer.theta(
            adjusted_spot,
            leg.strike,
            ttm,
            scenario.risk_free_rate,
            float(leg.iv),
            opt,
        )

    def _computed_vega(
        self: StrategyPayoffEngine,
        leg: OptionLeg,
        strategy: OptionStrategy,
        scenario: OptionPayoffScenario,
    ) -> float:
        if leg.iv is None:
            return 0.0
        ttm = self._time_to_expiry_years(leg, scenario)
        adjusted_spot = strategy.spot * float(np.exp(-float(scenario.dividend_yield) * ttm))
        return BlackScholesPricer.vega(
            adjusted_spot,
            leg.strike,
            ttm,
            scenario.risk_free_rate,
            float(leg.iv),
        )

    def _time_to_expiry_years(
        self: StrategyPayoffEngine,
        leg: OptionLeg,
        scenario: OptionPayoffScenario,
        *,
        dte_shift_days: int = 0,
    ) -> float:
        valuation_date = scenario.valuation_date or datetime.now(UTC).date()
        days = max((leg.expiry - valuation_date).days - dte_shift_days, 0)
        return max(days / 365.0, 0.0)

    def _break_evens(
        self: StrategyPayoffEngine,
        points: list[dict[str, float]],
    ) -> list[float]:
        breaks: list[float] = []
        for left, right in zip(points, points[1:], strict=False):
            l_pl = left["pl"]
            r_pl = right["pl"]
            if l_pl == 0:
                breaks.append(left["spot"])
            if l_pl * r_pl < 0:
                ratio = abs(l_pl) / (abs(l_pl) + abs(r_pl))
                breaks.append(round(left["spot"] + ratio * (right["spot"] - left["spot"]), 8))
        return sorted(dict.fromkeys(breaks))
