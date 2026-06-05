"""
Speed instability (∂Γ/∂S) — SWX profile, traps, decay, GEX vs SWX.
Vectorised spot profile (vs per-leg Python loops in the reference script).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import cast, Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]
from scipy.stats import norm  # type: ignore[import-not-found, import-untyped]

warnings.filterwarnings("ignore", category=RuntimeWarning)


def _d1(S: np.ndarray[Any, np.dtype[Any]], K: np.ndarray[Any, np.dtype[Any]], r: float, sigma: np.ndarray[Any, np.dtype[Any]], T: np.ndarray[Any, np.dtype[Any]]) -> np.ndarray[Any, np.dtype[Any]]:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(
            (T > 1e-10) & (sigma > 1e-10),
            (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T)),
            np.where(S >= K, np.inf, -np.inf),
        )


def bs_gamma(S: np.ndarray[Any, np.dtype[Any]], K: np.ndarray[Any, np.dtype[Any]], r: float, sigma: np.ndarray[Any, np.dtype[Any]], T: np.ndarray[Any, np.dtype[Any]]) -> np.ndarray[Any, np.dtype[Any]]:
    d1 = _d1(S, K, r, sigma, T)
    sqrtT = np.sqrt(np.maximum(T, 1e-10))
    denom = S * sigma * sqrtT
    return np.where(denom > 1e-12, norm.pdf(d1) / denom, 0.0)


def bs_speed(S: np.ndarray[Any, np.dtype[Any]], K: np.ndarray[Any, np.dtype[Any]], r: float, sigma: np.ndarray[Any, np.dtype[Any]], T: np.ndarray[Any, np.dtype[Any]]) -> np.ndarray[Any, np.dtype[Any]]:
    d1 = _d1(S, K, r, sigma, T)
    gamma = bs_gamma(S, K, r, sigma, T)
    sqrtT = np.sqrt(np.maximum(T, 1e-10))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(
            (sigma > 1e-10) & (T > 1e-10),
            -(gamma / np.maximum(S, 1e-10)) * (1.0 + d1 / (sigma * sqrtT)),
            0.0,
        )


def bs_delta(S: np.ndarray[Any, np.dtype[Any]], K: np.ndarray[Any, np.dtype[Any]], r: float, sigma: np.ndarray[Any, np.dtype[Any]], T: np.ndarray[Any, np.dtype[Any]], option_type: np.ndarray[Any, np.dtype[Any]]) -> np.ndarray[Any, np.dtype[Any]]:
    d1 = _d1(S, K, r, sigma, T)
    call_delta = norm.cdf(d1)
    return np.where(option_type == "C", call_delta, call_delta - 1.0)


@dataclass
class OptionsChain:
    df: pd.DataFrame
    r: float = 0.05

    def __post_init__(self) -> None:
        required = {"strike", "option_type", "sigma", "time_to_expiry", "spot_price", "open_interest"}
        missing = required - set(self.df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")
        self.df = self.df.copy()
        self.df["option_type"] = self.df["option_type"].astype(str).str.upper()
        S = self.df["spot_price"].values.astype(float)
        K = self.df["strike"].values.astype(float)
        sig = self.df["sigma"].values.astype(float)
        T = self.df["time_to_expiry"].values.astype(float)
        ot = self.df["option_type"].values
        self.df["gamma_bs"] = bs_gamma(S, K, self.r, sig, T)
        self.df["delta_bs"] = bs_delta(S, K, self.r, sig, T, ot)
        self.df["speed_bs"] = bs_speed(S, K, self.r, sig, T)
        self.df["oi_sign"] = np.where(ot == "C", 1.0, -1.0)


class SpeedInstabilityEngine:
    MULTIPLIER = 100

    def __init__(self, chain: OptionsChain):
        self.chain = chain
        self._computed = False
        self.df: pd.DataFrame | None = None

    def compute(self) -> SpeedInstabilityEngine:
        df = self.chain.df.copy()
        spot = df["spot_price"].values.astype(float)
        df["swx"] = df["speed_bs"] * df["open_interest"] * self.MULTIPLIER * spot
        df["net_swx"] = df["swx"] * df["oi_sign"]
        df["abs_speed"] = df["speed_bs"].abs()
        df["abs_swx"] = df["swx"].abs()
        mu = df["abs_speed"].mean()
        sig = df["abs_speed"].std()
        df["speed_zscore"] = (df["abs_speed"] - mu) / (sig + 1e-12)
        self.df = df
        self._computed = True
        return self

    def _assert_computed(self) -> None:
        assert self.df is not None
        if not self._computed or self.df is None:
            raise RuntimeError("Call .compute() first.")

    def gamma_traps(self, z_threshold: float = 1.5) -> pd.DataFrame:
        self._assert_computed()
        assert self.df is not None
        traps = self.df[self.df["speed_zscore"] >= z_threshold].copy()
        traps = traps.sort_values("abs_swx", ascending=False)
        return traps[
            ["strike", "option_type", "speed_bs", "swx", "net_swx", "speed_zscore", "open_interest", "gamma_bs", "sigma"]
        ].reset_index(drop=True)

    def instability_zones(self, n: int = 3) -> pd.DataFrame:
        self._assert_computed()
        assert self.df is not None
        agg = self.df.groupby("strike")["net_swx"].sum().reset_index().rename(columns={"net_swx": "total_net_swx"})
        agg["abs_total_net_swx"] = agg["total_net_swx"].abs()
        top = agg.nlargest(n, "abs_total_net_swx").reset_index(drop=True)
        top["regime"] = top["total_net_swx"].apply(
            lambda x: "ACCELERATION (rally cliff)" if x > 0 else "DECELERATION (sell vacuum)"
        )
        return top

    def speed_decay(self, strike: float, option_type: str = "C", t_range: tuple[float, float] = (0.001, 1.0), n_points: int = 120) -> pd.DataFrame:
        self._assert_computed()
        ref = self.chain.df.iloc[0]
        S = float(ref["spot_price"])
        r = self.chain.r
        diffs = (self.chain.df["strike"] - strike).abs()
        closest = self.chain.df.loc[diffs.idxmin()]
        sigma = float(closest["sigma"])
        oi = float(closest["open_interest"])
        T_arr = np.linspace(t_range[1], t_range[0], n_points)
        K_arr = np.full_like(T_arr, strike, dtype=float)
        S_arr = np.full_like(T_arr, S, dtype=float)
        sig_arr = np.full_like(T_arr, sigma, dtype=float)
        gamma_arr = bs_gamma(S_arr, K_arr, r, sig_arr, T_arr)
        speed_arr = bs_speed(S_arr, K_arr, r, sig_arr, T_arr)
        swx_arr = speed_arr * oi * self.MULTIPLIER * S
        return pd.DataFrame(
            {
                "time_to_expiry": T_arr,
                "days_to_expiry": T_arr * 252.0,
                "gamma": gamma_arr,
                "speed": speed_arr,
                "swx": swx_arr,
            }
        )

    def _speed_profile_data(self, spot_range_pct: float = 0.15, n_points: int = 240) -> pd.DataFrame:
        """Vectorised net SWX / GEX vs hypothetical spot (O(N·M) in numpy)."""
        self._assert_computed()
        assert self.df is not None
        ref_spot = float(self.df["spot_price"].iloc[0])
        s_low = ref_spot * (1.0 - spot_range_pct)
        s_high = ref_spot * (1.0 + spot_range_pct)
        spot_grid = np.linspace(s_low, s_high, n_points)

        strikes = self.df["strike"].to_numpy(dtype=np.float64)
        sigs = self.df["sigma"].to_numpy(dtype=np.float64)
        Ts = self.df["time_to_expiry"].to_numpy(dtype=np.float64)
        oi = self.df["open_interest"].to_numpy(dtype=np.float64)
        oi_sign = self.df["oi_sign"].to_numpy(dtype=np.float64)
        r = self.chain.r

        S = spot_grid[:, np.newaxis]
        K = strikes[np.newaxis, :]
        sigma = sigs[np.newaxis, :]
        T = Ts[np.newaxis, :]

        speed_m = bs_speed(S, K, r, sigma, T)
        gamma_m = bs_gamma(S, K, r, sigma, T)
        swx_m = speed_m * oi * self.MULTIPLIER * S * oi_sign
        gex_m = gamma_m * oi * self.MULTIPLIER * S * oi_sign

        return pd.DataFrame(
            {
                "spot": spot_grid,
                "net_swx": swx_m.sum(axis=1),
                "net_gex": gex_m.sum(axis=1),
            }
        )

    def summary_report(self) -> dict[str, Any]:
        self._assert_computed()
        assert self.df is not None
        zones = self.instability_zones(3)
        traps = self.gamma_traps()
        total_swx = float(self.df["net_swx"].sum())
        max_abs = float(self.df["abs_swx"].max())
        top_trap = float(traps.iloc[0]["strike"]) if not traps.empty else None
        return {
            "total_net_swx": total_swx,
            "max_abs_swx_single_strike": max_abs,
            "n_gamma_traps": int(len(traps)),
            "top_gamma_trap_strike": top_trap,
            "instability_zones": zones.to_dict("records"),
            "book_bias": "LONG SPEED (buy-climax prone)"
            if total_swx > 0
            else "SHORT SPEED (sell-vacuum prone)",
        }


def portfolio_df_to_speed_chain(df_portfolio: pd.DataFrame, spot: float, r: float) -> pd.DataFrame:
    """Map shadow-delta portfolio_df (CALL/PUT, iv, quantity, expiry) → OptionsChain schema."""
    out = df_portfolio.copy()
    out["sigma"] = out["iv"].astype(float)
    out["time_to_expiry"] = out["expiry"].astype(float)
    out["spot_price"] = float(spot)
    out["open_interest"] = np.maximum(out["quantity"].astype(float).round().astype(int), 0)
    ot = out["option_type"].astype(str).str.upper()
    out["option_type"] = np.where(ot.str.startswith("C"), "C", "P")
    return out[["strike", "option_type", "sigma", "time_to_expiry", "spot_price", "open_interest"]]


def compute_speed_instability_payload(
    df_portfolio: pd.DataFrame,
    spot: float,
    r: float = 0.04,
    max_legs: int = 180,
    profile_points: int = 220,
    z_trap: float = 1.6,
    max_decay_curves: int = 3,
) -> dict[str, Any]:
    """JSON-serialisable payload for API / frontend Plotly."""
    if df_portfolio is None or df_portfolio.empty or spot <= 0:
        return {"ok": False, "error": "empty_portfolio"}
    df = df_portfolio.copy()
    if len(df) > max_legs:
        df = df.nlargest(max_legs, "quantity").reset_index(drop=True)
    try:
        chain_df = portfolio_df_to_speed_chain(df, spot, r)
        if chain_df["open_interest"].sum() <= 0:
            return {"ok": False, "error": "zero_oi"}
        eng = SpeedInstabilityEngine(OptionsChain(chain_df, r=r))
        eng.compute()
        assert eng.df is not None
        profile = eng._speed_profile_data(0.15, min(profile_points, 280))
        traps = eng.gamma_traps(z_threshold=z_trap)
        zones_list = eng.instability_zones(3).to_dict("records")
        total_swx = float(eng.df["net_swx"].sum())
        rep_summary = {
            "total_net_swx": total_swx,
            "max_abs_swx_single_strike": float(eng.df["abs_swx"].max()),
            "n_gamma_traps": int(len(traps)),
            "top_gamma_trap_strike": float(traps.iloc[0]["strike"]) if not traps.empty else None,
            "book_bias": "LONG SPEED (buy-climax prone)"
            if total_swx > 0
            else "SHORT SPEED (sell-vacuum prone)",
        }

        if len(profile) > 240:
            profile = profile.iloc[:: max(1, len(profile) // 240)].reset_index(drop=True)

        calls = eng.df[eng.df["option_type"] == "C"][["strike", "speed_bs"]]
        puts = eng.df[eng.df["option_type"] == "P"][["strike", "speed_bs"]]
        by_strike = (
            calls.rename(columns={"speed_bs": "call_speed"})
            .merge(puts.rename(columns={"speed_bs": "put_speed"}), on="strike", how="outer")
            .fillna(0.0)
            .sort_values("strike")
        )
        speed_by_strike = [
            {"strike": float(r.strike), "call_speed": float(r.call_speed), "put_speed": float(r.put_speed)}
            for _, r in by_strike.iterrows()
        ]

        top_strikes = (
            traps.head(max_decay_curves)["strike"].tolist()
            if not traps.empty
            else eng.df.nlargest(max_decay_curves, "abs_speed")["strike"].tolist()
        )
        decay_series: list[dict[str, Any]] = []
        decay_colors = ["#f0e68c", "#ffa07a", "#20b2aa"]
        for i, k in enumerate(top_strikes[:max_decay_curves]):
            decay_df = eng.speed_decay(float(k), n_points=100)
            decay_series.append(
                {
                    "label": f"|Speed| K={float(k):.0f}",
                    "strike": float(k),
                    "color": decay_colors[i % len(decay_colors)],
                    "days_to_expiry": decay_df["days_to_expiry"].astype(float).tolist(),
                    "abs_speed": decay_df["speed"].abs().astype(float).tolist(),
                }
            )

        swx_abs = eng.df["abs_swx"].to_numpy(dtype=float)
        lo, hi = float(swx_abs.min()), float(swx_abs.max())
        ref_spot = float(eng.df["spot_price"].iloc[0])
        scatter: list[dict[str, Any]] = []
        for row in eng.df.itertuples(index=False):
            gex = float(row.gamma_bs * row.open_interest * eng.MULTIPLIER * ref_spot)
            mn = (float(row.abs_swx) - lo) / (hi - lo + 1e-12)
            scatter.append(
                {
                    "strike": float(row.strike),
                    "option_type": str(row.option_type),
                    "gex": gex,
                    "net_swx": float(row.net_swx),
                    "speed_bs": float(row.speed_bs),
                    "marker_norm": float(np.clip(mn, 0.0, 1.0)),
                }
            )

        trap_rows = traps.head(12)
        gamma_traps = trap_rows.to_dict("records")

        return {
            "ok": True,
            "spot": float(spot),
            "summary": rep_summary,
            "zones": zones_list,
            "profile": profile.to_dict("records"),
            "speed_by_strike": speed_by_strike,
            "speed_decay": decay_series,
            "scatter": scatter,
            "gamma_traps": gamma_traps,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}