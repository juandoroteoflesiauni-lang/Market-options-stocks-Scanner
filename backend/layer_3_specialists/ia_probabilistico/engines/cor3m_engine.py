"""
=============================================================================
COR3M_Signal_Engine  —  QuantumBeta Terminal
=============================================================================
Purpose
-------
Consume the CBOE 3-Month Implied Correlation Index (COR3M) to detect
systemic-panic regimes and generate LONG-ONLY re-entry signals based on
the mechanics described in Driessen, Maenhout & Vilkov (2005):

    "Option-Implied Correlations and the Price of Correlation Risk"

Core insight
------------
A spike in COR3M reflects indiscriminate cross-asset selling and the
destruction of diversification benefits (high correlation risk premium).
The premium begins to unwind — and liquidity re-enters — when COR3M rolls
over and starts descending from extreme percentile territory.

The optimal long entry is therefore NOT at the peak of panic but at the
confirmed inflection: the moment the rolling percentile rank of COR3M
crosses DOWN through the exit threshold after having been above the panic
threshold.

State Machine
-------------
                        ┌──────────────────────────┐
                        │         NORMAL            │
                        │  percentile < panic_thr   │
                        └────────────┬─────────────-┘
                                     │  percentile ≥ panic_thr
                                     ▼
                        ┌──────────────────────────┐
                        │   SYSTEMIC_PANIC_HOLD     │
                        │  percentile ≥ panic_thr   │
                        └────────────┬──────────────┘
                                     │  percentile < signal_thr
                                     │  AND memory window active
                                     ▼
                        ┌──────────────────────────┐
                        │  LONG_LIQUIDITY_RALLY     │◄─ BUY SIGNAL EMITTED
                        │  (single-bar trigger)     │
                        └──────────────────────────┘
                                     │  reset
                                     ▼
                                  NORMAL

IMPORTANT — LONG-ONLY CONSTRAINT
---------------------------------
This engine contains ZERO short-selling logic. All signals are BUY triggers
or NEUTRAL. No signal will ever be SHORT, SELL_SHORT, or equivalent.

Calibration Guide
-----------------
Parameter           Default  Description
------------------  -------  -------------------------------------------------
percentile_window   252      Rolling window (bars) for rank calculation.
                             • 252 ≈ 1 calendar year of daily bars (recommended
                               baseline; captures one full cycle of correlation
                               regimes).
                             • Decrease (e.g. 126) for intraday / high-frequency
                               data or faster regime detection.
                             • Increase (e.g. 504) for longer-horizon positioning
                               and more conservative panic thresholds.
panic_threshold     0.90     Percentile above which the market is classified as
                             SYSTEMIC_PANIC_HOLD. Based on empirical evidence
                             that implied correlations peak ~63 % above realized
                             correlations during crises (Driessen et al., 2005).
signal_threshold    0.85     Percentile at which the BUY trigger fires on the
                             way down. Gap between panic and signal thresholds
                             (default 5 pp) acts as a confirmation buffer to
                             avoid false signals on noise.
memory_window       5        Number of bars after leaving PANIC state during
                             which the engine remains "armed" for a BUY trigger.
                             Set higher (10-20) for daily data in slow markets;
                             lower (2-3) for intraday data.

Dependencies
------------
    pandas  ≥ 2.0
    numpy   ≥ 1.24

Author  : QuantumBeta Terminal — Senior Quantitative Software Engineer
Version : 1.0.0
License : Proprietary
=============================================================================
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MarketState(Enum):
    """Internal regime states tracked by the signal engine."""

    NORMAL = auto()
    """Correlation environment is within historical norms."""

    SYSTEMIC_PANIC_HOLD = auto()
    """COR3M percentile has breached the panic threshold.
    No long entries are opened — the market is in freefall."""

    LONG_LIQUIDITY_RALLY = auto()
    """One-bar BUY trigger: percentile has descended from panic territory
    through the signal threshold, confirming correlation-premium collapse
    and the return of selective buying (liquidity re-entry)."""


class SignalType(Enum):
    """Output signal types.  LONG-ONLY: no short signals exist."""

    BUY = "BUY"
    """Initiate or add to long position."""

    NEUTRAL = "NEUTRAL"
    """No actionable signal; remain flat or hold existing longs."""


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalBar:
    """Immutable record for a single bar's engine output."""

    timestamp: pd.Timestamp
    cor3m_value: float
    percentile_rank: float
    market_state: MarketState
    signal: SignalType
    bars_since_panic: int
    note: str = ""

    def __str__(self) -> str:
        return (
            f"[{self.timestamp.date()}] "
            f"COR3M={self.cor3m_value:6.2f}  "
            f"Pct={self.percentile_rank:5.1%}  "
            f"State={self.market_state.name:<25s}  "
            f"Signal={self.signal.value:<7s}  "
            f"{self.note}"
        )


@dataclass
class EngineConfig:
    """All tunable hyper-parameters for COR3M_Signal_Engine.

    See module docstring for full calibration guide.
    """

    percentile_window: int = 252
    """Rolling window length (bars) for percentile rank computation."""

    panic_threshold: float = 0.90
    """Percentile above which the state transitions to SYSTEMIC_PANIC_HOLD."""

    signal_threshold: float = 0.85
    """Percentile below which the BUY trigger fires (must be < panic_threshold)."""

    memory_window: int = 5
    """Max bars after leaving PANIC during which a BUY trigger can still fire."""

    min_periods: int | None = None
    """Minimum number of observations required to produce a percentile rank.
    Defaults to ``percentile_window`` if None (no rank computed until the
    window is fully warmed up)."""

    def __post_init__(self) -> None:
        if not 0.0 < self.signal_threshold < self.panic_threshold < 1.0:
            raise ValueError(
                f"Must satisfy 0 < signal_threshold ({self.signal_threshold}) "
                f"< panic_threshold ({self.panic_threshold}) < 1"
            )
        if self.percentile_window < 2:
            raise ValueError("percentile_window must be ≥ 2.")
        if self.memory_window < 1:
            raise ValueError("memory_window must be ≥ 1.")
        if self.min_periods is None:
            object.__setattr__(self, "min_periods", self.percentile_window)


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------


class COR3M_Signal_Engine:
    """
    Long-only signal engine powered by the CBOE COR3M implied-correlation index.

    Parameters
    ----------
    config : EngineConfig, optional
        Full parameter bundle.  A default-constructed ``EngineConfig`` is used
        when omitted, equivalent to daily-bar calibration with a 252-bar window.

    Usage
    -----
    >>> engine = COR3M_Signal_Engine()
    >>> results: pd.DataFrame = engine.run(cor3m_series)
    >>> buy_signals = results[results["signal"] == "BUY"]
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: EngineConfig | None = None) -> None:
        self._cfg: EngineConfig = config or EngineConfig()
        self._signal_log: list[SignalBar] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config(self) -> EngineConfig:
        """Read-only view of the engine configuration."""
        return self._cfg

    @property
    def signal_log(self) -> list[SignalBar]:
        """Ordered list of all SignalBar objects from the last ``run()`` call."""
        return list(self._signal_log)

    def run(
        self,
        cor3m_data: pd.Series | pd.DataFrame,
        price_col: str = "close",
    ) -> pd.DataFrame:
        """
        Execute the full signal pipeline over a historical COR3M series.

        Parameters
        ----------
        cor3m_data : pd.Series or pd.DataFrame
            Historical COR3M values indexed by ``pd.DatetimeIndex`` (or any
            monotonic index).  If a ``DataFrame`` is supplied, ``price_col``
            names the column to use.
        price_col : str
            Column name when ``cor3m_data`` is a ``DataFrame``.

        Returns
        -------
        pd.DataFrame
            One row per input bar with columns:
            ``cor3m``, ``percentile_rank``, ``market_state``,
            ``signal``, ``bars_since_panic``, ``note``.
        """
        series = self._extract_series(cor3m_data, price_col)
        self._validate_series(series)

        pct_rank = self._compute_percentile_rank(series)
        results = self._apply_state_machine(series, pct_rank)

        return results

    # ------------------------------------------------------------------
    # Step 1 — Vectorised percentile rank
    # ------------------------------------------------------------------

    def _compute_percentile_rank(self, series: pd.Series) -> pd.Series:
        """
        Compute the rolling percentile rank of COR3M using fully vectorised
        pandas operations.

        Implementation note
        -------------------
        ``rolling(window).rank(pct=True)`` assigns the current value its
        fractional rank within the preceding *window* bars.  This is O(n log n)
        per window in pandas' Cython backend — far faster than a Python loop.

        ``min_periods`` controls the warm-up: NaN is returned for bars where
        fewer observations are available, ensuring no spurious signals during
        the initial warm-up period.
        """
        pct_rank: pd.Series = series.rolling(
            window=self._cfg.percentile_window,
            min_periods=self._cfg.min_periods,
        ).rank(pct=True)
        return pct_rank.rename("percentile_rank")

    # ------------------------------------------------------------------
    # Step 2 — State machine (row-by-row; intentionally explicit)
    # ------------------------------------------------------------------

    def _apply_state_machine(
        self,
        series: pd.Series,
        pct_rank: pd.Series,
    ) -> pd.DataFrame:
        """
        Walk forward through the percentile series applying the three-state
        machine defined in the module docstring.

        The loop is intentionally sequential: state transitions are path-
        dependent and cannot be fully vectorised without sacrificing clarity.
        For production intraday use, compile with Numba or Cython.

        LONG-ONLY GUARANTEE: The only non-NEUTRAL signal emitted is ``BUY``.
        There is no code path that yields a short signal.
        """
        self._signal_log.clear()

        state: MarketState = MarketState.NORMAL
        bars_since_panic: int = 0  # counter; 0 means "not recently panicked"
        recently_panicked: bool = False  # armed flag

        rows: list[dict[str, Any]] = []

        for ts, cor3m_val in series.items():
            pct: float = pct_rank.loc[ts]

            note = ""
            signal = SignalType.NEUTRAL

            # --- Warm-up guard -------------------------------------------
            if np.isnan(pct):
                state = MarketState.NORMAL
                bars_since_panic = 0
                recently_panicked = False
                rows.append(self._build_row(ts, cor3m_val, pct, state, signal, 0, "WARMUP"))
                continue

            # --- State transition logic -----------------------------------
            if pct >= self._cfg.panic_threshold:
                # Enter or remain in panic
                state = MarketState.SYSTEMIC_PANIC_HOLD
                recently_panicked = True
                bars_since_panic = 0  # reset counter while inside panic
                note = f"Pct {pct:.1%} ≥ panic threshold {self._cfg.panic_threshold:.0%}"

            elif recently_panicked:
                # We have left the panic zone; start (or continue) counting
                bars_since_panic += 1
                state = MarketState.NORMAL  # tentatively back to normal

                if bars_since_panic <= self._cfg.memory_window:
                    # Still within the armed window
                    if pct < self._cfg.signal_threshold:
                        # ── BUY TRIGGER ──────────────────────────────────────
                        # Correlation-risk premium is collapsing; liquidity returns.
                        # This is the only code path that emits a BUY signal.
                        state = MarketState.LONG_LIQUIDITY_RALLY
                        signal = SignalType.BUY
                        note = (
                            f"▶ LONG ENTRY — pct {pct:.1%} crossed below "
                            f"signal threshold {self._cfg.signal_threshold:.0%} "
                            f"({bars_since_panic} bar(s) after panic peak)"
                        )
                        # Disarm after trigger to avoid repeated signals
                        recently_panicked = False
                        bars_since_panic = 0
                    else:
                        note = (
                            f"Armed ({bars_since_panic}/{self._cfg.memory_window} bars) — "
                            f"waiting for pct < {self._cfg.signal_threshold:.0%}"
                        )
                else:
                    # Memory window expired without a trigger; disarm.
                    recently_panicked = False
                    bars_since_panic = 0
                    note = "Memory window expired without BUY trigger — reset to NORMAL"

            else:
                state = MarketState.NORMAL
                bars_since_panic = 0

            # --- Record --------------------------------------------------
            bar = SignalBar(
                timestamp=ts,
                cor3m_value=float(cor3m_val),
                percentile_rank=float(pct),
                market_state=state,
                signal=signal,
                bars_since_panic=bars_since_panic,
                note=note,
            )
            self._signal_log.append(bar)
            rows.append(self._build_row(ts, cor3m_val, pct, state, signal, bars_since_panic, note))

        return self._to_dataframe(rows, series.index)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_series(
        data: pd.Series | pd.DataFrame,
        price_col: str,
    ) -> pd.Series:
        if isinstance(data, pd.Series):
            return data.copy().rename("cor3m")
        if isinstance(data, pd.DataFrame):
            if price_col not in data.columns:
                raise KeyError(
                    f"Column '{price_col}' not found. " f"Available columns: {list(data.columns)}"
                )
            return data[price_col].copy().rename("cor3m")
        raise TypeError(f"Expected pd.Series or pd.DataFrame, got {type(data).__name__}.")

    @staticmethod
    def _validate_series(series: pd.Series) -> None:
        if series.empty:
            raise ValueError("Input series is empty.")
        if series.isnull().all():
            raise ValueError("Input series contains only NaN values.")
        if (series.dropna() < 0).any():
            warnings.warn(
                "COR3M contains negative values — verify data source.",
                UserWarning,
                stacklevel=3,
            )

    @staticmethod
    def _build_row(
        ts: pd.Timestamp,
        cor3m_val: float,
        pct: float,
        state: MarketState,
        signal: SignalType,
        bars_since_panic: int,
        note: str,
    ) -> dict[str, Any]:
        return {
            "timestamp": ts,
            "cor3m": cor3m_val,
            "percentile_rank": pct,
            "market_state": state.name,
            "signal": signal.value,
            "bars_since_panic": bars_since_panic,
            "note": note,
        }

    @staticmethod
    def _to_dataframe(rows: list[dict[str, Any]], index: pd.Index) -> pd.DataFrame:
        df = pd.DataFrame(rows).set_index("timestamp")
        df.index.name = "timestamp"
        return df


# ---------------------------------------------------------------------------
# Summary & reporting utilities
# ---------------------------------------------------------------------------


def summarise_signals(results: pd.DataFrame) -> None:
    """Print a concise summary of engine output to stdout."""
    total = len(results)
    warmup = results["note"].str.contains("WARMUP", na=False).sum()
    live = total - warmup
    buys = (results["signal"] == "BUY").sum()
    panics = (results["market_state"] == MarketState.SYSTEMIC_PANIC_HOLD.name).sum()

    print("\n" + "=" * 70)
    print("  COR3M Signal Engine  —  Run Summary")
    print("=" * 70)
    print(f"  Total bars processed  : {total:>6d}")
    print(f"  Warm-up bars (no rank): {warmup:>6d}")
    print(f"  Live bars             : {live:>6d}")
    print(f"  Bars in SYSTEMIC_PANIC: {panics:>6d}  ({panics/live:.1%} of live bars)")
    print(f"  BUY signals emitted   : {buys:>6d}")
    print("=" * 70)

    if buys > 0:
        buy_rows = results[results["signal"] == "BUY"]
        print("\n  ▶ BUY TRIGGERS:")
        for ts, row in buy_rows.iterrows():
            print(
                f"    {ts.date()}  "
                f"COR3M={row['cor3m']:6.2f}  "
                f"Pct={row['percentile_rank']:5.1%}  "
                f"{row['note']}"
            )
    else:
        print("\n  No BUY signals emitted in this sample.")
    print()


# ---------------------------------------------------------------------------
# Demo / smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Simulated COR3M scenario
    ========================
    Phase 1 (bars 0–251)   : Warm-up — engine accumulates 252-bar window.
                             COR3M oscillates in the historical "normal" band
                             (~25–45), mirroring the 28.7% realized-correlation
                             baseline from Driessen et al. (2005).
    Phase 2 (bars 252–299) : Normal market — steady drift, no panic.
    Phase 3 (bars 300–324) : Systemic panic — COR3M spikes toward 70–85+,
                             echoing crisis episodes (1997 Asia, 1998 Russia,
                             2001 9/11) where implied correlations peaked far
                             above realized.  Percentile rank exceeds 90 →
                             state = SYSTEMIC_PANIC_HOLD.
    Phase 4 (bars 325–327) : Sharp V-reversal — correlation risk premium
                             collapses violently.  COR3M plummets from ~83
                             to ~30 in 3 bars, driving percentile below 85.
                             Within the 5-bar memory window → BUY fires.
    Phase 5 (bars 328–356) : Recovery — COR3M mean-reverts toward normal.

    Design note
    -----------
    The V-reversal in Phase 4 is intentionally sharp to illustrate the
    mechanism clearly.  In live markets this can be 1–3 sessions of
    extreme mean-reversion after the panic catalyst resolves.
    """

    rng = np.random.default_rng(seed=99)

    # --- Phase 1 & 2: 300 bars of background / normal regime -------------
    # These values form the empirical CDF against which panic is measured.
    # Mean ~33 ≈ Driessen et al.'s 28.7% realized correlation baseline.
    n_background = 300
    background_vals = rng.uniform(22, 44, n_background)

    # --- Phase 3: Panic escalation (25 bars) -----------------------------
    # COR3M surges from ~44 toward ~85 over ~3 weeks.
    n_panic = 25
    panic_vals = np.linspace(44, 85, n_panic) + rng.normal(0, 1.5, n_panic)
    panic_vals = np.clip(panic_vals, 40, 90)

    # --- Phase 4: V-reversal — 3 sharp bars dropping COR3M into low 30s --
    # This is the "destruction of the correlation risk premium."
    # After 300 bars of [22-44] and 25 bars of [44-85], a drop to 30
    # sits at roughly the 25th percentile of the rolling window → well below
    # the 85% signal threshold.  BUY trigger fires here.
    unwind_vals = np.array([83.0, 55.0, 28.0])  # 3-bar collapse

    # --- Phase 5: Smooth recovery (29 bars) ------------------------------
    n_recover = 29
    recover_vals = np.linspace(32, 36, n_recover) + rng.normal(0, 2, n_recover)
    recover_vals = np.clip(recover_vals, 18, 50)

    cor3m_values = np.concatenate(
        [
            background_vals,
            panic_vals,
            unwind_vals,
            recover_vals,
        ]
    )

    # Build DatetimeIndex (business days)
    dates = pd.bdate_range(start="2020-01-02", periods=len(cor3m_values))
    cor3m_series = pd.Series(cor3m_values, index=dates, name="COR3M")

    print("=" * 70)
    print("  COR3M_Signal_Engine  —  QuantumBeta Terminal  v1.0.0")
    print("=" * 70)
    print(f"\n  Input series  : {len(cor3m_series)} bars")
    print(f"  Date range    : {cor3m_series.index[0].date()} → {cor3m_series.index[-1].date()}")
    print(f"  COR3M range   : {cor3m_series.min():.2f} – {cor3m_series.max():.2f}")

    # --- Instantiate engine with explicit config -------------------------
    cfg = EngineConfig(
        percentile_window=252,  # 1 year of daily bars
        panic_threshold=0.90,  # 90th percentile → SYSTEMIC_PANIC_HOLD
        signal_threshold=0.85,  # 85th percentile → BUY trigger on descent
        memory_window=5,  # 5 bars to remain "armed" after panic
    )

    print(
        f"\n  Config        : window={cfg.percentile_window}, "
        f"panic={cfg.panic_threshold:.0%}, "
        f"signal={cfg.signal_threshold:.0%}, "
        f"memory={cfg.memory_window} bars\n"
    )

    engine = COR3M_Signal_Engine(config=cfg)
    results = engine.run(cor3m_series)

    # --- Print the transition window (last ~50 live bars) ----------------
    live = results[~results["note"].str.contains("WARMUP", na=False)]
    print("  Tail of live bars (last 50):")
    print("  " + "-" * 95)
    print(f"  {'Date':<12} {'COR3M':>7} {'Pct':>7}  {'State':<28} {'Signal':<8} Note")
    print("  " + "-" * 95)
    for ts, row in live.tail(50).iterrows():
        state_short = row["market_state"].replace("SYSTEMIC_PANIC_HOLD", "PANIC")
        state_short = state_short.replace("LONG_LIQUIDITY_RALLY", "BUY_TRIGGER")
        flag = "◄◄◄" if row["signal"] == "BUY" else ""
        print(
            f"  {ts.date()!s:<12} "
            f"{row['cor3m']:>7.2f} "
            f"{row['percentile_rank']:>7.1%}  "
            f"{state_short:<28} "
            f"{row['signal']:<8} "
            f"{flag}"
        )

    # --- Summary ---------------------------------------------------------
    summarise_signals(results)

    # --- Assertion: LONG-ONLY guarantee ----------------------------------
    assert "SHORT" not in results["signal"].values, "LONG-ONLY violation!"
    assert "SELL" not in results["signal"].values, "LONG-ONLY violation!"
    print("  ✓ LONG-ONLY constraint verified — no short signals present.\n")
