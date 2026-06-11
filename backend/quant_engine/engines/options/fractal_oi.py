"""
Fractales Williams + OI Walls Engine
====================================
Williams fractals validated by crossing with Open Interest (OI) walls.
Issues signals only when a structural fractal coincides with a major OI level.
"""

from typing import Any

import numpy as np
import pandas as pd


class FractalOIEngine:
    """
    Stateful engine that detects Williams Fractals and validates them against OI walls.
    """

    def __init__(
        self,
        ticker: str,
        tolerancia_pct: float = 0.002,
        percentil_pared: float = 0.80,
        top_n_paredes: int = 10,
        ventana_fractal: int = 2,
        min_oi_absoluto: int = 1000,
    ):
        self.ticker = ticker
        self.tolerancia_pct = tolerancia_pct
        self.percentil_pared = percentil_pared
        self.top_n_paredes = top_n_paredes
        self.ventana_fractal = ventana_fractal
        self.min_oi_absoluto = min_oi_absoluto

        self._high_buf: list[float] = []
        self._low_buf: list[float] = []
        self._close_buf: list[float] = []
        self._history: list[dict] = []

    def _paredes_oi(self, chain_calls: pd.DataFrame, chain_puts: pd.DataFrame) -> pd.DataFrame:
        if chain_calls.empty or chain_puts.empty:
            return pd.DataFrame(columns=["strike", "oi_total", "ratio_cp", "es_pared"])

        calls = chain_calls[["strike", "open_interest"]].copy()
        calls.columns = ["strike", "oi_calls"]

        puts = chain_puts[["strike", "open_interest"]].copy()
        puts.columns = ["strike", "oi_puts"]

        merged = (
            pd.merge(calls, puts, on="strike", how="outer")
            .fillna(0)
            .assign(oi_total=lambda d: d["oi_calls"] + d["oi_puts"])
            .sort_values("oi_total", ascending=False)
            .reset_index(drop=True)
        )

        umbral = merged["oi_total"].quantile(self.percentil_pared)
        merged["es_pared"] = merged["oi_total"] >= umbral
        merged["ratio_cp"] = (merged["oi_calls"] / merged["oi_puts"].replace(0, np.nan)).fillna(
            np.inf
        )

        paredes = merged[merged["es_pared"]].head(self.top_n_paredes).reset_index(drop=True)
        return paredes[paredes["oi_total"] >= self.min_oi_absoluto].reset_index(drop=True)

    def _clasificar_pared(self, ratio_cp: float) -> str:
        if ratio_cp > 1.5:
            return "resistencia_dura"
        elif ratio_cp < 0.7:
            return "soporte_duro"
        return "neutral"

    def update(
        self,
        high: float,
        low: float,
        close: float,
        chain: list[dict[str, Any]],
        timestamp: pd.Timestamp,
    ) -> dict:
        self._high_buf.append(high)
        self._low_buf.append(low)
        self._close_buf.append(close)

        max_len = self.ventana_fractal * 2 + 50
        if len(self._high_buf) > max_len:
            self._high_buf.pop(0)
            self._low_buf.pop(0)
            self._close_buf.pop(0)

        s_high = pd.Series(self._high_buf)
        s_low = pd.Series(self._low_buf)

        # 1. Detectar Fractales
        n = len(s_high)
        f_up = False
        f_down = False

        if n >= 2 * self.ventana_fractal + 1:
            i = n - self.ventana_fractal - 1  # Eval the fractal at the delayed bar
            win_h = s_high.iloc[i - self.ventana_fractal : i + self.ventana_fractal + 1]
            win_l = s_low.iloc[i - self.ventana_fractal : i + self.ventana_fractal + 1]

            if s_high.iloc[i] == win_h.max() and (win_h == s_high.iloc[i]).sum() == 1:
                f_up = True
            if s_low.iloc[i] == win_l.min() and (win_l == s_low.iloc[i]).sum() == 1:
                f_down = True

        # 2. Paredes OI
        calls_list = [r for r in chain if r.get("option_type", "").upper() == "CALL"]
        puts_list = [r for r in chain if r.get("option_type", "").upper() == "PUT"]

        df_calls = (
            pd.DataFrame(calls_list)
            if calls_list
            else pd.DataFrame(columns=["strike", "open_interest"])
        )
        df_puts = (
            pd.DataFrame(puts_list)
            if puts_list
            else pd.DataFrame(columns=["strike", "open_interest"])
        )

        paredes = self._paredes_oi(df_calls, df_puts)

        # 3. Validación Cruzada
        f_up_valid = False
        f_down_valid = False
        strike_asoc = 0.0
        oi_total = 0.0
        ratio_cp = 0.0
        tipo_pared = "NONE"
        distancia_pct = 0.0
        zona_rechazo = False

        if not paredes.empty:
            strikes_arr = paredes["strike"].values
            oi_map = paredes.set_index("strike")["oi_total"].to_dict()
            rc_map = paredes.set_index("strike")["ratio_cp"].to_dict()

            def strike_cercano(precio: float):
                diffs = np.abs(strikes_arr - precio)
                idx_min = diffs.argmin()
                s = strikes_arr[idx_min]
                d = diffs[idx_min] / precio
                if d <= self.tolerancia_pct:
                    return s, oi_map[s], rc_map[s], d
                return None, None, None, None

            if f_up:
                precio_eval = s_high.iloc[-self.ventana_fractal - 1]
                s, o, r, d = strike_cercano(precio_eval)
                if s is not None:
                    f_up_valid = True
                    strike_asoc = float(s)
                    oi_total = float(o)
                    ratio_cp = float(r)
                    distancia_pct = float(d)
                    tipo_pared = self._clasificar_pared(ratio_cp)
            elif f_down:
                precio_eval = s_low.iloc[-self.ventana_fractal - 1]
                s, o, r, d = strike_cercano(precio_eval)
                if s is not None:
                    f_down_valid = True
                    strike_asoc = float(s)
                    oi_total = float(o)
                    ratio_cp = float(r)
                    distancia_pct = float(d)
                    tipo_pared = self._clasificar_pared(ratio_cp)

            # Zona rechazo (toca la banda de cualquier pared)
            for _, row in paredes.iterrows():
                banda_sup = row["strike"] * (1 + self.tolerancia_pct)
                banda_inf = row["strike"] * (1 - self.tolerancia_pct)
                if low <= banda_sup and high >= banda_inf:
                    zona_rechazo = True
                    break

        signal = "NEUTRAL"
        strength = 0

        # Fractal down validado sobre pared = soporte institucional → señal long
        if f_down_valid:
            signal = "LONG"
            strength = 4

        # Fractal up validado sobre pared = resistencia institucional → señal short
        elif f_up_valid:
            signal = "SHORT"
            strength = 4

        res = {
            "timestamp": timestamp,
            "ticker": self.ticker,
            "signal": signal,
            "strength": strength,
            "fractal_up": f_up,
            "fractal_down": f_down,
            "zona_rechazo": zona_rechazo,
            "strike": strike_asoc,
            "oi_total": oi_total,
            "ratio_cp": ratio_cp,
            "tipo_pared": tipo_pared,
            "distancia_pct": distancia_pct,
        }
        self._history.append(res)
        return res
