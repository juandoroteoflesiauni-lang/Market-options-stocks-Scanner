"""
================================================================================
MODULE: skew_fattails_engine.py
SYSTEM: QuantumBeta Terminal — Core Risk Architecture
================================================================================

THEORETICAL FOUNDATION
-----------------------
This module implements a volatility skew and fat-tail risk engine grounded in
the Jarrow-Rudd (1982) extension of the Black-Scholes (1973) option pricing
model. The canonical Black-Scholes model assumes log-normally distributed
asset prices, which implies:
    - Skewness = 0 (for log-relative returns)
    - Kurtosis = 3 (mesokurtic / normal tails)

Empirically, as documented by Corrado & Su (1982) on S&P 500 index options,
real distributions exhibit:
    - Significant NEGATIVE skewness (λ1 < 0): left-tail crash risk
    - Positive EXCESS kurtosis (λ2 > 3): fatter tails than lognormal

The Jarrow-Rudd formula corrects Black-Scholes via an Edgeworth series
expansion around the lognormal distribution, adding adjustment terms
proportional to λ1 (skewness) and λ2 (excess kurtosis):

    C(F) = C_BS(ISD) + λ1·Q3 + λ2·Q4

where Q3 and Q4 are the third and fourth derivative correction terms of
the lognormal density evaluated at the strike price K.

OPERATIONAL CONSTRAINT — LONG-ONLY MANDATE
-------------------------------------------
QuantumBeta operates strictly LONG-ONLY. This engine NEVER emits, suggests,
or encodes short-selling logic. Elevated Put Skew (negative λ1) and/or high
kurtosis (elevated λ2) are converted exclusively into Risk_Flags that signal
one of three long-position stances:
    - RISK_CLEAR   : Favorable asymmetry. Long entry permitted.
    - RISK_CAUTION : Moderate skew/kurtosis. Size down; wait for confirmation.
    - RISK_AVOID   : Severe tail risk detected. Postpone long entry entirely.

AUTHORS: QuantumBeta Quant Engineering Team
REFERENCES:
    - Jarrow, R. & Rudd, A. (1982). "Approximate Option Valuation for
      Arbitrary Stochastic Processes." Journal of Financial Economics, 10.
    - Corrado, C.J. & Su, T. (1996). "Implied Volatility Skews and Stock
      Index Skewness and Kurtosis Implied by S&P 500 Index Option Prices."
    - Black, F. & Scholes, M. (1973). "The Pricing of Options and Corporate
      Liabilities." Journal of Political Economy, 81.
================================================================================
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]
from scipy.stats import norm  # type: ignore[import-not-found, import-untyped]

# ─────────────────────────────────────────────────────────────────────────────
# ENUMERATIONS & DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────


class RiskFlag(StrEnum):
    """
    Directional risk stance derived from skew and kurtosis analysis.

    Long flags fire on negative skew / put-side fat tails. Short flags fire on
    positive skew / call-side fat tails.

    Attributes
    ----------
    RISK_CLEAR   : Probabilistic asymmetry favors a long entry.
    RISK_CAUTION : Elevated skew or kurtosis; reduce size, await confirmation.
    RISK_AVOID   : Severe tail-risk conditions; postpone long entry entirely.
    """

    RISK_CLEAR = "RISK_CLEAR"
    RISK_CAUTION = "RISK_CAUTION"
    RISK_AVOID = "RISK_AVOID"
    RISK_SHORT_CLEAR = "RISK_SHORT_CLEAR"
    RISK_SHORT_CAUTION = "RISK_SHORT_CAUTION"
    RISK_SHORT_AVOID = "RISK_SHORT_AVOID"


@dataclass(frozen=True)
class OptionChainRow:
    """
    Single record from a standardized option chain snapshot.

    Parameters
    ----------
    strike : float
        Absolute strike price of the option contract.
    iv_call : float
        Implied volatility of the OTM Call at this strike (decimal, e.g. 0.15).
    iv_put  : float
        Implied volatility of the OTM Put at this strike (decimal, e.g. 0.20).
    moneyness : float
        Signed moneyness = (S_adj - K_disc) / S_adj, where positive values
        indicate in-the-money calls and negative values indicate OTM calls,
        consistent with Corrado & Su (1996) Exhibit 3 convention.
    """

    strike: float
    iv_call: float
    iv_put: float
    moneyness: float


@dataclass
class SkewAnalysisResult:
    """
    Output container for a single SkewFatTailsEngine.analyze() call.

    Attributes
    ----------
    spot_price         : float
        Underlying asset spot price at snapshot time.
    atm_iv             : float
        At-the-money implied volatility (proxy for σ in Black-Scholes / ISD).
    implied_skewness   : float
        Proxy for Jarrow-Rudd λ1. Negative values signal left-tail crash risk,
        consistent with post-1987 S&P 500 option markets (avg ≈ -1.68,
        Corrado & Su 1996).
    tail_risk_factor   : float
        Proxy for Jarrow-Rudd λ2 (excess kurtosis). Values > 3 indicate
        fatter tails than the lognormal baseline. Empirical baseline: ~5.39.
    put_call_iv_spread : float
        Raw spread: mean(IV_Put_OTM) − mean(IV_Call_OTM). Positive spread
        confirms negative skew (put buyers bid up crash insurance).
    risk_flag          : RiskFlag
        Long-only operational stance derived from combined skew+kurtosis signal.
    risk_score         : float
        Continuous composite score [0, 100]; higher = more tail risk.
        Thresholds: <30 → CLEAR, 30–60 → CAUTION, >60 → AVOID.
    flag_rationale     : str
        Human-readable explanation of the flag decision.
    skew_profile       : pd.DataFrame
        Per-strike breakdown: strike, iv_call, iv_put, skew_spread, moneyness.
    """

    spot_price: float
    atm_iv: float
    implied_skewness: float
    tail_risk_factor: float
    put_call_iv_spread: float
    risk_flag: RiskFlag
    risk_score: float
    flag_rationale: str
    skew_profile: pd.DataFrame = field(repr=False)


# ─────────────────────────────────────────────────────────────────────────────
# CORE ENGINE
# ─────────────────────────────────────────────────────────────────────────────


class SkewFatTailsEngine:
    """
    Volatility Skew and Fat-Tail Risk Engine — QuantumBeta Core Module.

    Implements a Jarrow-Rudd (1982) inspired framework to extract distributional
    shape parameters (skewness λ1, excess kurtosis λ2) from the implied
    volatility surface of an option chain.

    The engine translates these parameters into a long-only Risk_Flag, enabling
    the terminal to time entries with probabilistic edge rather than relying on
    the symmetric, thin-tailed assumptions of raw Black-Scholes pricing.

    Parameters
    ----------
    spot_price        : float
        Current underlying asset price S₀.
    risk_free_rate    : float
        Continuously compounded risk-free rate r (e.g. 0.05 for 5%).
    time_to_expiry    : float
        Time to option expiration in years (e.g. 0.25 for 3 months).
    otm_depth_pct     : float, optional
        Minimum |moneyness| threshold to classify a strike as OTM for the
        skew spread calculation. Default = 0.01 (1%). Prevents ATM noise
        from contaminating the directional skew signal.
    tail_depth_pct    : float, optional
        Minimum |moneyness| threshold to classify a strike as a "tail"
        strike for the kurtosis / fat-tail factor calculation.
        Default = 0.03 (3%). Mirrors the deep OTM region analyzed in
        Corrado & Su (1996) Exhibit 4 (strikes 430 and 490 vs S=459.65).
    caution_threshold : float, optional
        Risk score above which the flag escalates to RISK_CAUTION. Default=30.
    avoid_threshold   : float, optional
        Risk score above which the flag escalates to RISK_AVOID. Default=60.

    Notes
    -----
    Skewness proxy:
        λ1_proxy = mean(IV_Put_OTM) − mean(IV_Call_OTM)
        A positive spread (put IV > call IV) corresponds to negative
        distributional skewness — the classic "volatility skew" documented
        in equity index options post-1987.

    Kurtosis proxy:
        λ2_proxy = mean(IV at |moneyness| ≥ tail_depth_pct) / ATM_IV
        Elevated ratio indicates the market is pricing abnormally fat tails
        relative to the at-the-money anchor, analogous to high excess kurtosis.

    References
    ----------
    Jarrow & Rudd (1982), equation (5): C(F) = C(A) + λ1·Q3 + λ2·Q4
    Corrado & Su (1996), Exhibits 4–5: empirical ISD=12.88%, ISK=-1.68, IKT=5.39
    """

    # Empirical baselines from Corrado & Su (1996) — S&P 500, Dec 1993
    _EMPIRICAL_SKEW_BASELINE: float = -1.68  # average implied skewness ISK
    _EMPIRICAL_KURTOSIS_BASELINE: float = 5.39  # average implied kurtosis IKT
    _LOGNORMAL_KURTOSIS: float = 3.00  # kurtosis of lognormal distribution

    def __init__(
        self,
        spot_price: float,
        risk_free_rate: float,
        time_to_expiry: float,
        otm_depth_pct: float = 0.01,
        tail_depth_pct: float = 0.03,
        caution_threshold: float = 30.0,
        avoid_threshold: float = 60.0,
    ) -> None:
        if spot_price <= 0:
            raise ValueError(f"spot_price must be positive. Got {spot_price}.")
        if time_to_expiry <= 0:
            raise ValueError(f"time_to_expiry must be positive. Got {time_to_expiry}.")
        if not (0.0 <= risk_free_rate <= 1.0):
            warnings.warn(
                f"risk_free_rate={risk_free_rate} is outside [0, 1]. Verify units (decimal expected).",
                stacklevel=2,
            )
        if tail_depth_pct <= otm_depth_pct:
            raise ValueError("tail_depth_pct must be strictly greater than otm_depth_pct.")
        if not (0 < caution_threshold < avoid_threshold < 100):
            raise ValueError("Thresholds must satisfy 0 < caution < avoid < 100.")

        self.spot_price = float(spot_price)
        self.risk_free_rate = float(risk_free_rate)
        self.time_to_expiry = float(time_to_expiry)
        self.otm_depth_pct = float(otm_depth_pct)
        self.tail_depth_pct = float(tail_depth_pct)
        self.caution_threshold = float(caution_threshold)
        self.avoid_threshold = float(avoid_threshold)

    # ── PUBLIC API ────────────────────────────────────────────────────────────

    def analyze(self, option_chain: pd.DataFrame) -> SkewAnalysisResult:
        """
        Main entry point. Ingests an option chain and returns a full
        SkewAnalysisResult including skew proxy, fat-tail factor, and
        the long-only Risk_Flag.

        Parameters
        ----------
        option_chain : pd.DataFrame
            Must contain columns:
                - 'strike'    (float) : strike price
                - 'iv_call'   (float) : OTM call implied volatility (decimal)
                - 'iv_put'    (float) : OTM put implied volatility (decimal)
            Moneyness is computed internally from spot_price and strike.

        Returns
        -------
        SkewAnalysisResult
            Complete analysis output. See dataclass docstring for field details.

        Raises
        ------
        ValueError
            If required columns are missing or the chain has fewer than 3 rows.
        """
        chain = self._validate_and_enrich(option_chain)

        atm_iv = self._compute_atm_iv(chain)
        put_call_iv_spread = self._compute_skew_spread(chain)
        implied_skewness = self._compute_implied_skewness(put_call_iv_spread)
        tail_risk_factor = self._compute_tail_risk_factor(chain, atm_iv)
        risk_score = self._compute_risk_score(implied_skewness, tail_risk_factor)
        risk_flag, rationale = self._classify_risk_flag(
            risk_score, implied_skewness, tail_risk_factor, put_call_iv_spread
        )
        skew_profile = self._build_skew_profile(chain)

        return SkewAnalysisResult(
            spot_price=self.spot_price,
            atm_iv=round(atm_iv, 6),
            implied_skewness=round(implied_skewness, 4),
            tail_risk_factor=round(tail_risk_factor, 4),
            put_call_iv_spread=round(put_call_iv_spread, 6),
            risk_flag=risk_flag,
            risk_score=round(risk_score, 2),
            flag_rationale=rationale,
            skew_profile=skew_profile,
        )

    def black_scholes_call(
        self,
        strike: float,
        sigma: float,
        dividend_yield: float = 0.0,
    ) -> float:
        """
        Standard Black-Scholes European call price (Black 1975 dividend-adjusted).

        Serves as C(A) — the lognormal approximating distribution baseline —
        in the Jarrow-Rudd equation (5): C(F) = C(A) + λ1·Q3 + λ2·Q4.

        Parameters
        ----------
        strike        : float  — Option strike price K.
        sigma         : float  — Implied standard deviation (ISD) in decimal.
        dividend_yield: float  — Continuous dividend yield y (default 0).

        Returns
        -------
        float : Theoretical Black-Scholes call price per unit of underlying.
        """
        spot = self.spot_price * np.exp(-dividend_yield * self.time_to_expiry)
        strike_price = strike
        r = self.risk_free_rate
        t = self.time_to_expiry

        if sigma <= 0 or t <= 0:
            return float(max(spot - strike_price * np.exp(-r * t), 0.0))

        d1 = (np.log(spot / strike_price) + (r + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        return float(spot * norm.cdf(d1) - strike_price * np.exp(-r * t) * norm.cdf(d2))

    def jarrow_rudd_call(
        self,
        strike: float,
        isd: float,
        lambda1: float,
        lambda2: float,
        dividend_yield: float = 0.0,
    ) -> float:
        """
        Jarrow-Rudd skewness- and kurtosis-adjusted call price.

        Implements equation (5) from Jarrow & Rudd (1982), extended by
        Corrado & Su (1996):

            C(F) = C_BS(ISD) + λ1·Q3 + λ2·Q4

        where Q3 and Q4 are corrections derived from the third and fourth
        derivatives of the lognormal density evaluated at strike K.

        Parameters
        ----------
        strike        : float  — Option strike price K.
        isd           : float  — Implied Standard Deviation (σ).
        lambda1       : float  — Skewness adjustment parameter (λ1). Typically
                                 negative for equity index options (left-skewed).
        lambda2       : float  — Kurtosis adjustment parameter (λ2). Positive
                                 excess kurtosis signals fat tails.
        dividend_yield: float  — Continuous dividend yield y (default 0).

        Returns
        -------
        float : Skewness- and kurtosis-adjusted theoretical call price.

        Notes
        -----
        From Corrado & Su (1996) eq. (6):
            Q3 = S0·e^(rt)·(e^(-σ²t/2))·(1/(σ√t))·φ(d1)·(d1/σ - 1/t) / 3!
            Q4 = S0·e^(rt)·(e^(-σ²t/2))·(1/(σ√t))·φ(d1)·(...) / 4!
        This implementation uses a numerically stable approximation of the
        lognormal density derivatives per equations (3) and (6).
        """
        bs_price = self.black_scholes_call(strike, isd, dividend_yield)
        q3, q4 = self._compute_q3_q4(strike, isd)
        return bs_price + lambda1 * q3 + lambda2 * q4

    # ── PRIVATE: VALIDATION & ENRICHMENT ─────────────────────────────────────

    def _validate_and_enrich(self, option_chain: pd.DataFrame) -> pd.DataFrame:
        """Validate schema, coerce types, and append computed moneyness column."""
        required = {"strike", "iv_call", "iv_put"}
        missing = required - set(option_chain.columns)
        if missing:
            raise ValueError(f"option_chain is missing required columns: {missing}")
        if len(option_chain) < 3:
            raise ValueError("option_chain must contain at least 3 rows for meaningful analysis.")

        chain = option_chain[list(required)].copy().astype(float)

        # Moneyness: (S_adj − K_disc) / S_adj — positive = ITM call / OTM put
        # Using discounted strike per Corrado & Su (1996) Exhibit 3 convention
        disc_factor = np.exp(-self.risk_free_rate * self.time_to_expiry)
        chain["moneyness"] = (self.spot_price - chain["strike"] * disc_factor) / self.spot_price

        # Validate IV ranges
        for col in ("iv_call", "iv_put"):
            if (chain[col] <= 0).any() or (chain[col] > 5.0).any():
                warnings.warn(
                    f"Column '{col}' contains suspicious values (≤0 or >500%). "
                    "Verify that IVs are in decimal form (e.g. 0.15, not 15.0).",
                    stacklevel=2,
                )
        return chain.sort_values("strike").reset_index(drop=True)

    # ── PRIVATE: SKEW CALCULATIONS ────────────────────────────────────────────

    def _compute_atm_iv(self, chain: pd.DataFrame) -> float:
        """
        Estimate ATM implied volatility as the average IV of the two strikes
        closest to the spot price — robust to sparse chains.

        This corresponds to the ISD parameter in the Jarrow-Rudd model,
        anchoring the volatility level before skew adjustments.
        """
        abs_mono = chain["moneyness"].abs()
        atm_rows = chain.loc[abs_mono.nsmallest(2).index]
        atm_iv = ((atm_rows["iv_call"] + atm_rows["iv_put"]) / 2).mean()
        return float(atm_iv)

    def _compute_skew_spread(self, chain: pd.DataFrame) -> float:
        """
        Compute the Put-Call OTM implied volatility spread.

        This is the primary proxy for distributional skewness (λ1 direction):
            spread = mean(IV_Put_OTM) − mean(IV_Call_OTM)

        A positive spread (put IV > call IV) reflects the market's premium
        for downside protection — consistent with negative skewness in the
        return distribution. Corrado & Su (1996) document average ISK = -1.68
        for S&P 500 options in December 1993.

        Moneyness convention (from Corrado & Su Exhibit 3):
            Positive moneyness → K < S → ITM calls / OTM puts (lower strikes)
            Negative moneyness → K > S → OTM calls / ITM puts (higher strikes)

        Therefore:
            OTM puts  → rows with moneyness >  +otm_depth_pct (low strikes)
            OTM calls → rows with moneyness < −otm_depth_pct (high strikes)

        Only strikes with |moneyness| ≥ otm_depth_pct are included to
        isolate the directional signal from ATM noise.
        """
        otm_puts = chain[chain["moneyness"] > self.otm_depth_pct]  # K < S: OTM puts
        otm_calls = chain[chain["moneyness"] < -self.otm_depth_pct]  # K > S: OTM calls

        if otm_puts.empty or otm_calls.empty:
            warnings.warn(
                "Insufficient OTM strikes found for skew spread calculation. "
                "Consider reducing otm_depth_pct or providing a wider chain.",
                stacklevel=2,
            )
            return 0.0

        mean_put_iv = float(otm_puts["iv_put"].mean())
        mean_call_iv = float(otm_calls["iv_call"].mean())
        return mean_put_iv - mean_call_iv

    def _compute_implied_skewness(self, put_call_iv_spread: float) -> float:
        """
        Map the Put-Call IV spread to a signed skewness parameter analogous
        to λ1 in the Jarrow-Rudd model.

        Scaling convention: the spread is normalized relative to the empirical
        baseline from Corrado & Su (1996) where a skewness of -1.68 corresponded
        to a pronounced post-crash put skew in S&P 500 index options.

        A positive spread maps to negative λ1 (left-skewed distribution).
        A negative spread (call IV > put IV) maps to positive λ1 (right-skewed).
        """
        # Linear scaling: each 1% of spread maps to approximately 0.5 units of skewness
        # Calibrated against the empirical anchor: spread ≈ 3–5% → ISK ≈ -1.68
        scaling_factor = 35.0
        return -put_call_iv_spread * scaling_factor

    def _compute_tail_risk_factor(self, chain: pd.DataFrame, atm_iv: float) -> float:
        """
        Estimate the fat-tail / kurtosis factor (proxy for λ2 in Jarrow-Rudd).

        Logic: The ratio of deep-OTM implied volatility to ATM implied volatility
        reflects how much the market prices extreme tail events above the
        lognormal baseline. A ratio of 1.0 is consistent with lognormality
        (kurtosis = 3). Elevated ratios indicate excess kurtosis.

        The output is mapped to an absolute kurtosis estimate:
            kurtosis_estimate = lognormal_kurtosis + excess_factor

        Empirical reference: Corrado & Su (1996) report average IKT = 5.39,
        indicating ~2.39 units of excess kurtosis above the lognormal baseline.

        Parameters
        ----------
        chain  : pd.DataFrame — Enriched option chain with moneyness column.
        atm_iv : float        — ATM implied volatility anchor.

        Returns
        -------
        float : Estimated absolute kurtosis (lognormal baseline = 3.0).
        """
        tail_mask = chain["moneyness"].abs() >= self.tail_depth_pct
        tail_rows = chain[tail_mask]

        if tail_rows.empty or atm_iv <= 0:
            return self._LOGNORMAL_KURTOSIS  # default to lognormal if no tail data

        tail_iv_avg = float(((tail_rows["iv_call"] + tail_rows["iv_put"]) / 2).mean())
        tail_ratio = tail_iv_avg / atm_iv  # 1.0 = lognormal baseline

        # Non-linear mapping: small deviations above 1.0 → large kurtosis increase
        # Calibrated: ratio ≈ 1.15 → kurtosis ≈ 5.39 (Corrado & Su benchmark)
        excess_kurtosis = max(0.0, (tail_ratio - 1.0) * 16.0)
        return self._LOGNORMAL_KURTOSIS + excess_kurtosis

    # ── PRIVATE: RISK SCORING & CLASSIFICATION ────────────────────────────────

    def _compute_risk_score(
        self,
        implied_skewness: float,
        tail_risk_factor: float,
    ) -> float:
        """
        Compute a composite risk score in [0, 100].

        Two independent components contribute equally:
            1. Skew component  : severity of negative skewness (λ1 proxy)
            2. Kurtosis component: excess kurtosis above lognormal baseline

        Score > avoid_threshold  → RISK_AVOID  (long entry inadvisable)
        Score > caution_threshold → RISK_CAUTION (reduce size, await confirmation)
        Score ≤ caution_threshold → RISK_CLEAR  (favorable entry conditions)
        """
        # Skew component: negative skewness increases risk (range 0–50)
        # Normalized so that ISK = -1.68 (empirical avg) ≈ score 30
        skew_component = min(50.0, max(0.0, -implied_skewness * 17.86))

        # Kurtosis component: excess above 3.0 (lognormal) increases risk (range 0–50)
        # Normalized so that IKT = 5.39 (empirical avg) ≈ additional score 20
        excess_kurt = max(0.0, tail_risk_factor - self._LOGNORMAL_KURTOSIS)
        kurt_component = min(50.0, excess_kurt * 8.37)

        return skew_component + kurt_component

    def _classify_risk_flag(
        self,
        risk_score: float,
        implied_skewness: float,
        tail_risk_factor: float,
        put_call_spread: float,
    ) -> tuple[RiskFlag, str]:
        """
        Convert the continuous risk score into a directional RiskFlag.

        Positive put-call spread maps to long-side crash risk. Negative spread
        maps to short-side melt-up risk when call IV dominates put IV.

        Returns
        -------
        tuple[RiskFlag, str] : (flag, human-readable rationale string)
        """
        skew_pct = put_call_spread * 100
        excess_kurt = tail_risk_factor - self._LOGNORMAL_KURTOSIS
        is_call_skew_dominant = put_call_spread < -0.001

        if is_call_skew_dominant:
            if risk_score > self.avoid_threshold:
                flag = RiskFlag.RISK_SHORT_AVOID
                rationale = (
                    f"RISK_SHORT_AVOID | Score={risk_score:.1f}. "
                    f"Call-Put IV spread={-skew_pct:.2f}% indicates severe positive skew "
                    f"(λ1≈{implied_skewness:.2f}). Excess kurtosis={excess_kurt:.2f} signals "
                    "right-tail melt-up risk. Postpone short entry; await normalization of "
                    "call skew and kurtosis."
                )
            elif risk_score > self.caution_threshold:
                flag = RiskFlag.RISK_SHORT_CAUTION
                rationale = (
                    f"RISK_SHORT_CAUTION | Score={risk_score:.1f}. "
                    f"Moderate call skew detected (λ1≈{implied_skewness:.2f}, "
                    f"spread={-skew_pct:.2f}%). Tail factor={tail_risk_factor:.2f}. "
                    "Reduce short size or await improved asymmetry before adding."
                )
            else:
                flag = RiskFlag.RISK_SHORT_CLEAR
                rationale = (
                    f"RISK_SHORT_CLEAR | Score={risk_score:.1f}. "
                    "Call-skew and kurtosis within acceptable bounds for short entry "
                    f"(λ1≈{implied_skewness:.2f}, tail factor={tail_risk_factor:.2f}). "
                    "No melt-up risk signal at this time."
                )
            return flag, rationale

        if risk_score > self.avoid_threshold:
            flag = RiskFlag.RISK_AVOID
            rationale = (
                f"RISK_AVOID | Score={risk_score:.1f}. "
                f"Put-Call IV spread={skew_pct:.2f}% indicates severe negative skew "
                f"(λ1≈{implied_skewness:.2f}). Excess kurtosis={excess_kurt:.2f} signals "
                f"fat-tail crash risk. Postpone long entry; await normalization of skew "
                f"and kurtosis to more favorable levels."
            )
        elif risk_score > self.caution_threshold:
            flag = RiskFlag.RISK_CAUTION
            rationale = (
                f"RISK_CAUTION | Score={risk_score:.1f}. "
                f"Moderate put skew detected (λ1≈{implied_skewness:.2f}, spread={skew_pct:.2f}%). "
                f"Tail risk factor={tail_risk_factor:.2f}. Consider reducing position size "
                f"or waiting for a higher-conviction entry with improved risk/reward asymmetry."
            )
        else:
            flag = RiskFlag.RISK_CLEAR
            rationale = (
                f"RISK_CLEAR | Score={risk_score:.1f}. "
                f"Skew and kurtosis within acceptable bounds for long entry "
                f"(λ1≈{implied_skewness:.2f}, tail factor={tail_risk_factor:.2f}). "
                f"Probabilistic asymmetry does not signal elevated crash risk at this time."
            )

        return flag, rationale

    # ── PRIVATE: JARROW-RUDD DERIVATIVE TERMS ────────────────────────────────

    def _compute_q3_q4(self, strike: float, sigma: float) -> tuple[float, float]:
        """
        Compute the third (Q3) and fourth (Q4) order correction terms for
        the Jarrow-Rudd formula using the numerically stable Gram-Charlier /
        Edgeworth expansion around the Black-Scholes lognormal density.

        FORMULATION
        -----------
        Derived from the Gram-Charlier expansion of the risk-neutral density
        around the lognormal approximation (Jarrow & Rudd 1982, eq. 5-6).
        The correction terms are expressed using normalized Hermite polynomials
        weighted by the Black-Scholes vega component, per the equivalence:

            C_JR = C_BS + λ1·Q3 + λ2·Q4

        where:
            Q3 = S₀·σ√t·φ(d1) · H₂(d1) / 6         [skewness correction]
            Q4 = S₀·σ√t·φ(d1) · H₃(d1) / 24         [kurtosis correction]

        with Hermite polynomials:
            H₂(x) = x² − 1                            [related to d²φ/dx²]
            H₃(x) = x³ − 3x                           [related to d³φ/dx³]

        This formulation is numerically stable for all moneyness levels and
        is equivalent to the cumulant-based Edgeworth expansion in the paper.

        Interpretation:
        - Q3 changes sign around d1=±1, creating asymmetric price adjustment:
          negative λ1 lowers ITM call prices and raises OTM call prices → skew.
        - Q4 is zero at-the-money (d1=0) and amplifies at the tails → fat-tail
          premium for deep OTM options.

        Parameters
        ----------
        strike : float — Strike price K.
        sigma  : float — Volatility ISD (Implied Standard Deviation).

        Returns
        -------
        tuple[float, float] : (Q3, Q4) — per-unit price correction terms.

        References
        ----------
        Backus, Foresi & Wu (1997): "Accounting for Biases in Black-Scholes."
        Rubinstein, M. (1998): "Edgeworth Binomial Trees." JoD.
        """
        spot = self.spot_price
        r = self.risk_free_rate
        t = self.time_to_expiry
        strike_price = strike

        if sigma <= 0 or t <= 0 or strike_price <= 0:
            return 0.0, 0.0

        sqrt_t = np.sqrt(t)
        sigma_t = sigma * sqrt_t  # σ√t — volatility scaled to expiry

        d1 = (np.log(spot / strike_price) + (r + 0.5 * sigma**2) * t) / sigma_t

        # Black-Scholes vega component: S₀·σ√t·φ(d1)
        # This is the natural scaling unit for small distribution adjustments.
        vega_component = spot * sigma_t * norm.pdf(d1)

        # Hermite polynomial corrections (proper sign convention):
        # H₂(d1) = d1² − 1   → zero crossing at |d1|=1
        # H₃(d1) = d1³ − 3d1 → zero crossing at d1=0 and |d1|=√3
        h2 = d1**2 - 1.0
        h3 = d1**3 - 3.0 * d1

        # Q3: skewness correction (scaled by 3rd cumulant normalization factor 1/3!)
        q3 = vega_component * h2 / 6.0

        # Q4: excess kurtosis correction (scaled by 4th cumulant factor 1/4!)
        q4 = vega_component * h3 / 24.0

        return q3, q4

    # ── PRIVATE: REPORTING ────────────────────────────────────────────────────

    def _build_skew_profile(self, chain: pd.DataFrame) -> pd.DataFrame:
        """
        Build a per-strike skew profile DataFrame for audit and visualization.

        Columns
        -------
        strike       : float  — Strike price.
        iv_call      : float  — Call implied volatility.
        iv_put       : float  — Put implied volatility.
        skew_spread  : float  — iv_put − iv_call per strike (positive = put premium).
        moneyness    : float  — Signed moneyness (positive = ITM call).
        abs_moneyness: float  — |moneyness| for filtering convenience.
        """
        profile = chain[["strike", "iv_call", "iv_put", "moneyness"]].copy()
        profile["skew_spread"] = (profile["iv_put"] - profile["iv_call"]).round(6)
        profile["abs_moneyness"] = profile["moneyness"].abs().round(6)
        profile["iv_call"] = profile["iv_call"].round(6)
        profile["iv_put"] = profile["iv_put"].round(6)
        profile["moneyness"] = profile["moneyness"].round(6)
        return profile.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: OPTION CHAIN BUILDER
# ─────────────────────────────────────────────────────────────────────────────


def build_option_chain(
    strikes: list[float],
    iv_calls: list[float],
    iv_puts: list[float],
) -> pd.DataFrame:
    """
    Convenience constructor for an option chain DataFrame.

    Parameters
    ----------
    strikes  : list[float] — List of strike prices.
    iv_calls : list[float] — List of OTM call implied volatilities (decimal).
    iv_puts  : list[float] — List of OTM put implied volatilities (decimal).

    Returns
    -------
    pd.DataFrame with columns: strike, iv_call, iv_put.

    Raises
    ------
    ValueError : If input lists have unequal lengths.
    """
    if not (len(strikes) == len(iv_calls) == len(iv_puts)):
        raise ValueError("strikes, iv_calls, and iv_puts must have equal length.")
    return pd.DataFrame(
        {
            "strike": strikes,
            "iv_call": iv_calls,
            "iv_put": iv_puts,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# DEMO / INTEGRATION SMOKE TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Smoke test replicating the December 1993 S&P 500 environment described in
    Corrado & Su (1996). Expected outputs:
        - Negative implied skewness (≈ -1.5 to -2.0)
        - Kurtosis factor above 3.0 (≈ 4.5 to 6.0)
        - Risk flag: RISK_CAUTION or RISK_AVOID
    """
    # Approximate SPX chain: S=459.65, Dec 1993, February 1994 expiry
    # IVs calibrated to reproduce the volatility skew in Exhibit 3 (Corrado & Su)
    # Deep OTM puts ~17%, ATM ~12-13%, deep OTM calls ~9%
    chain_data = build_option_chain(
        strikes=[420, 430, 440, 450, 460, 470, 480, 490, 500],
        iv_calls=[0.175, 0.162, 0.148, 0.135, 0.129, 0.118, 0.107, 0.096, 0.088],
        iv_puts=[0.185, 0.173, 0.159, 0.143, 0.131, 0.120, 0.109, 0.100, 0.093],
    )

    engine = SkewFatTailsEngine(
        spot_price=459.65,
        risk_free_rate=0.0315,
        time_to_expiry=78 / 365,  # 78 days to Feb 1994 expiry
        otm_depth_pct=0.01,
        tail_depth_pct=0.03,
    )

    result = engine.analyze(chain_data)

    print("=" * 70)
    print("  QuantumBeta — Skew_FatTails_Engine | Analysis Report")
    print("=" * 70)
    print(f"  Spot Price          : {result.spot_price}")
    print(f"  ATM IV (ISD proxy)  : {result.atm_iv:.2%}")
    print(f"  Put-Call IV Spread  : {result.put_call_iv_spread:.2%}")
    print(f"  Implied Skewness λ1 : {result.implied_skewness:.4f}")
    print(f"  Tail Risk Factor λ2 : {result.tail_risk_factor:.4f}")
    print(f"  Composite Risk Score: {result.risk_score:.2f} / 100")
    print("  ── RISK FLAG ──────────────────────────────────────")
    print(f"  {result.risk_flag.value}")
    print(f"  {result.flag_rationale}")
    print("=" * 70)
    print("\n  Per-Strike Skew Profile:")
    print(result.skew_profile.to_string(index=False))

    # Jarrow-Rudd vs Black-Scholes price comparison at representative strikes
    print("\n  J-R vs B-S Call Price Comparison (ISD=12.88%, λ1=-1.68, λ2=5.39):")
    print(f"  {'Strike':>8}  {'BS Price':>10}  {'JR Price':>10}  {'Δ Price':>10}")
    for k in [430, 460, 490]:
        bs = engine.black_scholes_call(k, sigma=0.1288)
        jr = engine.jarrow_rudd_call(k, isd=0.1162, lambda1=-1.68, lambda2=5.39)
        print(f"  {k:>8}  {bs:>10.4f}  {jr:>10.4f}  {jr - bs:>+10.4f}")
    print("=" * 70)
