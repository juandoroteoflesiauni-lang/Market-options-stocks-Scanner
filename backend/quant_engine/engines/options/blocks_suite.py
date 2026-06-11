from collections import deque
from typing import Any

import numpy as np
import pandas as pd


class BlockSweepEngine:
    def __init__(self, ticker: str, atr_period: int = 20, block_k: float = 3.0):
        self.ticker = ticker
        self.atr_period = atr_period
        self.block_k = block_k
        self.history = deque(maxlen=atr_period + 5)

    def update(
        self,
        close: float,
        volume: float,
        delta: float,
        sweep_count: int,
        call_buy_vol_delta: float,
        put_buy_vol_delta: float,
    ) -> dict[str, Any]:
        self.history.append({"close": close, "volume": volume, "delta": delta})

        if len(self.history) < self.atr_period:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        df = pd.DataFrame(self.history)
        mean_vol = df["volume"].rolling(self.atr_period).mean().iloc[-1]
        std_vol = df["volume"].rolling(self.atr_period).std().iloc[-1]

        if pd.isna(std_vol) or std_vol == 0:
            std_vol = 1.0

        threshold = mean_vol + self.block_k * std_vol

        is_block = volume > threshold
        block_dir = "BUY" if delta > 0 else "SELL"
        block_ratio = volume / threshold if is_block else 0.0

        # Sweep info from opt_flow
        has_sweep = sweep_count > 0
        sweep_side = "call" if call_buy_vol_delta > put_buy_vol_delta else "put"

        signal = "NEUTRAL"
        score = 0.0

        if is_block and has_sweep:
            aligned = (block_dir == "BUY" and sweep_side == "call") or (
                block_dir == "SELL" and sweep_side == "put"
            )
            score = min(0.95, 0.5 + block_ratio / 10 * 0.3 + sweep_count / 10 * 0.2)
            if aligned:
                signal = "LONG" if block_dir == "BUY" else "SHORT"
            else:
                signal = "MIXED"
        elif is_block:
            score = min(0.55, 0.3 + block_ratio / 10 * 0.25)
            signal = "LONG" if block_dir == "BUY" else "SHORT"
        elif has_sweep:
            score = min(0.45, 0.2 + sweep_count / 10 * 0.25)
            signal = "LONG" if sweep_side == "call" else "SHORT"

        if score < 0.3:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "is_block": is_block,
            "has_sweep": has_sweep,
            "block_dir": block_dir,
            "sweep_side": sweep_side,
        }


class VolumeBubbleGammaEngine:
    def __init__(
        self,
        ticker: str,
        rolling_window: int = 20,
        bubble_sigma: float = 3.0,
        gamma_ratio_min: float = 0.10,
    ):
        self.ticker = ticker
        self.rolling_window = rolling_window
        self.bubble_sigma = bubble_sigma
        self.gamma_ratio_min = gamma_ratio_min
        self.history = deque(maxlen=rolling_window + 5)

    def update(
        self, close: float, volume: float, delta: float, chain_snap: Any | None
    ) -> dict[str, Any]:
        self.history.append({"volume": volume, "delta": delta})

        if len(self.history) < self.rolling_window:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        df = pd.DataFrame(self.history)
        mean_vol = df["volume"].rolling(self.rolling_window).mean().iloc[-1]
        std_vol = df["volume"].rolling(self.rolling_window).std().iloc[-1]

        if pd.isna(std_vol) or std_vol == 0:
            std_vol = 1.0

        threshold = mean_vol + self.bubble_sigma * std_vol
        sigma_dist = (volume - mean_vol) / std_vol

        is_bubble = volume > threshold
        bubble_dir = "BUY" if delta > 0 else "SELL"

        # Gamma Cluster calculation ATM
        gamma_ratio = 0.0
        if chain_snap and chain_snap.strikes:
            # Encontrar el strike más cercano al precio
            strikes = [s.strike for s in chain_snap.strikes]
            diffs = np.abs(np.array(strikes) - close)
            atm_idx = np.argmin(diffs)
            s = chain_snap.strikes[atm_idx]

            gex_net = (s.call_gamma * s.call_oi - s.put_gamma * s.put_oi) * close * 100.0
            oi_total = s.call_oi + s.put_oi

            gamma_ratio = abs(gex_net) / (oi_total + 1)

        gamma_cluster = gamma_ratio > self.gamma_ratio_min

        signal = "NEUTRAL"
        score = 0.0

        if is_bubble and gamma_cluster:
            score = min(0.92, 0.5 + sigma_dist / 6 * 0.3 + gamma_ratio * 0.2)
            signal = "LONG" if bubble_dir == "BUY" else "SHORT"
        elif is_bubble:
            score = round(min(0.35, sigma_dist / 6 * 0.3), 3)
            signal = "FAKE_BUBBLE"

        if score < 0.3:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "gamma_ratio": gamma_ratio,
            "sigma_dist": sigma_dist,
        }


class IcebergVannaEngine:
    def __init__(self, ticker: str):
        self.ticker = ticker

    def update(
        self, open: float, high: float, low: float, close: float, volume: float, delta: float
    ) -> dict[str, Any]:
        """
        Proxy Iceberg Detector without Spot tick data:
        If the candle has unusually high volume but the high-low spread is extremely tight,
        it suggests an iceberg/absorption order keeping the price pinned.
        """
        spread = high - low
        price_range = spread / close

        # Umbrales heurísticos: Volumen significativo, pero spread ínfimo
        is_iceberg_proxy = (price_range < 0.0005) and (
            volume > 50000
        )  # Ajustar volumen según activo

        signal = "NEUTRAL"
        score = 0.0

        if is_iceberg_proxy:
            score = 0.60
            signal = "LONG" if delta > 0 else "SHORT"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "is_iceberg_proxy": is_iceberg_proxy,
            "spread_pct": price_range,
        }
