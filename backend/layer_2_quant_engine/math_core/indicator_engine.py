"""Technical Indicator Engine - high-performance calculation using pandas-ta."""

import logging
from typing import Any

import pandas as pd
import pandas_ta as ta  # noqa: F401 — registers `DataFrame.ta` accessor

logger = logging.getLogger(__name__)

_OHLCV = ("open", "high", "low", "close", "volume")


class IndicatorEngine:
    """
    Stateful or stateless indicators engine.
    Calculates technical analysis markers for tick-by-tick or candle data.
    """

    @staticmethod
    def _ta_first_series(result: pd.DataFrame | pd.Series | None) -> pd.Series | None:
        """pandas-ta sometimes returns a DataFrame; chart pipeline needs one Series."""
        if result is None:
            return None
        if isinstance(result, pd.DataFrame):
            if result.empty or result.shape[1] < 1:
                return None
            return result.iloc[:, 0]
        out = result.squeeze()
        return out if isinstance(out, pd.Series) else None

    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates institutional indicators on OHLCV rows.
        Expects columns including open, high, low, close, volume (time optional).
        """
        if df.empty or len(df) < 5:
            return df

        work = df.copy()
        work.columns = [str(c).lower() for c in work.columns]
        if not set(_OHLCV).issubset(work.columns):
            logger.warning(
                "IndicatorEngine: missing OHLCV (have %s)",
                list(work.columns),
            )
            return df

        work = work.loc[:, ~work.columns.duplicated()]
        time_col = "time" if "time" in work.columns else None
        if time_col:
            work = work.sort_values(time_col, kind="mergesort").reset_index(drop=True)
        else:
            work = work.reset_index(drop=True)

        base = work[list(_OHLCV)].astype(float)

        out = pd.DataFrame()
        if time_col:
            out["time"] = work[time_col].values
        for c in _OHLCV:
            out[c] = base[c].values

        try:
            rsi = IndicatorEngine._ta_first_series(base.ta.rsi(length=14))
            if rsi is not None:
                out["rsi"] = rsi.values

            ema20 = IndicatorEngine._ta_first_series(base.ta.ema(length=20))
            if ema20 is not None:
                out["ema20"] = ema20.values

            ema50 = IndicatorEngine._ta_first_series(base.ta.ema(length=50))
            if ema50 is not None:
                out["ema50"] = ema50.values

            ema200 = IndicatorEngine._ta_first_series(base.ta.ema(length=200))
            if ema200 is not None:
                out["ema200"] = ema200.values

            st = base.ta.supertrend(length=7, multiplier=3.0)
            if st is not None and not st.empty:
                for col in st.columns:
                    cus = str(col).upper()
                    if cus.startswith("SUPERT_") and not cus.startswith("SUPERTD"):
                        out["supertrend"] = st[col].values
                        break

            # Session-anchored pandas-ta VWAP needs DatetimeIndex; use cumulative VWAP on the window.
            vol = base["volume"].fillna(0.0)
            if float(vol.sum()) > 0:
                out["vwap"] = (base["close"] * vol).cumsum().to_numpy() / vol.cumsum().to_numpy()
            else:
                out["vwap"] = base["close"].values

        except Exception as e:
            logger.error("Error calculating indicators: %s", e)
            return work

        out = out.loc[:, ~out.columns.duplicated()]
        return out

    @staticmethod
    def process_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Takes a list of candle dicts and returns them with indicators added per row.
        """
        if not candles:
            return []

        df = pd.DataFrame(candles)
        processed_df = IndicatorEngine.calculate_indicators(df)
        if processed_df.columns.duplicated().any():
            processed_df = processed_df.loc[:, ~processed_df.columns.duplicated()]
        return processed_df.to_dict(orient="records")
