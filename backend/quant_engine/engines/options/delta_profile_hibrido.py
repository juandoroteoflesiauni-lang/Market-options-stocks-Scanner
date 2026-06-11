from collections import deque
from typing import Any

import numpy as np
import pandas as pd


class DeltaProfileHibridoEngine:
    def __init__(
        self,
        ticker: str,
        lookback: int = 120,
        price_bins: int = 50,
        proximity_pct: float = 0.005,
    ):
        self.ticker = ticker
        self.lookback = lookback
        self.price_bins = price_bins
        self.proximity_pct = proximity_pct
        self.history = deque(maxlen=lookback)

    def update(
        self,
        high: float,
        low: float,
        close: float,
        spot_delta: float,
        options_net_flow: float,  # Can be NDDE or net_shadow_delta
    ) -> dict[str, Any]:
        """
        Updates the Spot Delta Profile and crosses it with the Options Net Flow direction.
        """
        self.history.append({"high": high, "low": low, "close": close, "delta": spot_delta})

        if len(self.history) < 10:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        df = pd.DataFrame(self.history)
        p_min = df["low"].min()
        p_max = df["high"].max()

        if p_max <= p_min:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        edges = np.linspace(p_min, p_max, self.price_bins + 1)
        centers = (edges[:-1] + edges[1:]) / 2.0
        delta_arr = np.zeros(self.price_bins)

        for _, row in df.iterrows():
            mask = (centers >= row["low"]) & (centers <= row["high"])
            count = mask.sum()
            if count > 0:
                delta_arr[mask] += row["delta"] / count

        # Profile is built.
        # Find the bin closest to current price
        diffs = np.abs(centers - close)
        near_idx = int(np.argmin(diffs))

        if diffs[near_idx] / close > self.proximity_pct:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        current_spot_delta = float(delta_arr[near_idx])
        max_abs_delta = float(np.abs(delta_arr).max())

        rel_strength = abs(current_spot_delta) / max_abs_delta if max_abs_delta > 0 else 0.0

        # Confluence Spot vs Options Flow
        kind = "NEUTRAL"
        if current_spot_delta > 0 and options_net_flow > 0:
            kind = "ACCUMULATION"
        elif current_spot_delta < 0 and options_net_flow < 0:
            kind = "DISTRIBUTION"
        elif current_spot_delta > 0 and options_net_flow < 0:
            kind = "DISGUISED_DISTRIBUTION"
        elif current_spot_delta < 0 and options_net_flow > 0:
            kind = "SILENT_ACCUMULATION"

        # Score calculation
        score = min(0.95, rel_strength * 0.9 + 0.1)

        signal = "NEUTRAL"
        if kind == "ACCUMULATION":
            signal = "LONG"
        elif kind == "DISTRIBUTION":
            signal = "SHORT"
        elif kind in ("DISGUISED_DISTRIBUTION", "SILENT_ACCUMULATION"):
            signal = "NEUTRAL"
            score *= 0.7  # Trampa institucional

        if score < 0.3:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "type": kind,
            "spot_delta": current_spot_delta,
            "options_net_flow": options_net_flow,
        }
