from typing import Any
from collections import deque

import numpy as np
import pandas as pd


def _linear_slope(series: np.ndarray[Any, Any]) -> float:
    n = len(series)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    xm, ym = x.mean(), series.mean()
    denom = ((x - xm) ** 2).sum()
    return float((((x - xm) * (series - ym)).sum() / denom) if denom > 0 else 0.0)


class CVDNddeDivergenceEngine:
    def __init__(self, ticker: str, lookback: int = 30, div_slope_threshold: float = 0.001):
        self.ticker = ticker
        self.lookback = lookback
        self.div_slope_threshold = div_slope_threshold
        self.history = deque(maxlen=lookback)

    def update(self, close: float, delta: float, ndde: float) -> dict[str, Any]:
        self.history.append({"close": close, "delta": delta})
        if len(self.history) < 10:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        df = pd.DataFrame(self.history)
        price_arr = df["close"].values
        cvd_arr = df["delta"].cumsum().values

        s_price = _linear_slope(price_arr)
        s_cvd = _linear_slope(cvd_arr)

        bull_trap = (
            s_price > self.div_slope_threshold and s_cvd < -self.div_slope_threshold and ndde < 0
        )
        bear_trap = (
            s_price < -self.div_slope_threshold and s_cvd > self.div_slope_threshold and ndde > 0
        )

        score = 0.0
        signal = "NEUTRAL"

        if bull_trap:
            score = min(0.90, abs(s_cvd) / 0.01 * 0.4 + abs(ndde) / 1000 * 0.3)
            signal = "BULL_TRAP"
        elif bear_trap:
            score = min(0.90, abs(s_cvd) / 0.01 * 0.4 + abs(ndde) / 1000 * 0.3)
            signal = "BEAR_TRAP"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "slope_price": s_price,
            "slope_cvd": s_cvd,
            "ndde": ndde,
        }


class CVDGammaWeightedEngine:
    def __init__(self, ticker: str, lookback: int = 60):
        self.ticker = ticker
        self.lookback = lookback
        self.history = deque(maxlen=lookback)

    def update(self, close: float, delta: float, total_gex: float) -> dict[str, Any]:
        self.history.append({"close": close, "delta": delta})
        if len(self.history) < 10:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        # Normalize GEX
        gex_norm = max(0.0, 1.0 + float(np.tanh(total_gex / 1e6)))

        df = pd.DataFrame(self.history)
        price_arr = df["close"].values
        cvd_weighted = (df["delta"] * gex_norm).cumsum().values

        slope_cvd_w = _linear_slope(cvd_weighted)
        slope_price = _linear_slope(price_arr)

        aligned = (slope_cvd_w > 0 and slope_price > 0) or (slope_cvd_w < 0 and slope_price < 0)

        score = min(0.85, abs(slope_cvd_w) / 0.005 * 0.5) if aligned else 0.05
        signal = "NEUTRAL"

        if aligned:
            if slope_cvd_w > 0:
                signal = "CONFIRMED_UP"
            elif slope_cvd_w < 0:
                signal = "CONFIRMED_DOWN"

        if score < 0.3:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "gex_norm": gex_norm,
            "slope_cvd_w": slope_cvd_w,
            "slope_price": slope_price,
        }


class CVDFootprintEngine:
    def __init__(self, ticker: str):
        self.ticker = ticker

    def update(
        self,
        open: float,
        close: float,
        delta: float,
        call_buy_vol_delta: float,
        put_buy_vol_delta: float,
    ) -> dict[str, Any]:
        candle_green = close >= open
        candle_delta = delta

        opt_calls_bought = call_buy_vol_delta
        opt_puts_bought = put_buy_vol_delta

        ftype = "NEUTRAL"
        score = 0.05
        signal = "NEUTRAL"

        if candle_green and opt_puts_bought > opt_calls_bought and opt_puts_bought > 0:
            ftype = "DISTRIBUTION"
            signal = "SHORT"
            score = min(
                0.85, 0.4 + opt_puts_bought / (opt_puts_bought + opt_calls_bought + 1) * 0.5
            )
        elif not candle_green and opt_calls_bought > opt_puts_bought and opt_calls_bought > 0:
            ftype = "ACCUMULATION"
            signal = "LONG"
            score = min(
                0.85, 0.4 + opt_calls_bought / (opt_calls_bought + opt_puts_bought + 1) * 0.5
            )

        if score < 0.3:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "footprint_type": ftype,
            "candle_delta": candle_delta,
            "calls_bought": opt_calls_bought,
            "puts_bought": opt_puts_bought,
        }
