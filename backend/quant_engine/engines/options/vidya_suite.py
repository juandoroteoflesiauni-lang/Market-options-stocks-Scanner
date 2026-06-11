from collections import deque
from typing import Any

import numpy as np
import pandas as pd


def _compute_cmo(close: np.ndarray, period: int = 9) -> np.ndarray:
    diff = np.diff(close, prepend=close[0])
    ups = np.where(diff > 0, diff, 0.0)
    downs = np.where(diff < 0, -diff, 0.0)

    cmo = np.zeros(len(close))
    for i in range(period, len(close)):
        su = ups[i - period + 1 : i + 1].sum()
        sd = downs[i - period + 1 : i + 1].sum()
        if su + sd > 0:
            cmo[i] = (su - sd) / (su + sd) * 100
    return cmo


def _run_vidya(close: np.ndarray, k_arr: np.ndarray) -> np.ndarray:
    vidya = np.zeros(len(close))
    vidya[0] = close[0]
    for i in range(1, len(close)):
        k = np.clip(k_arr[i], 0.0, 1.0)
        vidya[i] = vidya[i - 1] + k * (close[i] - vidya[i - 1])
    return vidya


def _crossover_signal(vidya: np.ndarray, close: np.ndarray) -> tuple[bool, bool]:
    if len(vidya) < 2:
        return False, False
    bull_cross = close[-2] <= vidya[-2] and close[-1] > vidya[-1]
    bear_cross = close[-2] >= vidya[-2] and close[-1] < vidya[-1]
    return bull_cross, bear_cross


class VidyaIVAdaptiveEngine:
    def __init__(self, ticker: str, cmo_period: int = 9, lookback: int = 100):
        self.ticker = ticker
        self.cmo_period = cmo_period
        self.lookback = lookback
        self.history = deque(maxlen=lookback)

    def update(self, close: float, iv_atm: float) -> dict[str, Any]:
        self.history.append({"close": close})

        if len(self.history) < self.cmo_period + 5:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        df = pd.DataFrame(self.history)
        close_arr = df["close"].values
        cmo_arr = _compute_cmo(close_arr, self.cmo_period)

        # k = |CMO|/100 * (1.0 - IV)
        k_iv = np.full(len(close_arr), np.clip(1.0 - iv_atm, 0.1, 0.9))
        k_comb = np.abs(cmo_arr) / 100.0 * k_iv

        vidya = _run_vidya(close_arr, k_comb)
        bull, bear = _crossover_signal(vidya, close_arr)

        signal_quality = 1.0 - iv_atm
        score = 0.0
        signal = "NEUTRAL"

        if bull or bear:
            score = round(min(0.90, 0.5 + signal_quality * 0.4), 3)
            signal = "LONG" if bull else "SHORT"

        if score < 0.3:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "iv_atm": iv_atm,
            "signal_quality": signal_quality,
        }


class VidyaGammaSpeedEngine:
    def __init__(
        self, ticker: str, cmo_period: int = 9, lookback: int = 80, gex_modifier: float = 0.5
    ):
        self.ticker = ticker
        self.cmo_period = cmo_period
        self.lookback = lookback
        self.gex_modifier = gex_modifier
        self.history = deque(maxlen=lookback)

    def update(self, close: float, total_gex: float) -> dict[str, Any]:
        self.history.append({"close": close})

        if len(self.history) < self.cmo_period + 5:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        gex_sign = int(np.sign(total_gex))
        df = pd.DataFrame(self.history)
        close_arr = df["close"].values
        cmo_arr = _compute_cmo(close_arr, self.cmo_period)

        k_base = np.abs(cmo_arr) / 100.0
        multiplier = 1.0 + gex_sign * (-self.gex_modifier)
        k_gex = np.clip(k_base * multiplier, 0.05, 1.0)

        vidya = _run_vidya(close_arr, k_gex)
        bull, bear = _crossover_signal(vidya, close_arr)

        regime_bonus = 0.15 if gex_sign < 0 else 0.0
        score = 0.0
        signal = "NEUTRAL"

        if bull or bear:
            score = round(min(0.92, 0.50 + regime_bonus + abs(float(cmo_arr[-1])) / 100 * 0.3), 3)
            signal = "LONG" if bull else "SHORT"

        if score < 0.3:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "gex_sign": gex_sign,
            "k_multiplier": multiplier,
        }


class VidyaCvdEngine:
    def __init__(
        self, ticker: str, cmo_period: int = 9, lookback: int = 90, ndde_scale: float = 1000.0
    ):
        self.ticker = ticker
        self.cmo_period = cmo_period
        self.lookback = lookback
        self.ndde_scale = ndde_scale
        self.history = deque(maxlen=lookback)

    def update(self, close: float, volume: float, delta: float, ndde: float) -> dict[str, Any]:
        self.history.append({"close": close, "volume": volume, "delta": delta})

        if len(self.history) < self.cmo_period + 5:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        df = pd.DataFrame(self.history)
        close_arr = df["close"].values

        cvd = df["delta"].cumsum().values
        vol_total = float(df["volume"].sum())
        cvd_norm = cvd / vol_total if vol_total > 0 else cvd * 0

        ndde_norm = float(np.tanh(ndde / self.ndde_scale))

        k_cvd = np.abs(cvd_norm)
        cvd_sign = np.sign(cvd_norm)

        alignment = 0.5 + 0.5 * cvd_sign * ndde_norm
        k_final = np.clip(k_cvd * alignment * 2.0, 0.05, 0.95)

        vidya = _run_vidya(close_arr, k_final)
        bull, bear = _crossover_signal(vidya, close_arr)

        cvd_last = float(cvd_norm[-1])
        align_last = float(alignment[-1])
        base_score = 0.0
        signal = "NEUTRAL"

        if bull or bear:
            base_score = min(0.85, abs(cvd_last) * 0.5 + align_last * 0.4)
            signal = "LONG" if bull else "SHORT"

        if base_score < 0.3:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "strength": int(base_score * 100),
            "score": round(base_score, 3),
            "cvd_norm_last": cvd_last,
            "ndde_norm": ndde_norm,
            "alignment": align_last,
        }
