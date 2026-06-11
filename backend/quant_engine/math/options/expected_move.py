"""
backend/engine/metrics/expected_move.py
Sector: Options / Expected Move Engine
[ARCH-1]

Theoretical basis (Black-Scholes, 1973):
    Under geometric Brownian motion, the 1-sigma range represents ~68%
    of probable outcomes over a given horizon. The standard approximation
    used by institutional desks is:

        Expected Move = Spot × IV × √(DTE / 365)
"""

import math
from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

# ---------------------------------------------------------------------------
# Result Container
# ---------------------------------------------------------------------------


class ExpectedMoveResult(BaseModel):
    """
    Immutable container for the output of an Expected Move calculation.

    Provides symmetrical probabilistic bounds:
    - lower_bound: The 1σ support zone, institutional accumulation (Long entry).
    - upper_bound: The 1σ resistance zone, institutional distribution (Short entry).
    """

    model_config = ConfigDict(frozen=True)

    spot: float
    expected_move: float
    upper_bound: float
    lower_bound: float
    iv: float
    dte: int
    tte: float

    def get_summary(self) -> dict[str, Any]:
        """
        Returns a structured dictionary of all computed values.
        """
        return {
            "long_entry_zone": round(self.lower_bound, 4),
            "short_entry_zone": round(self.upper_bound, 4),
            "lower_bound_entry_zone": round(self.lower_bound, 4),
            "upper_bound_entry_zone": round(self.upper_bound, 4),
            "spot": round(self.spot, 4),
            "upper_bound": round(self.upper_bound, 4),
            "expected_move_abs": round(self.expected_move, 4),
            "expected_move_pct": round((self.expected_move / self.spot) * 100, 4),
            "iv_annualized": round(self.iv, 6),
            "dte": self.dte,
            "tte": round(self.tte, 6),
        }

    def summary(self) -> dict[str, Any]:
        """Backward-compatible alias for older pipeline consumers."""
        return self.get_summary()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ExpectedMoveEngine:
    """
    Calculates the probabilistic 1-sigma expected move for an underlying
    asset using Black-Scholes implied volatility dynamics.
    """

    TRADING_DAYS_PER_YEAR: int = 365

    @staticmethod
    def _validate_inputs(spot: float, iv: float, dte: int) -> Result[None]:
        """
        Validates all inputs before any computation is performed.
        Returns Result.success(None) if valid, or Result.failure with reason.
        """
        if not isinstance(dte, int):
            return Result.failure(
                reason=f"[ExpectedMoveEngine] 'dte' must be an integer "
                f"(received {type(dte).__name__}: {dte})."
            )
        if spot <= 0:
            return Result.failure(
                reason=f"[ExpectedMoveEngine] 'spot' must be strictly positive "
                f"(received {spot})."
            )
        if not (0 < iv <= 10.0):
            return Result.failure(
                reason=f"[ExpectedMoveEngine] 'iv' must be in range (0, 10.0] as a "
                f"decimal (received {iv})."
            )
        if dte <= 0:
            return Result.failure(
                reason=f"[ExpectedMoveEngine] 'dte' must be a positive integer "
                f"(received {dte})."
            )

        return Result.success(None)

    @staticmethod
    def compute_expected_move(spot: float, iv: float, dte: int) -> float:
        """
        Core calculation: the 1-sigma expected move in absolute price terms.
        """
        tte: float = dte / ExpectedMoveEngine.TRADING_DAYS_PER_YEAR
        return spot * iv * math.sqrt(tte)

    @staticmethod
    def _round_to_tick(price: float, tick_size: float) -> float:
        """
        Rounds a price level down to the nearest valid market tick increment.
        Uses floor rounding (conservative).
        """
        return math.floor(price / tick_size) * tick_size

    @classmethod
    def calculate(
        cls,
        spot: float,
        iv: float,
        dte: int,
        round_prices: bool = False,
        tick_size: float | None = None,
    ) -> Result[ExpectedMoveResult]:
        """
        Primary entry point. Computes the 1-sigma expected move and derives
        the upper and lower price bounds for the given underlying.

        The bounds represent key reference points:
        - lower_bound: Institutional accumulation zone (Long entry).
        - upper_bound: Institutional distribution zone (Short entry).
        """
        val_result = cls._validate_inputs(spot, iv, dte)
        if val_result.is_failure:
            return Result.failure(reason=val_result.reason)

        if round_prices and tick_size is None:
            return Result.failure(
                reason="[ExpectedMoveEngine] 'tick_size' must be provided "
                "when round_prices=True."
            )

        tte: float = dte / cls.TRADING_DAYS_PER_YEAR
        expected_move: float = cls.compute_expected_move(spot, iv, dte)

        raw_upper: float = spot + expected_move
        raw_lower: float = spot - expected_move

        if round_prices and tick_size is not None:
            upper_bound: float = cls._round_to_tick(raw_upper, tick_size)
            lower_bound: float = cls._round_to_tick(raw_lower, tick_size)
        else:
            upper_bound = raw_upper
            lower_bound = raw_lower

        return Result.success(
            ExpectedMoveResult(
                spot=spot,
                expected_move=expected_move,
                upper_bound=upper_bound,
                lower_bound=lower_bound,
                iv=iv,
                dte=dte,
                tte=tte,
            )
        )

    @classmethod
    def get_lower_bound(cls, spot: float, iv: float, dte: int) -> Result[float]:
        """
        Convenience accessor that returns only the lower_bound.
        Represents the 1σ support zone for Long entries.
        """
        res = cls.calculate(spot=spot, iv=iv, dte=dte)
        if res.is_failure:
            return Result.failure(reason=res.reason)
        return Result.success(res.unwrap().lower_bound)

    @classmethod
    def get_upper_bound(cls, spot: float, iv: float, dte: int) -> Result[float]:
        """
        Convenience accessor that returns only the upper_bound.
        Represents the 1σ resistance zone for Short entries.
        """
        res = cls.calculate(spot=spot, iv=iv, dte=dte)
        if res.is_failure:
            return Result.failure(reason=res.reason)
        return Result.success(res.unwrap().upper_bound)
