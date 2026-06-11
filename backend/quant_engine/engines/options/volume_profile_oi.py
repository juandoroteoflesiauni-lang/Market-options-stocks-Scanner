from collections import deque
from typing import Any

import numpy as np
import pandas as pd


class VolumeProfileOIEngine:
    def __init__(
        self,
        ticker: str,
        lookback: int = 390,
        price_bins: int = 50,
        value_area_pct: float = 0.70,
        oi_proximity_pct: float = 0.003,
    ):
        self.ticker = ticker
        self.lookback = lookback
        self.price_bins = price_bins
        self.value_area_pct = value_area_pct
        self.oi_proximity_pct = oi_proximity_pct
        self.history = deque(maxlen=lookback)

    def update(
        self,
        high: float,
        low: float,
        close: float,
        volume: float,
        chain_snap: Any | None,
    ) -> dict[str, Any]:
        """
        Updates the volume profile incrementally and detects Institutional Walls.
        """
        self.history.append({"high": high, "low": low, "close": close, "volume": volume})

        if len(self.history) < 5 or chain_snap is None or not chain_snap.strikes:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        # 1. Construir Perfil de Volumen
        df = pd.DataFrame(self.history)
        p_min = df["low"].min()
        p_max = df["high"].max()

        if p_max <= p_min:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        edges = np.linspace(p_min, p_max, self.price_bins + 1)
        centers = (edges[:-1] + edges[1:]) / 2.0
        vol_arr = np.zeros(self.price_bins)

        # Distribuir volumen
        for _, row in df.iterrows():
            mask = (centers >= row["low"]) & (centers <= row["high"])
            count = mask.sum()
            if count > 0:
                vol_arr[mask] += row["volume"] / count

        poc_idx = int(np.argmax(vol_arr))
        poc = float(centers[poc_idx])

        # 2. Obtener Strike con mayor OI (OI Wall)
        max_oi_strike = 0.0
        max_oi_val = 0
        for s in chain_snap.strikes:
            total_oi = s.call_oi + s.put_oi
            if total_oi > max_oi_val:
                max_oi_val = total_oi
                max_oi_strike = s.strike

        if max_oi_strike == 0.0:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        # 3. Calcular confluencia
        poc_oi_dist = abs(poc - max_oi_strike) / poc
        is_institutional_wall = poc_oi_dist <= self.oi_proximity_pct

        price_dist = abs(close - max_oi_strike) / close
        at_wall = price_dist <= self.oi_proximity_pct

        score = 0.0
        signal = "NEUTRAL"

        if is_institutional_wall and at_wall:
            # Máxima fricción detectada
            score = 0.85
            signal = "FRICTION"
        elif at_wall:
            score = 0.50
            signal = "AT_WALL"
        elif is_institutional_wall:
            score = 0.30
            signal = "WALL_ACTIVE"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "poc": poc,
            "oi_wall_strike": max_oi_strike,
            "is_institutional_wall": is_institutional_wall,
        }
