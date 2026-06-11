from typing import Any

import numpy as np


class GEXProfileEngine:
    def __init__(
        self,
        ticker: str,
        gamma_wall_top_n: int = 3,
        void_zone_threshold: float = 0.20,
        proximity_pct: float = 0.005,
    ):
        self.ticker = ticker
        self.gamma_wall_top_n = gamma_wall_top_n
        self.void_zone_threshold = void_zone_threshold
        self.proximity_pct = proximity_pct

    def update(self, close: float, chain_snap: Any | None) -> dict[str, Any]:
        """
        Computes GEX Profile using the current options chain snapshot.
        """
        if chain_snap is None or not chain_snap.strikes:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        # Calculate GEX per strike
        # GEX = (Call_Gamma * Call_OI - Put_Gamma * Put_OI) * close * 100
        strikes = []
        gex_vals = []
        for s in chain_snap.strikes:
            # Aproximación estándar de GEX neto por strike
            gex_net = (s.call_gamma * s.call_oi - s.put_gamma * s.put_oi) * close * 100.0
            strikes.append(s.strike)
            gex_vals.append(gex_net)

        if not strikes:
            return {"signal": "NEUTRAL", "strength": 0, "score": 0.0}

        strikes = np.array(strikes)
        gex_vals = np.array(gex_vals)

        # Gamma Walls
        top_idx = np.argsort(gex_vals)[-self.gamma_wall_top_n :][::-1]
        gamma_walls = sorted(strikes[top_idx].tolist())

        # Gamma Voids
        gex_max = gex_vals.max()
        void_thresh = gex_max * self.void_zone_threshold
        gamma_voids = strikes[gex_vals < void_thresh].tolist()

        # Gamma Flip
        sign_changes = np.where(np.diff(np.sign(gex_vals)))[0]
        gamma_flip = None
        if len(sign_changes) > 0:
            mid_idx = len(strikes) // 2
            closest = min(sign_changes, key=lambda i: abs(i - mid_idx))
            gamma_flip = float(strikes[closest])

        total_gex = float(gex_vals.sum())
        gex_regime = "POSITIVE" if total_gex > 0 else "NEGATIVE"

        # Signal Logic
        def price_in_zone(p, levels, tol):
            return any(abs(p - lvl) / p <= tol for lvl in levels)

        in_void = price_in_zone(close, gamma_voids, self.proximity_pct * 0.5)
        at_wall = price_in_zone(close, gamma_walls, self.proximity_pct)
        near_flip = gamma_flip is not None and (
            abs(close - gamma_flip) / close <= self.proximity_pct
        )

        dist_to_wall = min((abs(close - w) / close for w in gamma_walls), default=1.0)

        if in_void and gex_regime == "NEGATIVE":
            score = 0.90
            signal = "VOID_ACCELERATION"
        elif in_void:
            score = 0.70
            signal = "VOID_TRANSIT"
        elif at_wall:
            score = 0.75
            signal = "WALL_BOUNCE"
        elif near_flip:
            score = 0.60
            signal = "FLIP_PROXIMITY"
        else:
            score = round(min(0.3, 0.3 * (1 - dist_to_wall / 0.02)), 3)
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "strength": int(score * 100),
            "score": score,
            "gamma_walls": gamma_walls,
            "gamma_flip": gamma_flip,
            "gex_regime": gex_regime,
            "in_void": in_void,
        }
