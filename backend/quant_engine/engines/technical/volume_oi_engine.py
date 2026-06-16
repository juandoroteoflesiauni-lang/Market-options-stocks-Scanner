from typing import Any
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           OPTIONS MARKET ANALYZER — Volume / OI Dynamics Module             ║
║                                                                              ║
║  Theoretical basis:                                                          ║
║    Agarwal, K. (2024). Option Chain Dynamics: Analysing Open Interest,       ║
║    Trading Volume, and Last Traded Price Relationships.                       ║
║    TURCOMAT, 15(2), 140-146.                                                 ║
║                                                                              ║
║  Core insight from the paper:                                                ║
║    • Rising OI + high volume  → new money entering (institutional entry)     ║
║    • Flat OI   + high volume  → intraday speculation (positions net-zero)    ║
║    • Falling OI + volume      → position liquidation / profit-taking         ║
║    • Low OI Δ  + low volume   → stagnation / exhaustion at that strike       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd


from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration dataclass — all thresholds live here for easy tuning
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class AnalyzerConfig:
    """
    Central configuration for all classification thresholds.

    Percentile-based thresholds (recommended approach):
    ─────────────────────────────────────────────────────
    Instead of hard-coded absolute numbers we derive thresholds from the
    empirical distribution of the dataset being analysed.  This makes the
    classifier self-adapting across instruments with very different liquidity
    profiles (e.g. SPY options vs. a small-cap single name).

    Parameters
    ──────────
    volume_noise_floor : int
        Contracts below this value are flagged as "noise" and skipped.
        Default = 50, which filters micro-lot activity that can distort ratios.

    high_volume_percentile : float  [0–100]
        Percentile of the volume distribution used as the "high volume" cut-off.
        Default = 70 → the top 30 % of volume is considered "high".

    low_volume_percentile : float  [0–100]
        Percentile below which volume is considered "low".
        Default = 30 → the bottom 30 % is considered "low".

    oi_increase_percentile : float  [0–100]
        Percentile of *positive* net OI changes used to define a "significant
        increase".  Default = 60 → top 40 % of OI build-up is "significant".

    oi_decrease_percentile : float  [0–100]
        Percentile of *negative* net OI changes (taken as absolute value) used
        to define a "significant decrease".  Default = 60.

    flat_oi_ratio_threshold : float
        |net_oi_change| / volume below this value → OI is considered "flat"
        (i.e. positions opened and closed within the same session).
        Default = 0.10 → less than 10 % net OI carry-over per unit of volume.
    """

    volume_noise_floor: int = 50
    high_volume_percentile: float = 70.0
    low_volume_percentile: float = 30.0
    oi_increase_percentile: float = 60.0
    oi_decrease_percentile: float = 60.0
    flat_oi_ratio_threshold: float = 0.10


# ──────────────────────────────────────────────────────────────────────────────
# Signal labels (constants so we avoid magic strings throughout the code)
# ──────────────────────────────────────────────────────────────────────────────


class Signal:
    NEW_POSITION = "New Position / Institutional Entry"
    DAY_TRADING = "Day Trading / Speculation"
    PROFIT_TAKING = "Profit Taking / Closing"
    STAGNATION = "Stagnation / Exhaustion"
    NOISE = "Below Noise Floor"
    INDETERMINATE = "Indeterminate"


# ──────────────────────────────────────────────────────────────────────────────
# Main analyser class
# ──────────────────────────────────────────────────────────────────────────────


class OptionsMarketAnalyzer:
    """
    Classifies options contracts by analysing the relationship between daily
    trading Volume and the change in Open Interest (ΔOI).

    Usage
    ─────
        analyzer = OptionsMarketAnalyzer()                  # default config
        result   = analyzer.analyze_volume_oi_dynamics(df)

        # Custom thresholds
        cfg      = AnalyzerConfig(volume_noise_floor=100, high_volume_percentile=75)
        analyzer = OptionsMarketAnalyzer(config=cfg)
        result   = analyzer.analyze_volume_oi_dynamics(df)

    Expected DataFrame columns
    ──────────────────────────
        ticker                 : str   — underlying symbol
        expiration             : date  — contract expiry
        strike                 : float — strike price
        option_type            : str   — "Call" or "Put"
        volume                 : int   — daily traded contracts
        open_interest          : int   — current session OI
        previous_open_interest : int   — prior session OI (may contain NaN)
    """

    REQUIRED_COLUMNS = [
        "ticker",
        "expiration",
        "strike",
        "option_type",
        "volume",
        "open_interest",
        "previous_open_interest",
    ]

    def __init__(self, config: AnalyzerConfig | None = None):
        self.config = config or AnalyzerConfig()
        self._thresholds: dict[str, Any] = {}  # populated at runtime per dataset

    # ── Public entry point ────────────────────────────────────────────────────

    def analyze_volume_oi_dynamics(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Main pipeline.  Returns the original DataFrame enriched with:

            net_oi_change    — ΔOI = open_interest − previous_open_interest
            volume_oi_ratio  — ΔOI / volume  (signed; >0 build-up, <0 unwind)
            signal_type      — one of the four Signal labels (or NOISE)

        Parameters
        ──────────
        df : pd.DataFrame
            Raw options chain data.  See class docstring for required columns.

        Returns
        ───────
        pd.DataFrame  — copy of df with three new columns appended.
        """
        df = self._validate_and_copy(df)
        df = self._engineer_features(df)
        self._compute_thresholds(df)
        df = self._classify(df)
        return df

    # ── Step 1 — Validation ───────────────────────────────────────────────────

    def _validate_and_copy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate schema and return a safe working copy."""
        if not isinstance(df, pd.DataFrame):
            raise TypeError(f"Expected a pandas DataFrame, got {type(df).__name__}.")

        missing = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"Input DataFrame is missing required columns: {missing}")

        if df.empty:
            raise ValueError("Input DataFrame is empty.")

        return df.copy()

    # ── Step 2 — Feature engineering ─────────────────────────────────────────

    def _engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute derived columns used by the classifier.

        net_oi_change
        ─────────────
        ΔOI = current OI − previous OI.
        • Positive → more contracts outstanding (new positions opened net)
        • Negative → fewer contracts outstanding (positions closed net)
        • NaN in previous_open_interest is handled by imputing with current OI
          (implying ΔOI = 0, which is conservative — avoids false signals).

        volume_oi_ratio
        ───────────────
        Ratio = ΔOI / Volume.
        • |ratio| close to 0  → most volume was intraday (opened & closed)
        • |ratio| close to 1  → almost every trade left a new open position
        • |ratio| > 1         → possible OI inherited from prior rollovers
        Volume = 0 is guarded against division-by-zero (→ NaN).
        """
        # ── Handle NaN in previous_open_interest ─────────────────────────────
        nan_mask = df["previous_open_interest"].isna()
        if nan_mask.any():
            n_nan = nan_mask.sum()
            warnings.warn(
                f"{n_nan} row(s) have NaN in 'previous_open_interest'. "
                "Imputing with current 'open_interest' → net_oi_change = 0 "
                "for those rows. Treat their signals with caution.",
                UserWarning,
                stacklevel=3,
            )
            df["previous_open_interest"] = df["previous_open_interest"].fillna(df["open_interest"])

        # ── Ensure numeric dtypes ─────────────────────────────────────────────
        for col in ["volume", "open_interest", "previous_open_interest"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # ── Net OI change ─────────────────────────────────────────────────────
        df["net_oi_change"] = df["open_interest"] - df["previous_open_interest"]

        # ── OI / Volume ratio (signed) ────────────────────────────────────────
        # A ratio near 0 signals pure speculation (day-traded positions).
        # A ratio ≫ 0 signals institutional accumulation.
        # A ratio ≪ 0 signals active liquidation.
        df["volume_oi_ratio"] = np.where(
            df["volume"] > 0,
            df["net_oi_change"] / df["volume"],
            np.nan,
        )

        return df

    # ── Step 3 — Adaptive threshold computation ───────────────────────────────

    def _compute_thresholds(self, df: pd.DataFrame) -> None:
        """
        Derive classification thresholds from the dataset's empirical
        distribution.  Using percentiles means the model auto-calibrates
        to whatever liquidity regime the underlying instrument sits in.

        Thresholds stored in self._thresholds:
            vol_high     — minimum volume to be "high"
            vol_low      — maximum volume to still be "low"
            oi_inc_min   — minimum ΔOI to be a "significant increase"
            oi_dec_min   — minimum |ΔOI| to be a "significant decrease"
        """
        cfg = self.config

        # Work only on rows above the noise floor for percentile computation
        active = df[df["volume"] >= cfg.volume_noise_floor]

        if active.empty:
            warnings.warn(
                "All rows are below the noise floor. "
                "Thresholds will be computed from the full dataset.",
                UserWarning,
                stacklevel=3,
            )
            active = df

        self._thresholds = {
            "vol_high": np.nanpercentile(active["volume"], cfg.high_volume_percentile),
            "vol_low": np.nanpercentile(active["volume"], cfg.low_volume_percentile),
            "oi_inc_min": (
                np.nanpercentile(
                    active.loc[active["net_oi_change"] > 0, "net_oi_change"],
                    cfg.oi_increase_percentile,
                )
                if (active["net_oi_change"] > 0).any()
                else 1
            ),
            "oi_dec_min": (
                np.nanpercentile(
                    active.loc[active["net_oi_change"] < 0, "net_oi_change"].abs(),
                    cfg.oi_decrease_percentile,
                )
                if (active["net_oi_change"] < 0).any()
                else 1
            ),
        }

    # ── Step 4 — Row-level classification ─────────────────────────────────────

    def _classify_row(self, row: pd.Series) -> str:
        """
        Decision tree for a single option contract row.

        Classification logic
        ────────────────────

        STEP A — Noise filter
            If volume < noise_floor → NOISE (skip unreliable micro-lot data)

        STEP B — Volume regime
            high_vol = volume ≥ vol_high  (top 30 % by default)
            low_vol  = volume <  vol_low  (bottom 30 % by default)

        STEP C — OI change regime
            sig_increase = net_oi_change ≥ oi_inc_min  (new longs/shorts added)
            sig_decrease = net_oi_change ≤ −oi_dec_min (positions liquidated)
            flat_oi      = |volume_oi_ratio| < flat_oi_threshold
                           → nearly all volume was intraday (ratio ~ 0)

        STEP D — Map to signal
            high_vol + sig_increase  → NEW POSITION / INSTITUTIONAL ENTRY
            high_vol + flat_oi       → DAY TRADING / SPECULATION
            (high|moderate) + sig_decrease → PROFIT TAKING / CLOSING
            low_vol  + not sig_increase  → STAGNATION / EXHAUSTION
            everything else          → INDETERMINATE
        """
        t = self._thresholds
        cfg = self.config

        vol = row["volume"]
        doi = row["net_oi_change"]
        ratio = row["volume_oi_ratio"]

        # ── A: Noise filter ───────────────────────────────────────────────────
        if vol < cfg.volume_noise_floor:
            return Signal.NOISE

        # ── B: Volume regime flags ────────────────────────────────────────────
        is_high_vol = vol >= t["vol_high"]
        is_low_vol = vol < t["vol_low"]

        # ── C: OI change regime flags ─────────────────────────────────────────
        is_sig_increase = doi >= t["oi_inc_min"]
        is_sig_decrease = doi <= -t["oi_dec_min"]
        is_flat_oi = pd.notna(ratio) and abs(ratio) < cfg.flat_oi_ratio_threshold

        # ── D: Decision tree ──────────────────────────────────────────────────

        # 1. New Position / Institutional Entry
        #    High volume + significant OI build-up → real money committed
        if is_high_vol and is_sig_increase:
            return Signal.NEW_POSITION

        # 2. Day Trading / Speculation
        #    High volume but near-zero net OI → positions opened and closed
        #    within the same session; no net commitment of capital
        if is_high_vol and is_flat_oi:
            return Signal.DAY_TRADING

        # 3. Profit Taking / Closing
        #    Significant OI reduction (regardless of whether volume is high or
        #    moderate) → existing holders are exiting positions
        if is_sig_decrease:
            return Signal.PROFIT_TAKING

        # 4. Stagnation / Exhaustion
        #    Low volume and no meaningful OI change → the market has lost
        #    interest in this particular strike/expiry combination
        if is_low_vol and not is_sig_increase:
            return Signal.STAGNATION

        return Signal.INDETERMINATE

    def _classify(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply _classify_row across all rows."""
        df["signal_type"] = df.apply(self._classify_row, axis=1)
        return df

    # ── Convenience / diagnostics ─────────────────────────────────────────────

    def get_thresholds(self) -> dict[str, Any]:
        """
        Return the thresholds computed for the last dataset analysed.
        Useful for logging, auditing, or dashboard display.
        """
        if not self._thresholds:
            raise RuntimeError(
                "No thresholds computed yet. " "Call analyze_volume_oi_dynamics() first."
            )
        return self._thresholds.copy()

    def summary(self, result_df: pd.DataFrame) -> pd.DataFrame:
        """
        Return a pivot table counting signals by option_type (Call / Put).

        Example output
        ──────────────
        signal_type                          Call   Put
        Day Trading / Speculation              42    38
        New Position / Institutional Entry     17    21
        Profit Taking / Closing                11     9
        Stagnation / Exhaustion                28    33
        """
        return (
            result_df.groupby(["signal_type", "option_type"])
            .size()
            .unstack(fill_value=0)
            .rename_axis(None, axis=1)
        )

    def top_signals(
        self,
        result_df: pd.DataFrame,
        signal: str,
        n: int = 10,
        sort_by: str = "volume",
    ) -> pd.DataFrame:
        """
        Filter rows matching a specific signal and return the top-n by
        a chosen column (default: volume).

        Parameters
        ──────────
        result_df : pd.DataFrame — output of analyze_volume_oi_dynamics()
        signal    : str          — one of the Signal.* constants
        n         : int          — number of rows to return
        sort_by   : str          — column to sort by descending
        """
        subset = result_df[result_df["signal_type"] == signal]
        return subset.sort_values(sort_by, ascending=False).head(n)


# ──────────────────────────────────────────────────────────────────────────────
# Stateless functional API — get_volume_oi_analysis()
# ──────────────────────────────────────────────────────────────────────────────

_DOTM_DELTA_THRESHOLD = 0.20  # |delta| < this → DOTM
_UOA_VOL_OI_THRESHOLD = 3.0  # vol/OI ratio above this is unusual
_UOA_TOP_N = 3  # number of UOA strikes to report
_DOTM_ALERT_PERCENTILE = 80.0  # percentile for dotm_alert vs history


def _get_premium_flow(df: pd.DataFrame, spot: float) -> dict[str, Any]:
    """
    Directional premium flow via Lee-Ready classification.

    Requires columns: option_type, last_price (trade price), bid, ask.
    midpoint = (bid + ask) / 2; price > mid → buyer-initiated.

    Returns flow_signal ∈ (-1, 1]:
        flow_signal = (call_net - put_net) / (|call_net| + |put_net| + 1)
    Positive = net call premium buying (bullish), negative = put buying (bearish).

    If required trade columns are absent, returns flow_signal=None.
    """
    required = {"bid", "ask", "last_price", "option_type"}
    if not required.issubset(df.columns):
        return {
            "call_net_premium": None,
            "put_net_premium": None,
            "flow_signal": None,
        }

    sub = df.dropna(subset=["bid", "ask", "last_price"]).copy()
    if sub.empty:
        return {"call_net_premium": None, "put_net_premium": None, "flow_signal": None}

    sub["_mid"] = (sub["bid"] + sub["ask"]) / 2.0
    sub["_buyer"] = sub["last_price"] > sub["_mid"]
    sub["_sign"] = np.where(sub["_buyer"], 1.0, -1.0)
    premium_col = "last_price"

    otype = sub["option_type"].str.upper()
    call_mask = otype == "CALL"
    put_mask = otype == "PUT"

    call_net = float((sub.loc[call_mask, "_sign"] * sub.loc[call_mask, premium_col]).sum())
    put_net = float((sub.loc[put_mask, "_sign"] * sub.loc[put_mask, premium_col]).sum())
    denom = abs(call_net) + abs(put_net) + 1.0
    signal = float(np.clip((call_net - put_net) / denom, -1.0, 1.0))

    return {
        "call_net_premium": call_net,
        "put_net_premium": put_net,
        "flow_signal": signal,
    }


def _get_dotm_flow(
    df: pd.DataFrame,
    spot: float,
    dotm_ratio_history: list[float] | None = None,
) -> dict[str, Any]:
    """
    DOTM (deep-OTM) put/call OI analysis — institutional hedging indicator.

    Requires columns: option_type, delta, open_interest.
    DOTM defined as |delta| < 0.20.

    dotm_signal ∈ [0, 1]: higher = more bearish institutional hedging.
    dotm_alert: True if dotm_ratio > 80th percentile of provided history.
    """
    required = {"option_type", "delta", "open_interest"}
    if not required.issubset(df.columns):
        return {
            "dotm_put_oi": None,
            "dotm_call_oi": None,
            "dotm_ratio": None,
            "dotm_signal": None,
            "dotm_alert": False,
        }

    sub = df.dropna(subset=["delta", "open_interest"]).copy()
    sub["_abs_delta"] = sub["delta"].abs()
    dotm = sub[sub["_abs_delta"] < _DOTM_DELTA_THRESHOLD]
    otype = dotm["option_type"].str.upper()

    dotm_put_oi = float(dotm.loc[otype == "PUT", "open_interest"].sum())
    dotm_call_oi = float(dotm.loc[otype == "CALL", "open_interest"].sum())
    dotm_ratio = dotm_put_oi / (dotm_call_oi + 1.0)

    # Signal: saturates at ratio = 10 → signal = 1.0
    dotm_signal = float(np.clip(dotm_ratio / 10.0, 0.0, 1.0))

    alert = False
    if dotm_ratio_history and len(dotm_ratio_history) >= 5:
        threshold = float(np.nanpercentile(dotm_ratio_history, _DOTM_ALERT_PERCENTILE))
        alert = bool(dotm_ratio > threshold)

    return {
        "dotm_put_oi": dotm_put_oi,
        "dotm_call_oi": dotm_call_oi,
        "dotm_ratio": dotm_ratio,
        "dotm_signal": dotm_signal,
        "dotm_alert": alert,
    }


def _get_uoa_strikes(df: pd.DataFrame, spot: float) -> list[dict[str, Any]]:
    """
    Unusual Options Activity (UOA) — top N strikes by vol/OI ratio.

    Requires columns: strike, option_type, volume, open_interest.
    Optional columns: last_price (for premium_estimate), delta.

    Returns list of dicts sorted by vol_oi_ratio descending.
    direction_bias: "CALL" or "PUT" per row's option_type.
    """
    required = {"strike", "option_type", "volume", "open_interest"}
    if not required.issubset(df.columns):
        return []

    sub = df.copy()
    sub["_oi_safe"] = sub["open_interest"].clip(lower=1)
    sub["_vol_oi"] = sub["volume"] / sub["_oi_safe"]

    unusual = sub[sub["_vol_oi"] >= _UOA_VOL_OI_THRESHOLD].copy()
    if unusual.empty:
        return []

    top = unusual.nlargest(_UOA_TOP_N, "_vol_oi")

    records = []
    for _, row in top.iterrows():
        price_est = (
            float(row["last_price"])
            if "last_price" in row and pd.notna(row.get("last_price"))
            else None
        )
        records.append(
            {
                "strike": float(row["strike"]),
                "type": str(row["option_type"]).upper(),
                "vol_oi_ratio": round(float(row["_vol_oi"]), 2),
                "premium_estimate": price_est,
                "direction_bias": str(row["option_type"]).upper(),
            }
        )

    return records


def get_volume_oi_analysis(
    options_chain: pd.DataFrame,
    spot: float,
    *,
    analyzer_config: AnalyzerConfig | None = None,
    dotm_ratio_history: list[float] | None = None,
) -> dict[str, Any]:
    """
    Stateless entry point for volume/OI analysis.

    Mandatory columns (passed to OptionsMarketAnalyzer):
        ticker, expiration, strike, option_type, volume,
        open_interest, previous_open_interest

    Optional columns that unlock extra analysis:
        delta       → DOTM flow (dotm_*)
        bid, ask, last_price → premium flow (flow_signal, *_net_premium)

    Parameters
    ──────────
    options_chain       : pd.DataFrame — options snapshot
    spot                : float        — current underlying price
    analyzer_config     : AnalyzerConfig | None — threshold overrides
    dotm_ratio_history  : list[float]  — 30-day dotm_ratio series for alert calibration

    Returns
    ───────
    dict with keys:
        classified_chain  : pd.DataFrame  — input enriched with signal_type, net_oi_change, volume_oi_ratio
        thresholds        : dict          — percentile thresholds used
        summary           : pd.DataFrame  — pivot of signal counts by option_type

        call_net_premium  : float | None
        put_net_premium   : float | None
        flow_signal       : float | None  — Lee-Ready directional signal ∈ (-1, 1]

        dotm_put_oi       : float | None
        dotm_call_oi      : float | None
        dotm_ratio        : float | None
        dotm_signal       : float | None  — ∈ [0, 1]; higher = more bearish hedging
        dotm_alert        : bool

        uoa_strikes       : list[dict[str, Any]]    — top unusual-activity strikes
    """
    if not isinstance(options_chain, pd.DataFrame) or options_chain.empty:
        return {"error_msg": "options_chain must be a non-empty DataFrame"}

    analyzer = OptionsMarketAnalyzer(config=analyzer_config)

    try:
        classified = analyzer.analyze_volume_oi_dynamics(options_chain)
        thresholds = analyzer.get_thresholds()
        summary = analyzer.summary(classified)
    except (ValueError, TypeError) as exc:
        return {"error_msg": str(exc)}

    flow = _get_premium_flow(options_chain, spot)
    dotm = _get_dotm_flow(options_chain, spot, dotm_ratio_history)
    uoa = _get_uoa_strikes(options_chain, spot)

    return {
        "classified_chain": classified,
        "thresholds": thresholds,
        "summary": summary,
        **flow,
        **dotm,
        "uoa_strikes": uoa,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Demo / smoke-test
# ──────────────────────────────────────────────────────────────────────────────


def _build_demo_data() -> pd.DataFrame:
    """
    Synthetic option chain snapshot with edge cases:
    • NaN in previous_open_interest  (row 5)
    • Volume below noise floor       (row 6)
    • A clear institutional entry    (row 0)
    • A clear day-trading row        (row 1)
    • A clear profit-taking row      (row 2)
    • A stagnation row               (row 3)
    """
    data = {
        "ticker": ["AAPL", "AAPL", "TSLA", "MSFT", "SPY", "NVDA", "AMZN"],
        "expiration": ["2024-06-21"] * 7,
        "strike": [190, 195, 250, 420, 530, 880, 185],
        "option_type": ["Call", "Put", "Call", "Put", "Call", "Call", "Put"],
        # ── volume ──────────────────────────────────────────────────────────
        "volume": [15_000, 12_000, 4_500, 300, 8_200, 30, 9_000],
        # ── current OI ──────────────────────────────────────────────────────
        "open_interest": [85_000, 50_000, 18_000, 2_100, 35_000, 1_200, 28_000],
        # ── prior session OI (NaN on row 5 = NVDA) ──────────────────────────
        "previous_open_interest": [72_000, 50_500, 22_000, 2_050, 35_100, np.nan, 32_000],
    }
    return pd.DataFrame(data)


if __name__ == "__main__":
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)

    logger.info("%s", "=" * 70)
    logger.info("  OptionsMarketAnalyzer demo run")
    logger.info("%s", "=" * 70)

    # ── Build synthetic data ──────────────────────────────────────────────────
    df_raw = _build_demo_data()
    logger.info("[INPUT]\n%s", df_raw.to_string(index=False))

    # ── Run the analyser ──────────────────────────────────────────────────────
    cfg = AnalyzerConfig(volume_noise_floor=50, high_volume_percentile=65)
    analyzer = OptionsMarketAnalyzer(config=cfg)
    result = analyzer.analyze_volume_oi_dynamics(df_raw)

    # ── Show enriched DataFrame ───────────────────────────────────────────────
    output_cols = [
        "ticker",
        "option_type",
        "strike",
        "volume",
        "net_oi_change",
        "volume_oi_ratio",
        "signal_type",
    ]
    logger.info("[RESULT]\n%s", result[output_cols].to_string(index=False))

    # ── Show computed thresholds ──────────────────────────────────────────────
    logger.info("[COMPUTED THRESHOLDS]")
    for k, v in analyzer.get_thresholds().items():
        logger.info("  %15s : %s", k, f"{v:,.1f}")

    # ── Signal summary ────────────────────────────────────────────────────────
    logger.info("[SIGNAL SUMMARY BY OPTION TYPE]\n%s", analyzer.summary(result))

    # ── Top New-Position signals by volume ────────────────────────────────────
    logger.info(
        "[TOP 'New Position' SIGNALS]\n%s",
        analyzer.top_signals(result, Signal.NEW_POSITION, n=5)[output_cols].to_string(index=False),
    )
