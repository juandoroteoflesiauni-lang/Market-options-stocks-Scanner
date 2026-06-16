"""
backend/layer_3_specialists/ia_probabilistico/engines/cross_asset_engine.py
════════════════════════════════════════════════════════════════════════════════
Cross-Asset Correlation Engine — detects regime decoupling in real time.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ── Reference universe ────────────────────────────────────────────────────────
# Maps a readable name to the ticker used for FMP lookups.
REFERENCE_ASSETS: dict[str, str] = {
    "SPY": "SPY",  # S&P 500 — broad market beta
    "QQQ": "QQQ",  # Nasdaq — tech regime
    "GLD": "GLD",  # Gold — safe-haven flow
    "DXY": "DXY",  # US Dollar Index — macro pressure
    "BTC": "BTCUSD",  # Bitcoin — risk-on / risk-off sentiment
    "TLT": "TLT",  # 20Y Treasury — rates / duration risk
}

SECTOR_ETFS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLY": "Consumer Discretionary",
    "XLI": "Industrials",
}


@dataclass
class CorrelationProfile:
    """Pairwise correlation result between target and a reference asset."""

    reference_ticker: str
    label: str
    rolling_corr: float  # 60-day rolling Pearson
    long_corr: float  # 252-day baseline
    decoupling_score: float  # |rolling - long| normalised [0,1]
    is_decoupled: bool  # True when score > 0.35
    direction: str  # "POSITIVE" | "NEGATIVE" | "NEUTRAL"


@dataclass
class CrossAssetReport:
    """Full cross-asset analysis for a target symbol."""

    symbol: str
    correlations: list[CorrelationProfile] = field(default_factory=list)
    strongest_link: str | None = None
    max_decoupling: float = 0.0
    decoupling_alert: bool = False
    systematic_risk: float = 0.0  # avg |corr| with SPY + TLT
    idiosyncratic_risk: float = 0.0  # 1 - systematic_risk
    regime_label: str = "UNKNOWN"


# ── Engine ────────────────────────────────────────────────────────────────────


class CrossAssetEngine:
    """
    Calculates rolling and long-term correlations between a target symbol
    and key reference assets to detect decoupling events.
    """

    SHORT_WINDOW = 30  # days for "current" regime correlation
    LONG_WINDOW = 126  # days for structural baseline (~6 months)
    DECOUPLING_THRESHOLD = 0.30  # minimum shift to flag as decoupled

    def compute_returns(self, prices: list[float]) -> np.ndarray[Any, np.dtype[Any]]:
        arr = np.array(prices, dtype=float)
        return np.diff(arr) / (arr[:-1] + 1e-12)

    def _safe_corr(
        self, a: np.ndarray[Any, np.dtype[Any]], b: np.ndarray[Any, np.dtype[Any]], window: int
    ) -> float:
        """Return the last observation of a rolling Pearson correlation."""
        if len(a) < window or len(b) < window:
            return float(np.corrcoef(a[-20:], b[-20:])[0, 1]) if len(a) >= 5 else 0.0
        n = min(len(a), len(b), window)
        a_w, b_w = a[-n:], b[-n:]
        if np.std(a_w) < 1e-9 or np.std(b_w) < 1e-9:
            return 0.0
        return float(np.corrcoef(a_w, b_w)[0, 1])

    def analyze(
        self,
        symbol: str,
        target_prices: list[float],
        reference_prices: dict[str, list[float]],
    ) -> CrossAssetReport:
        """
        Run correlation analysis for `symbol` against all available references.

        Args:
            symbol: The target ticker.
            target_prices: Sorted list of closing prices (oldest → newest).
            reference_prices: Dict mapping reference ticker → price list.
        """
        if len(target_prices) < 10:
            return CrossAssetReport(symbol=symbol, regime_label="INSUFFICIENT_DATA")

        target_rets = self.compute_returns(target_prices)
        profiles: list[CorrelationProfile] = []

        for ref_ticker, ref_prices in reference_prices.items():
            if not ref_prices or len(ref_prices) < 10:
                continue

            label = REFERENCE_ASSETS.get(ref_ticker, SECTOR_ETFS.get(ref_ticker, ref_ticker))
            ref_rets = self.compute_returns(ref_prices)

            # Align lengths
            n = min(len(target_rets), len(ref_rets))
            t_r = target_rets[-n:]
            r_r = ref_rets[-n:]

            rolling_corr = self._safe_corr(t_r, r_r, self.SHORT_WINDOW)
            long_corr = self._safe_corr(t_r, r_r, self.LONG_WINDOW)

            decoupling = abs(rolling_corr - long_corr)
            is_decoupled = decoupling > self.DECOUPLING_THRESHOLD

            if rolling_corr > 0.15:
                direction = "POSITIVE"
            elif rolling_corr < -0.15:
                direction = "NEGATIVE"
            else:
                direction = "NEUTRAL"

            profiles.append(
                CorrelationProfile(
                    reference_ticker=ref_ticker,
                    label=label,
                    rolling_corr=round(rolling_corr, 4),
                    long_corr=round(long_corr, 4),
                    decoupling_score=round(decoupling, 4),
                    is_decoupled=is_decoupled,
                    direction=direction,
                )
            )

        if not profiles:
            return CrossAssetReport(symbol=symbol, regime_label="NO_REFERENCE_DATA")

        # ── Aggregate insights ─────────────────────────────────────────────
        profiles.sort(key=lambda p: abs(p.rolling_corr), reverse=True)
        strongest = profiles[0]
        max_decoupling = max(p.decoupling_score for p in profiles)
        decoupling_alert = any(p.is_decoupled for p in profiles)

        # Systematic risk: avg |corr| with SPY and TLT
        market_proxies = {p.reference_ticker: p for p in profiles}
        spy_corr = abs(market_proxies["SPY"].rolling_corr) if "SPY" in market_proxies else 0.5
        tlt_corr = abs(market_proxies["TLT"].rolling_corr) if "TLT" in market_proxies else 0.3
        systematic = (spy_corr + tlt_corr) / 2.0
        idiosyncratic = max(0.0, 1.0 - systematic)

        # Regime label heuristic
        if decoupling_alert and idiosyncratic > 0.6:
            regime = "IDIOSYNCRATIC_DECOUPLING"
        elif systematic > 0.65:
            regime = "HIGHLY_SYSTEMATIC"
        elif systematic < 0.25:
            regime = "INDEPENDENT"
        else:
            regime = "MODERATE_COUPLING"

        return CrossAssetReport(
            symbol=symbol,
            correlations=profiles,
            strongest_link=strongest.reference_ticker,
            max_decoupling=round(max_decoupling, 4),
            decoupling_alert=decoupling_alert,
            systematic_risk=round(systematic, 4),
            idiosyncratic_risk=round(idiosyncratic, 4),
            regime_label=regime,
        )
