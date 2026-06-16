"""
QuantumBeta Terminal — DeltaWeightedFlow_Engine
================================================
Module for real-time and historical Delta-Weighted Premium Flow analysis.
Detects institutional capitulation events ("Mechanical Floors") through
statistical anomaly detection on options order flow.

Mathematical Basis
------------------
Premium Flow  = Volume × MarkPrice × ContractMultiplier
DW Flow       = Premium Flow × |Delta|
PC Ratio      = Total_Put_Flow / Total_Call_Flow
Z-Score       = (PC_Ratio_t − μ_rolling) / σ_rolling

Signal Logic (LONG-ONLY)
------------------------
Z > +3σ  → EXHAUSTION_WARNING   (institutional panic selling in progress)
Z ≤ +1σ  (after prior warning)  → LONG_SETUP_TRIGGER  (forced selling exhausted)
Call spike                       → HOLD_STATE          (no action)

Author : QuantumBeta Quant Engineering
Version: 1.0.0
"""

from __future__ import annotations

import warnings
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]

# ---------------------------------------------------------------------------
# Public Constants
# ---------------------------------------------------------------------------
CONTRACT_MULTIPLIER: int = 100  # Standard equity options multiplier
DEFAULT_ROLLING_WINDOW: int = 20  # Periods for rolling statistics
PANIC_THRESHOLD: float = 3.0  # σ above mean → EXHAUSTION_WARNING
RESET_THRESHOLD: float = 1.0  # σ below this (post-panic) → TRIGGER


# ---------------------------------------------------------------------------
# Signal Enum
# ---------------------------------------------------------------------------
class MarketSignal(Enum):
    """Enumeration of all possible engine output signals.

    Only long-side signals are defined by design. Short signals are
    explicitly excluded from this system.
    """

    NEUTRAL = auto()  # Not enough data or no anomaly detected
    HOLD_STATE = auto()  # Call-flow spike; no actionable edge
    EXHAUSTION_WARNING = auto()  # Z-Score > +3σ — panic selling in progress
    LONG_SETUP_TRIGGER = auto()  # Z-Score fell ≤ +1σ after WARNING — buy window


# ---------------------------------------------------------------------------
# Snapshot Result Dataclass
# ---------------------------------------------------------------------------
@dataclass
class FlowSnapshot:
    """Immutable result object returned after processing one options snapshot.

    Attributes
    ----------
    total_call_flow : float
        Aggregate delta-weighted premium flow across all call strikes (USD).
    total_put_flow : float
        Aggregate delta-weighted premium flow across all put strikes (USD).
    pc_flow_ratio : float
        Put/Call flow ratio for this snapshot.
    z_score : Optional[float]
        Z-Score of the current ratio against the rolling window.
        None when the window has fewer than 2 observations.
    signal : MarketSignal
        The engine's current state classification.
    rolling_mean : Optional[float]
        Rolling mean of the PC ratio (diagnostic).
    rolling_std : Optional[float]
        Rolling standard deviation of the PC ratio (diagnostic).
    raw_df : pd.DataFrame
        The enriched DataFrame (with computed flow columns) for audit trails.
    """

    total_call_flow: float
    total_put_flow: float
    pc_flow_ratio: float
    z_score: float | None
    signal: MarketSignal
    rolling_mean: float | None
    rolling_std: float | None
    raw_df: pd.DataFrame = field(repr=False)

    def __str__(self) -> str:
        z_str = f"{self.z_score:+.3f}σ" if self.z_score is not None else "N/A"
        return (
            f"┌─ FlowSnapshot ─────────────────────────────\n"
            f"│  Call Flow : ${self.total_call_flow:>15,.2f}\n"
            f"│  Put  Flow : ${self.total_put_flow:>15,.2f}\n"
            f"│  PC  Ratio : {self.pc_flow_ratio:>15.4f}\n"
            f"│  Z-Score   : {z_str:>15}\n"
            f"│  Signal    : {self.signal.name:>15}\n"
            f"└────────────────────────────────────────────"
        )


# ---------------------------------------------------------------------------
# Core Engine
# ---------------------------------------------------------------------------
class DeltaWeightedFlow_Engine:
    """Calculates Delta-Weighted Premium Flow and detects institutional
    capitulation events for the QuantumBeta terminal.

    This engine is **LONG-ONLY** by design. It will never emit, imply, or
    store any short-side trade instruction.

    Parameters
    ----------
    contract_multiplier : int
        Number of shares per contract. Default is 100 (standard equity).
    rolling_window : int
        Number of past snapshots used for rolling μ / σ computation.
    panic_threshold : float
        Z-Score level above which EXHAUSTION_WARNING is set. Default 3.0.
    reset_threshold : float
        Z-Score level below which (after a prior warning) LONG_SETUP_TRIGGER
        is emitted. Default 1.0.

    Examples
    --------
    >>> engine = DeltaWeightedFlow_Engine()
    >>> snapshot = engine.process_snapshot(options_df)
    >>> print(snapshot.signal)
    """

    # Required columns in the input DataFrame
    _REQUIRED_COLS: frozenset[str] = frozenset(["strike", "type", "volume", "mark_price", "delta"])

    def __init__(
        self,
        contract_multiplier: int = CONTRACT_MULTIPLIER,
        rolling_window: int = DEFAULT_ROLLING_WINDOW,
        panic_threshold: float = PANIC_THRESHOLD,
        reset_threshold: float = RESET_THRESHOLD,
    ) -> None:
        self.contract_multiplier: int = contract_multiplier
        self.rolling_window: int = rolling_window
        self.panic_threshold: float = panic_threshold
        self.reset_threshold: float = reset_threshold

        # Rolling history of PC ratios (bounded deque for O(1) append/pop)
        self._ratio_history: deque[float] = deque(maxlen=rolling_window)

        # State machine flag: True while Z-Score is above panic threshold
        self._in_exhaustion_phase: bool = False

        # Internal snapshot counter (diagnostics / logging)
        self._snapshot_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_snapshot(self, option_chain: pd.DataFrame) -> FlowSnapshot:
        """Process one snapshot of an option chain and return a FlowSnapshot.

        Parameters
        ----------
        option_chain : pd.DataFrame
            Must contain columns: strike, type ('call'/'put'),
            volume, mark_price, delta.
            Delta for puts should be negative (standard Black-Scholes
            convention) or positive — the engine uses abs(delta).

        Returns
        -------
        FlowSnapshot
            Fully populated result object including the current MarketSignal.

        Raises
        ------
        ValueError
            If required columns are missing or if 'type' contains unknown values.
        """
        self._validate_dataframe(option_chain)
        enriched_df = self._compute_flows(option_chain.copy())

        call_flow, put_flow = self._aggregate_flows(enriched_df)
        pc_ratio = self._compute_pc_ratio(call_flow, put_flow)

        # Append to rolling window before computing Z-Score
        self._ratio_history.append(pc_ratio)

        z_score, roll_mean, roll_std = self._compute_zscore()
        signal = self._classify_signal(z_score, call_flow, put_flow)

        self._snapshot_count += 1

        return FlowSnapshot(
            total_call_flow=call_flow,
            total_put_flow=put_flow,
            pc_flow_ratio=pc_ratio,
            z_score=z_score,
            signal=signal,
            rolling_mean=roll_mean,
            rolling_std=roll_std,
            raw_df=enriched_df,
        )

    def process_historical(self, snapshots: list[pd.DataFrame]) -> list[FlowSnapshot]:
        """Process an ordered list of historical option-chain snapshots.

        Parameters
        ----------
        snapshots : list[pd.DataFrame]
            Chronologically ordered list of option chain DataFrames.

        Returns
        -------
        list[FlowSnapshot]
            One FlowSnapshot per input DataFrame, in the same order.
        """
        self.reset()
        return [self.process_snapshot(df) for df in snapshots]

    def reset(self) -> None:
        """Reset internal rolling state. Call before replaying historical data."""
        self._ratio_history.clear()
        self._in_exhaustion_phase = False
        self._snapshot_count = 0

    @property
    def snapshot_count(self) -> int:
        """Total number of snapshots processed since last reset."""
        return self._snapshot_count

    @property
    def ratio_history(self) -> list[float]:
        """Read-only copy of the current rolling PC-ratio window."""
        return list(self._ratio_history)

    # ------------------------------------------------------------------
    # Private — Validation
    # ------------------------------------------------------------------

    def _validate_dataframe(self, df: pd.DataFrame) -> None:
        """Raise ValueError if the DataFrame is malformed."""
        missing = self._REQUIRED_COLS - set(df.columns)
        if missing:
            raise ValueError(f"option_chain is missing required columns: {sorted(missing)}")
        if df.empty:
            raise ValueError("option_chain DataFrame is empty.")

        # Normalise 'type' column to lowercase for safety
        df["type"] = df["type"].str.lower().str.strip()
        unknown_types = set(df["type"].unique()) - {"call", "put"}
        if unknown_types:
            raise ValueError(
                f"'type' column contains unknown values: {unknown_types}. "
                "Accepted: 'call', 'put'."
            )

        # Guard against negative premiums or volumes
        if (df["mark_price"] < 0).any():
            raise ValueError("mark_price contains negative values.")
        if (df["volume"] < 0).any():
            raise ValueError("volume contains negative values.")

    # ------------------------------------------------------------------
    # Private — Flow Calculations (fully vectorised — no row-level loops)
    # ------------------------------------------------------------------

    def _compute_flows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Enrich the DataFrame with premium_flow and dw_flow columns.

        Operations are fully vectorised using Pandas/NumPy; no Python-level
        row iteration is used.

        Formula
        -------
        premium_flow = volume × mark_price × contract_multiplier
        dw_flow      = premium_flow × |delta|
        """
        df["premium_flow"] = (
            df["volume"].astype(np.float64)
            * df["mark_price"].astype(np.float64)
            * self.contract_multiplier
        )
        df["dw_flow"] = df["premium_flow"] * df["delta"].abs()
        return df

    def _aggregate_flows(self, enriched_df: pd.DataFrame) -> tuple[float, float]:
        """Sum delta-weighted flows separately for calls and puts.

        Returns
        -------
        tuple[float, float]
            (total_call_flow, total_put_flow) in notional USD.
        """
        # Boolean masks — vectorised, no loops
        call_mask = enriched_df["type"] == "call"
        put_mask = enriched_df["type"] == "put"

        total_call_flow: float = enriched_df.loc[call_mask, "dw_flow"].sum()
        total_put_flow: float = enriched_df.loc[put_mask, "dw_flow"].sum()
        return total_call_flow, total_put_flow

    @staticmethod
    def _compute_pc_ratio(call_flow: float, put_flow: float) -> float:
        """Compute Put/Call Flow Ratio with zero-division guard.

        When call_flow is zero, returns np.inf to indicate extreme
        put-side dominance (which will yield a very high Z-Score).
        """
        if call_flow == 0.0:
            warnings.warn(
                "Call flow is zero. PC ratio set to np.inf.",
                RuntimeWarning,
                stacklevel=3,
            )
            return np.inf
        return put_flow / call_flow

    # ------------------------------------------------------------------
    # Private — Statistical Anomaly Detection
    # ------------------------------------------------------------------

    def _compute_zscore(
        self,
    ) -> tuple[float | None, float | None, float | None]:
        """Compute the Z-Score of the latest PC ratio against the rolling window.

        Returns
        -------
        tuple[Optional[float], Optional[float], Optional[float]]
            (z_score, rolling_mean, rolling_std)
            All three are None when fewer than 2 observations exist in the window.
        """
        n = len(self._ratio_history)
        if n < 2:
            return None, None, None

        history_arr = np.array(self._ratio_history, dtype=np.float64)

        # Exclude np.inf from statistics to avoid NaN contamination
        finite_mask = np.isfinite(history_arr)
        if finite_mask.sum() < 2:
            return None, None, None

        roll_mean: float = history_arr[finite_mask].mean()
        roll_std: float = history_arr[finite_mask].std(ddof=1)

        if roll_std == 0.0:
            return 0.0, roll_mean, roll_std

        current_ratio = self._ratio_history[-1]
        if not np.isfinite(current_ratio):
            # Infinite ratio → extreme positive Z-Score
            z_score = self.panic_threshold + 1.0
        else:
            z_score = (current_ratio - roll_mean) / roll_std

        return z_score, roll_mean, roll_std

    # ------------------------------------------------------------------
    # Private — Signal Classification (LONG-ONLY state machine)
    # ------------------------------------------------------------------

    def _classify_signal(
        self,
        z_score: float | None,
        call_flow: float,
        put_flow: float,
    ) -> MarketSignal:
        """Apply the LONG-ONLY state machine to produce a MarketSignal.

        State Machine
        -------------
        1. No Z-Score available yet         → NEUTRAL
        2. Z  > panic_threshold             → EXHAUSTION_WARNING
                                              + set _in_exhaustion_phase = True
        3. _in_exhaustion_phase AND Z ≤ 1σ  → LONG_SETUP_TRIGGER
                                              + clear _in_exhaustion_phase
        4. Call flow dominates (put < call) → HOLD_STATE
        5. Default                          → NEUTRAL

        NOTE: Short signals are **never** emitted. Call spikes → HOLD_STATE.
        """
        if z_score is None:
            return MarketSignal.NEUTRAL

        # ── Panic phase entry ──────────────────────────────────────────
        if z_score > self.panic_threshold:
            self._in_exhaustion_phase = True
            return MarketSignal.EXHAUSTION_WARNING

        # ── Trigger: exhaustion absorbed by the market ─────────────────
        if self._in_exhaustion_phase and z_score <= self.reset_threshold:
            self._in_exhaustion_phase = False
            return MarketSignal.LONG_SETUP_TRIGGER

        # ── Call-flow spike: no short edge, just hold ──────────────────
        if call_flow > put_flow:
            return MarketSignal.HOLD_STATE

        return MarketSignal.NEUTRAL


# ===========================================================================
# Demo / Smoke-Test
# ===========================================================================
if __name__ == "__main__":
    import random

    rng = random.Random(42)

    def _make_option_chain(
        spot: float,
        put_pressure_multiplier: float = 1.0,
    ) -> pd.DataFrame:
        """Generate a synthetic SPX-like option chain for testing."""
        strikes = list(range(int(spot * 0.85), int(spot * 1.15), 5))
        rows = []
        for k in strikes:
            moneyness = k / spot
            # --- Call row ---
            call_delta = max(0.01, min(0.99, 1.0 - moneyness + 0.5))
            rows.append(
                {
                    "strike": k,
                    "type": "call",
                    "volume": rng.randint(50, 800),
                    "mark_price": max(0.5, (spot - k + 20) * max(0.01, call_delta)),
                    "delta": round(call_delta, 4),
                }
            )
            # --- Put row ---
            put_delta = -(1.0 - call_delta)
            put_vol = int(rng.randint(50, 800) * put_pressure_multiplier)
            rows.append(
                {
                    "strike": k,
                    "type": "put",
                    "volume": put_vol,
                    "mark_price": max(0.5, (k - spot + 20) * max(0.01, abs(put_delta))),
                    "delta": round(put_delta, 4),
                }
            )
        return pd.DataFrame(rows)

    print("\n" + "=" * 60)
    print("  QuantumBeta — DeltaWeightedFlow_Engine Demo")
    print("=" * 60)

    engine = DeltaWeightedFlow_Engine(
        rolling_window=20,
        panic_threshold=3.0,
        reset_threshold=1.0,
    )

    SPOT = 5_400.0
    snapshots_data: list[tuple[int, str, float]] = (
        # (period, label, put_pressure)
        [(i, "NORMAL", 1.0) for i in range(1, 16)]  # 15 calm periods
        + [(i, "PANIC", 6.5) for i in range(16, 22)]  # 6 panic periods
        + [(i, "EXHAUSTION", 1.2) for i in range(22, 28)]  # 6 recovery periods
    )

    print(
        f"\n{'Period':<8} {'Label':<12} {'Put Flow':>14} {'Call Flow':>14} "
        f"{'PC Ratio':>10} {'Z-Score':>9} {'Signal'}"
    )
    print("-" * 80)

    last_trigger_snapshot: FlowSnapshot | None = None

    for period, label, put_mult in snapshots_data:
        df_chain = _make_option_chain(SPOT, put_pressure_multiplier=put_mult)
        snapshot = engine.process_snapshot(df_chain)
        z_str = f"{snapshot.z_score:+.2f}σ" if snapshot.z_score is not None else "  N/A "
        sig_name = snapshot.signal.name

        # Highlight critical signals
        marker = ""
        if snapshot.signal == MarketSignal.EXHAUSTION_WARNING:
            marker = " ⚠️"
        elif snapshot.signal == MarketSignal.LONG_SETUP_TRIGGER:
            marker = " 🎯 ← LONG ENTRY"
            last_trigger_snapshot = snapshot

        print(
            f"{period:<8} {label:<12} "
            f"${snapshot.total_put_flow:>13,.0f} "
            f"${snapshot.total_call_flow:>13,.0f} "
            f"{snapshot.pc_flow_ratio:>10.3f} "
            f"{z_str:>9} "
            f"{sig_name}{marker}"
        )

    print("\n" + "=" * 60)
    if last_trigger_snapshot:
        print("\n✅ LONG_SETUP_TRIGGER Captured — Final Snapshot Detail:")
        print(last_trigger_snapshot)
    else:
        print("No LONG_SETUP_TRIGGER fired in this simulation run.")

    print(f"\n📊 Rolling PC-Ratio History (last {engine.rolling_window} periods):")
    history = engine.ratio_history
    arr = np.array(history)
    finite = arr[np.isfinite(arr)]
    if len(finite):
        print(
            f"   μ = {finite.mean():.4f}  |  σ = {finite.std(ddof=1):.4f}"
            f"  |  min = {finite.min():.4f}  |  max = {finite.max():.4f}"
        )
    print(f"   Total snapshots processed: {engine.snapshot_count}")
    print("=" * 60 + "\n")
