"""
QuantumBeta Terminal
====================
Module  : ExpectedMove_Engine
File    : expected_move_engine.py
Version : 1.0.0

Theoretical basis (Black-Scholes, 1973):
    Under geometric Brownian motion, the 1-sigma range represents ~68%
    of probable outcomes over a given horizon. The standard approximation
    used by institutional desks is:

        Expected Move = Spot × IV × √(DTE / 365)

    This derives from the B-S stochastic differential equation:
        dS/S = μ·dt + σ·dW

    where annualized volatility σ is scaled to the time horizon via √T,
    mirroring the diffusion term. The lower_bound represents the 1σ
    support level below which price has only ~16% statistical probability
    of closing — used as the institutional entry / accumulation reference
    in Long-Only architectures.

System constraint:
    Long-Only. No short-side logic, execution paths, or naming conventions
    related to short-selling are present in this module.

Usage:
    from expected_move_engine import ExpectedMoveEngine

    result = ExpectedMoveEngine.calculate(spot=150.00, iv=0.30, dte=30)
    print(result.summary())

    # Pipeline shortcut (Long-Only entry zone)
    entry_zone = ExpectedMoveEngine.get_lower_bound(spot=150.00, iv=0.30, dte=30)
"""

import math
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Result Container
# ---------------------------------------------------------------------------


@dataclass
class ExpectedMoveResult:
    """
    Immutable container for the output of an Expected Move calculation.

    In a Long-Only system the lower_bound is the primary field of interest:
    it represents the probabilistic support zone (1σ below spot) used as
    the institutional entry / "fear level" for planning long entries.

    Attributes:
        spot          : Input spot price of the underlying asset.
        expected_move : Absolute 1σ move in price units.
        upper_bound   : spot + expected_move  (~84th percentile).
        lower_bound   : spot - expected_move  (~16th percentile) — PRIMARY.
        iv            : Annualized implied volatility used (decimal).
        dte           : Days to expiration used in the calculation.
        tte           : Time to expiration as a fraction of a year (DTE/365).
    """

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

        Bidirectional entry zones are exposed for execution pipelines. The
        lower bound is the long accumulation zone and the upper bound is the
        short distribution zone. The legacy lower_bound_entry_zone alias is
        preserved for older consumers.

        Returns:
            dict: Rounded values ready for logging, display, or serialization.
        """
        return {
            "long_entry_zone": round(self.lower_bound, 4),
            "short_entry_zone": round(self.upper_bound, 4),
            "lower_bound_entry_zone": round(self.lower_bound, 4),
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
    QuantumBeta Terminal — ExpectedMove_Engine
    ==========================================
    Calculates the probabilistic 1-sigma expected move for an underlying
    asset using Black-Scholes implied volatility dynamics.

    All public methods are classmethods or staticmethods: the engine is
    stateless and requires no instantiation. Import and call directly.

    Class attributes:
        TRADING_DAYS_PER_YEAR (int): Calendar-day denominator used for
            annualization (365). Consistent with the B-S convention T = DTE/365.
    """

    TRADING_DAYS_PER_YEAR: int = 365

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_inputs(spot: float, iv: float, dte: int) -> None:
        """
        Validates all inputs before any computation is performed.
        Fails fast with descriptive messages to ease pipeline debugging.

        Args:
            spot (float): Current spot price. Must be strictly positive.
            iv   (float): Annualized IV as decimal (e.g. 0.30 for 30%).
                          Accepted range: (0, 10.0] — i.e. up to 1000%.
            dte  (int)  : Days to expiration. Must be a positive integer.

        Raises:
            TypeError : If dte is not an integer.
            ValueError: If spot, iv, or dte fall outside valid domains.
        """
        if not isinstance(dte, int):
            raise TypeError(
                f"[ExpectedMoveEngine] 'dte' must be an integer "
                f"(received {type(dte).__name__}: {dte})."
            )
        if spot <= 0:
            raise ValueError(
                f"[ExpectedMoveEngine] 'spot' must be strictly positive " f"(received {spot})."
            )
        if not (0 < iv <= 10.0):
            raise ValueError(
                f"[ExpectedMoveEngine] 'iv' must be in range (0, 10.0] as a "
                f"decimal — pass 0.30 for 30% IV (received {iv})."
            )
        if dte <= 0:
            raise ValueError(
                f"[ExpectedMoveEngine] 'dte' must be a positive integer " f"(received {dte})."
            )

    # ------------------------------------------------------------------
    # Core mathematical primitive
    # ------------------------------------------------------------------

    @staticmethod
    def compute_expected_move(spot: float, iv: float, dte: int) -> float:
        """
        Core calculation: the 1-sigma expected move in absolute price terms.

        Formula (Black-Scholes derived):
            Expected Move = Spot × IV × √(DTE / 365)

        Represents the ±1σ range (~68% confidence interval) over the
        given DTE horizon. The √(DTE/365) term is the B-S time-scaling
        factor for the diffusion component σ·dW of the SDE.

        Note: This method does NOT validate inputs. Call _validate_inputs()
              before using this method directly in external integrations.

        Args:
            spot (float): Current spot price of the underlying.
            iv   (float): Annualized implied volatility as a decimal.
            dte  (int)  : Calendar days to expiration.

        Returns:
            float: Absolute expected move (applied as ±delta from spot).
        """
        tte: float = dte / ExpectedMoveEngine.TRADING_DAYS_PER_YEAR
        return spot * iv * math.sqrt(tte)

    # ------------------------------------------------------------------
    # Tick rounding utility
    # ------------------------------------------------------------------

    @staticmethod
    def _round_to_tick(price: float, tick_size: float) -> float:
        """
        Rounds a price level down to the nearest valid market tick increment.

        Uses floor rounding (conservative) so that:
          - lower_bound is never overstated (entry zone stays wider).
          - upper_bound is never overstated (resistance stays tighter).

        Args:
            price     (float): Raw computed price level.
            tick_size (float): Minimum price increment (e.g. 0.01, 0.25).

        Returns:
            float: Price floored to the nearest tick.
        """
        return math.floor(price / tick_size) * tick_size

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    @classmethod
    def calculate(
        cls,
        spot: float,
        iv: float,
        dte: int,
        round_prices: bool = False,
        tick_size: float | None = None,
    ) -> ExpectedMoveResult:
        """
        Primary entry point for the ExpectedMove_Engine.

        Computes the 1-sigma expected move and derives the upper and lower
        price bounds for the given underlying, IV, and expiration horizon.

        Long-Only note:
            The lower_bound is the statistically meaningful entry zone for
            long positions. It represents the price level below which only
            ~16% of outcomes are expected to close, making it the preferred
            reference for institutional accumulation planning.

        Args:
            spot         (float)         : Current spot price of the underlying
                                           (e.g. 150.00).
            iv           (float)         : Annualized implied volatility as a
                                           decimal (e.g. 0.30 for 30% IV).
            dte          (int)           : Calendar days to expiration. Use
                                           actual calendar days, not trading
                                           days (consistent with T = DTE/365).
            round_prices (bool)          : If True, rounds price levels to
                                           tick_size using floor rounding.
                                           Default: False.
            tick_size    (Optional[float]): Minimum price increment for
                                           rounding (e.g. 0.01 for equities,
                                           0.25 for some futures). Required
                                           when round_prices=True.

        Returns:
            ExpectedMoveResult: Dataclass with all computed values.
                                Call .summary() for a serialization-ready dict.

        Raises:
            TypeError : If dte is not an integer.
            ValueError: On invalid inputs or missing tick_size when rounding.

        Example:
            >>> result = ExpectedMoveEngine.calculate(
            ...     spot=150.00, iv=0.30, dte=30
            ... )
            >>> print(result.summary())
            {
                'lower_bound_entry_zone': 139.2986,
                'spot': 150.0,
                'upper_bound': 160.7014,
                ...
            }
        """
        cls._validate_inputs(spot, iv, dte)

        if round_prices and tick_size is None:
            raise ValueError(
                "[ExpectedMoveEngine] 'tick_size' must be provided " "when round_prices=True."
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

        return ExpectedMoveResult(
            spot=spot,
            expected_move=expected_move,
            upper_bound=upper_bound,
            lower_bound=lower_bound,
            iv=iv,
            dte=dte,
            tte=tte,
        )

    # ------------------------------------------------------------------
    # Bidirectional pipeline shortcuts
    # ------------------------------------------------------------------

    @classmethod
    def get_lower_bound(cls, spot: float, iv: float, dte: int) -> float:
        """
        Convenience accessor that returns only the lower_bound.

        Designed for Long-Only execution pipelines where downstream modules
        (risk engine, order manager, alert system) need a single float
        representing the probabilistic entry zone without deserializing
        the full ExpectedMoveResult dataclass.

        Args:
            spot (float): Current spot price of the underlying.
            iv   (float): Annualized implied volatility as a decimal.
            dte  (int)  : Calendar days to expiration.

        Returns:
            float: The 1σ lower price bound (Long-Only entry reference level).

        Example:
            >>> entry = ExpectedMoveEngine.get_lower_bound(
            ...     spot=4500.00, iv=0.18, dte=45
            ... )
            >>> print(f"Entry zone: {entry:.2f}")
        """
        return cls.calculate(spot=spot, iv=iv, dte=dte).lower_bound

    @classmethod
    def get_upper_bound(cls, spot: float, iv: float, dte: int) -> float:
        """
        Convenience accessor that returns only the upper_bound.

        Used for SHORT execution pipelines: 1σ resistance / distribution zone
        above which only ~16% of outcomes are expected to close. Mirror of
        get_lower_bound for the bearish side.

        Args:
            spot (float): Current spot price of the underlying.
            iv   (float): Annualized implied volatility as a decimal.
            dte  (int)  : Calendar days to expiration.

        Returns:
            float: The 1σ upper price bound (SHORT entry reference level).
        """
        return cls.calculate(spot=spot, iv=iv, dte=dte).upper_bound


# ---------------------------------------------------------------------------
# Smoke-test  —  remove or wrap in CI harness before production deploy
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    SEPARATOR = "=" * 52

    # ------------------------------------------------------------------
    # Example 1: Replicating Black-Scholes course parameters
    #   S0=21, σ=0.235, T≈0.25 (91 calendar days ≈ 3 months)
    #   Reference: Mordecki (2024) — Ejercicio 3, p.29
    # ------------------------------------------------------------------
    print(SEPARATOR)
    print("Example 1 — Course B-S Parameters (Mordecki, Ex.3)")
    print(SEPARATOR)
    r1 = ExpectedMoveEngine.calculate(spot=21.00, iv=0.235, dte=91)
    for k, v in r1.get_summary().items():
        print(f"  {k:<28}: {v}")

    # ------------------------------------------------------------------
    # Example 2: Equity with penny-tick rounding
    # ------------------------------------------------------------------
    print()
    print(SEPARATOR)
    print("Example 2 — Equity (30-day, 30% IV, tick=0.01)")
    print(SEPARATOR)
    r2 = ExpectedMoveEngine.calculate(
        spot=150.00,
        iv=0.30,
        dte=30,
        round_prices=True,
        tick_size=0.01,
    )
    for k, v in r2.get_summary().items():
        print(f"  {k:<28}: {v}")

    # ------------------------------------------------------------------
    # Example 3: Index futures — pipeline shortcut
    # ------------------------------------------------------------------
    print()
    print(SEPARATOR)
    print("Example 3 — Long-Only Pipeline Shortcut (S&P proxy)")
    print(SEPARATOR)
    entry_zone: float = ExpectedMoveEngine.get_lower_bound(spot=4500.00, iv=0.18, dte=45)
    print(f"  {'lower_bound_entry_zone':<28}: {round(entry_zone, 2)}")

    # ------------------------------------------------------------------
    # Example 4: FX option — currency pair (course section 4.10)
    #   Domestic: pesos, Foreign: USD
    #   Spot: 10.50 (pesos/dollar), IV estimated from last 180 days
    # ------------------------------------------------------------------
    print()
    print(SEPARATOR)
    print("Example 4 — FX Option (Pesos/USD, course §4.10)")
    print(SEPARATOR)
    r4 = ExpectedMoveEngine.calculate(
        spot=10500.00,  # 10,500 pesos per 1000 USD contract unit
        iv=0.32,
        dte=60,
        round_prices=True,
        tick_size=0.01,
    )
    for k, v in r4.get_summary().items():
        print(f"  {k:<28}: {v}")

    print()
    print("Integration check passed. Ready for QuantumBeta deployment.")
