"""
backend/layer_3_specialists/ia_probabilistico/engines/markov_regime_engine.py
════════════════════════════════════════════════════════════════════════════════
Markov Regime Switching Engine — identifies structural market shifts.

Strategy:
  1. Use log-returns and rolling volatility as observation space.
  2. Define 3 latent regimes:
     - BULL_QUIET (High Return, Low Vol)
     - BEAR_VOLATILE (Low/Neg Return, High Vol)
     - CHAOTIC_TRANSITION (Unstable mean/vol)
  3. Estimate state probabilities using a Gaussian Mixture approach.
  4. Detect "Regime Decoupling" when the current state probability shifts
     rapidly (high entropy).
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, cast

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]
from scipy.stats import norm  # type: ignore[import-not-found, import-untyped]

logger = logging.getLogger(__name__)

@dataclass
class RegimeState:
    index: int
    label: str        # "BULL_QUIET" | "BEAR_VOLATILE" | "CHAOTIC"
    probability: float
    mean_return: float
    volatility: float

@dataclass
class MarkovReport:
    symbol: str
    current_state: str
    state_confidence: float
    states: list[RegimeState]
    transition_risk: float  # Entropy of the state distribution
    expected_days_in_state: int
    regime_signal: str      # "STABLE" | "SHIFTING" | "CRITICAL"

class MarkovRegimeEngine:
    """
    Identifies the current market regime using a Gaussian Mixture model
    on returns and volatility.
    """

    # Pre-defined regime characteristics (priors)
    # Mean and Volatility priors for 3 states
    REGIME_PRIORS = {
        0: {"label": "BULL_QUIET", "mu": 0.0005, "sigma": 0.01},
        1: {"label": "BEAR_VOLATILE", "mu": -0.001, "sigma": 0.03},
        2: {"label": "CHAOTIC", "mu": 0.0, "sigma": 0.02},
    }

    def analyze(
        self,
        symbol: str,
        df: pd.DataFrame,
        window: int = 60
    ) -> MarkovReport:
        """
        Estimates the probability of being in each regime.
        """
        if df.empty or len(df) < 20:
            return self._empty_report(symbol)

        # 1. Prepare Observations
        # Log returns
        returns = np.log(df["close"] / df["close"].shift(1)).dropna().values
        # Rolling vol (20d)
        vols = df["close"].pct_change().rolling(20).std().dropna().values

        if len(returns) < 5 or len(vols) < 5:
            return self._empty_report(symbol)

        # Sync lengths
        min_len = min(len(returns), len(vols))
        obs_returns = returns[-min_len:]
        obs_vols = vols[-min_len:]

        # Current observation
        curr_ret = obs_returns[-1]
        curr_vol = obs_vols[-1]

        # 2. Calculate Likelihoods for each state
        likelihoods = []
        for i in range(3):
            prior = cast(dict[str, Any], self.REGIME_PRIORS[i])
            # Prob of return given state
            p_ret = norm.pdf(curr_ret, loc=prior["mu"], scale=prior["sigma"])
            # Prob of vol given state
            p_vol = norm.pdf(curr_vol, loc=prior["sigma"], scale=prior["sigma"] * 0.5)

            # Joint likelihood (assuming independence for simplicity)
            likelihoods.append(p_ret * p_vol + 1e-10) # epsilon for stability

        # 3. Normalize to get Posterior Probabilities (Naive Bayes assumption)
        total = sum(likelihoods)
        probs = [lh / total for lh in likelihoods]

        # 4. State Assignment
        max_idx = int(np.argmax(probs))
        current_label = cast(str, self.REGIME_PRIORS[max_idx]["label"])
        confidence = probs[max_idx]

        # 5. Calculate Entropy (Transition Risk)
        # Entropy = -sum(p * log(p))
        entropy = -sum(p * np.log(p + 1e-10) for p in probs)
        normalized_entropy = entropy / np.log(3) # Scale 0 to 1

        # 6. Expected Duration (Approximated from transition persistence)
        # In a real HMM this is 1 / (1 - p_ii). Here we estimate based on confidence.
        expected_days = int(5 + 20 * confidence)

        # 7. Regime Signal
        if normalized_entropy > 0.7:
            regime_signal = "CRITICAL" # Market is undecisive, high shift risk
        elif normalized_entropy > 0.4:
            regime_signal = "SHIFTING"
        else:
            regime_signal = "STABLE"

        # 8. Build States list
        regime_states = []
        for i in range(3):
            regime_states.append(RegimeState(
                index=i,
                label=cast(str, self.REGIME_PRIORS[i]["label"]),
                probability=round(probs[i], 4),
                mean_return=cast(float, self.REGIME_PRIORS[i]["mu"]),
                volatility=cast(float, self.REGIME_PRIORS[i]["sigma"])
            ))

        return MarkovReport(
            symbol=symbol,
            current_state=current_label,
            state_confidence=round(confidence, 4),
            states=regime_states,
            transition_risk=round(normalized_entropy, 4),
            expected_days_in_state=expected_days,
            regime_signal=regime_signal
        )

    def _empty_report(self, symbol: str) -> MarkovReport:
        return MarkovReport(
            symbol=symbol,
            current_state="UNKNOWN",
            state_confidence=0.0,
            states=[],
            transition_risk=1.0,
            expected_days_in_state=0,
            regime_signal="CRITICAL"
        )