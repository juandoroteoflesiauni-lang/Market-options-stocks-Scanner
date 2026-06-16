from __future__ import annotations
"""
backend/engine/metrics/cross_asset.py
Sector: Options / Cross-Asset Correlation Engine
[ARCH-1, PD-4]

Theoretical basis:
    Calculates the decoupling scores and regime shifting between a target asset
    and a reference portfolio of global macro assets to detect institutional flow regimes.
"""


import logging

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.cross_asset")

type FloatArray = npt.NDArray[np.float64]

REFERENCE_ASSETS: dict[str, str] = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "GLD": "GLD",
    "DXY": "DXY",
    "BTC": "BTCUSD",
    "TLT": "TLT",
}

SECTOR_ETFS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLY": "Consumer Discretionary",
    "XLI": "Industrials",
}


class CorrelationProfile(BaseModel):
    """Pairwise correlation profile between target and reference asset (frozen)."""

    model_config = ConfigDict(frozen=True)

    reference_ticker: str
    label: str
    rolling_corr: float
    long_corr: float
    decoupling_score: float
    is_decoupled: bool
    direction: str


class CrossAssetReport(BaseModel):
    """Aggregate cross-asset report (frozen)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    correlations: list[CorrelationProfile]
    strongest_link: str | None
    max_decoupling: float
    decoupling_alert: bool
    systematic_risk: float
    idiosyncratic_risk: float
    regime_label: str


class CrossAssetEngine:
    """
    Calculates rolling and structural correlations against global reference assets
    using high-speed 2D matrix operations.
    """

    SHORT_WINDOW: int = 30
    LONG_WINDOW: int = 126
    DECOUPLING_THRESHOLD: float = 0.30

    def analyze(
        self,
        symbol: str,
        target_prices: FloatArray,
        ref_prices: FloatArray,
        ref_tickers: list[str],
    ) -> Result[CrossAssetReport]:
        """
        Analyzes decoupling regimes using 2D matrix correlations.

        Parameters
        ----------
        symbol : str
            Target symbol.
        target_prices : FloatArray
            1D array of target asset price history of size N.
        ref_prices : FloatArray
            2D array of reference asset price history of size (N, M).
        ref_tickers : list[str]
            List of reference tickers of size M.

        Returns
        -------
        Result[CrossAssetReport]
            The Correlation analysis report.
        """
        try:
            # 1. Validations
            if not isinstance(target_prices, np.ndarray) or not isinstance(ref_prices, np.ndarray):
                return Result.failure(reason="Prices must be numpy ndarrays")

            if target_prices.ndim != 1:
                return Result.failure(reason="target_prices must be a 1D array")

            if ref_prices.ndim != 2:
                return Result.failure(reason="ref_prices must be a 2D array")

            n = len(target_prices)
            if ref_prices.shape[0] != n:
                return Result.failure(
                    reason="ref_prices must have same number of rows as target_prices"
                )

            if n < 10:
                return Result.failure(reason="Prices must have at least 10 observations")

            m = ref_prices.shape[1]
            if len(ref_tickers) != m:
                return Result.failure(
                    reason="ref_tickers count must match number of columns in ref_prices"
                )

            if m == 0:
                return Result.failure(reason="ref_prices must contain at least 1 reference asset")

            if np.any(np.isnan(target_prices)) or np.any(np.isnan(ref_prices)):
                return Result.failure(reason="Prices contain NaN values")

            if np.any(target_prices <= 0.0) or np.any(ref_prices <= 0.0):
                return Result.failure(reason="Prices must be positive")

            # 2. Vectorized returns using 2D matrix stacking
            combined_prices = np.column_stack((target_prices, ref_prices))
            combined_returns = np.diff(combined_prices, axis=0) / (combined_prices[:-1] + 1e-12)

            ret_len = len(combined_returns)
            short_n = min(ret_len, self.SHORT_WINDOW)
            long_n = min(ret_len, self.LONG_WINDOW)

            short_rets = combined_returns[-short_n:]
            long_rets = combined_returns[-long_n:]

            # 3. Correlation matrices using np.corrcoef
            short_corr = np.corrcoef(short_rets, rowvar=False)
            long_corr = np.corrcoef(long_rets, rowvar=False)

            # Ensure correct formatting if M=1 (2x2 matrix) or M>1 ((M+1)x(M+1) matrix)
            short_corrs = np.nan_to_num(short_corr[0, 1:], nan=0.0)
            long_corrs = np.nan_to_num(long_corr[0, 1:], nan=0.0)

            # Convert single values to arrays if they are scalar (e.g. if M=1, slice returns scalar)
            if not isinstance(short_corrs, np.ndarray):
                short_corrs = np.array([short_corrs])
            if not isinstance(long_corrs, np.ndarray):
                long_corrs = np.array([long_corrs])

            # 4. Decoupling calculations (vectorized)
            decoupling_vals = np.abs(short_corrs - long_corrs)
            is_decoupled_vals = decoupling_vals > self.DECOUPLING_THRESHOLD
            direction_vals = np.select(
                [short_corrs > 0.15, short_corrs < -0.15],
                ["POSITIVE", "NEGATIVE"],
                default="NEUTRAL",
            )

            # 5. Build profiles
            profiles: list[CorrelationProfile] = []
            for j, ref_ticker in enumerate(ref_tickers):
                label = REFERENCE_ASSETS.get(ref_ticker, SECTOR_ETFS.get(ref_ticker, ref_ticker))
                profiles.append(
                    CorrelationProfile(
                        reference_ticker=ref_ticker,
                        label=label,
                        rolling_corr=round(float(short_corrs[j]), 4),
                        long_corr=round(float(long_corrs[j]), 4),
                        decoupling_score=round(float(decoupling_vals[j]), 4),
                        is_decoupled=bool(is_decoupled_vals[j]),
                        direction=str(direction_vals[j]),
                    )
                )

            # Sort by absolute rolling correlation descending
            profiles.sort(key=lambda p: abs(p.rolling_corr), reverse=True)
            strongest_link = profiles[0].reference_ticker if profiles else None
            max_decoupling = max((p.decoupling_score for p in profiles), default=0.0)
            decoupling_alert = any(p.is_decoupled for p in profiles)

            # 6. Risk metrics
            market_proxies = {p.reference_ticker: p for p in profiles}
            spy_corr = abs(market_proxies["SPY"].rolling_corr) if "SPY" in market_proxies else 0.5
            tlt_corr = abs(market_proxies["TLT"].rolling_corr) if "TLT" in market_proxies else 0.3
            systematic = (spy_corr + tlt_corr) / 2.0
            idiosyncratic = max(0.0, 1.0 - systematic)

            # Heuristic regime labels
            if decoupling_alert and idiosyncratic > 0.6:
                regime = "IDIOSYNCRATIC_DECOUPLING"
            elif systematic > 0.65:
                regime = "HIGHLY_SYSTEMATIC"
            elif systematic < 0.25:
                regime = "INDEPENDENT"
            else:
                regime = "MODERATE_COUPLING"

            report = CrossAssetReport(
                symbol=symbol,
                correlations=profiles,
                strongest_link=strongest_link,
                max_decoupling=round(max_decoupling, 4),
                decoupling_alert=decoupling_alert,
                systematic_risk=round(systematic, 4),
                idiosyncratic_risk=round(idiosyncratic, 4),
                regime_label=regime,
            )
            return Result.success(report)

        except Exception as e:
            logger.error("CrossAsset analysis failed: %s", e)
            return Result.failure(reason=f"CrossAsset analysis failed: {e}")


def get_cross_asset_analysis(
    symbol: str,
    target_prices: FloatArray,
    ref_prices: FloatArray,
    ref_tickers: list[str],
) -> Result[CrossAssetReport]:
    """Stateless entry point for Cross Asset analysis."""
    engine = CrossAssetEngine()
    return engine.analyze(symbol, target_prices, ref_prices, ref_tickers)
