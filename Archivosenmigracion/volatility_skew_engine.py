"""
Volatility smile / skew (polynomial or SABR fit) — API-friendly, no Plotly import.
OTM-composite curve for fitting avoids duplicate log-moneyness from call+put rows.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, cast

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]
from scipy.optimize import curve_fit  # type: ignore[import-not-found, import-untyped]
from scipy.stats import norm  # type: ignore[import-not-found, import-untyped]

warnings.filterwarnings("ignore", category=RuntimeWarning)


def polynomial_smile(x: np.ndarray[Any, np.dtype[Any]], a: float, b: float, c: float) -> np.ndarray[Any, np.dtype[Any]]:
    return a * x**2 + b * x + c


def sabr_approximation(
    strike: np.ndarray[Any, np.dtype[Any]],
    forward: float,
    alpha: float,
    beta: float,
    rho: float,
    nu: float,
    t: float = 1.0,
) -> np.ndarray[Any, np.dtype[Any]]:
    K = np.asarray(strike, dtype=float)
    F = float(forward)
    eps = 1e-8
    atm_vol = alpha / (F ** (1 - beta)) * (
        1
        + (
            (1 - beta) ** 2 / 24 * alpha**2 / F ** (2 * (1 - beta))
            + rho * beta * nu * alpha / (4 * F ** (1 - beta))
            + (2 - 3 * rho**2) / 24 * nu**2
        )
        * t
    )
    FK = F * K
    log_FK = np.log(F / (K + eps))
    FK_mid = (FK) ** ((1 - beta) / 2)
    z = nu / alpha * FK_mid * log_FK
    x_z = np.log((np.sqrt(1 - 2 * rho * z + z**2) + z - rho) / (1 - rho))
    ratio = np.where(np.abs(x_z) < eps, 1.0, z / (x_z + eps))
    A = alpha / (
        FK_mid
        * (1 + (1 - beta) ** 2 / 24 * log_FK**2 + (1 - beta) ** 4 / 1920 * log_FK**4)
    )
    B = (
        1
        + (
            (1 - beta) ** 2 / 24 * alpha**2 / FK ** (1 - beta)
            + rho * beta * nu * alpha / (4 * FK_mid)
            + (2 - 3 * rho**2) / 24 * nu**2
        )
        * t
    )
    iv_sabr = A * ratio * B
    atm_mask = np.abs(log_FK) < 1e-4
    iv_sabr = np.where(atm_mask, atm_vol, iv_sabr)
    return cast(np.ndarray[Any, np.dtype[Any]], np.clip(iv_sabr, 1e-4, 5.0))


@dataclass
class SkewMetrics:
    slope_25d: float = 0.0
    convexity: float = 0.0
    iv_25d_put: float = 0.0
    iv_25d_call: float = 0.0
    iv_atm: float = 0.0
    iv_10d_put: float = 0.0
    iv_10d_call: float = 0.0
    regime: str = "Normal Skew"
    tail_risk_alert: bool = False
    alert_message: str = ""
    poly_coeffs: np.ndarray[Any, np.dtype[Any]] = field(default_factory=lambda: np.zeros(3))
    sabr_params: dict[str, Any] | None = None


class VolatilitySkewEngine:
    CRASH_RISK_SLOPE_THRESHOLD = 0.08
    CRASH_RISK_CONVEXITY_THRESHOLD = 0.03
    BULLISH_SLOPE_THRESHOLD = -0.02
    TAIL_RISK_MULTIPLIER = 1.20

    def __init__(
        self,
        spot_price: float,
        risk_free_rate: float = 0.05,
        time_to_expiry: float = 30 / 365,
        fit_model: str = "polynomial",
    ):
        self.spot_price = spot_price
        self.risk_free_rate = risk_free_rate
        self.time_to_expiry = time_to_expiry
        self.fit_model = fit_model
        self._raw_df: pd.DataFrame | None = None
        self._curve_fit_df: pd.DataFrame | None = None
        self._fitted_params: np.ndarray[Any, np.dtype[Any]] | None = None
        self._strike_grid: np.ndarray[Any, np.dtype[Any]] | None = None
        self._iv_fitted: np.ndarray[Any, np.dtype[Any]] | None = None
        self._convexity_history: list[float] = []
        self.metrics: SkewMetrics | None = None

    def fit(
        self,
        options_df: pd.DataFrame,
        curve_fit_df: pd.DataFrame | None = None,
    ) -> VolatilitySkewEngine:
        required_cols = {"strike", "option_type", "delta", "iv"}
        missing = required_cols - set(options_df.columns)
        if missing:
            raise ValueError(f"Columnas faltantes en el DataFrame: {missing}")
        df = options_df.copy()
        df["option_type"] = df["option_type"].str.lower().str.strip()
        df = df.sort_values("strike").reset_index(drop=True)
        self._raw_df = df
        self._curve_fit_df = curve_fit_df
        self._fit_curve()
        return self

    def _fit_curve(self) -> None:
        assert self._raw_df is not None
        df = self._curve_fit_df if self._curve_fit_df is not None else self._raw_df
        strikes = df["strike"].values.astype(float)
        ivs = df["iv"].values.astype(float)
        log_moneyness = np.log(strikes / self.spot_price)

        if self.fit_model == "polynomial":
            try:
                popt_raw, _ = curve_fit(
                    polynomial_smile,
                    log_moneyness,
                    ivs,
                    p0=[0.5, -0.1, float(np.mean(ivs))],
                    maxfev=10000,
                )
                self._fitted_params = cast(np.ndarray[Any, np.dtype[Any]], popt_raw)
            except RuntimeError:
                warnings.warn("Ajuste polinómico no convergió; usando mínimos cuadrados.")
                self._fitted_params = np.polyfit(log_moneyness, ivs, 2)[::-1]

        elif self.fit_model == "sabr":
            forward = self.spot_price * np.exp(self.risk_free_rate * self.time_to_expiry)

            def sabr_wrapper(K: np.ndarray[Any, np.dtype[Any]], alpha: float, beta: float, rho: float, nu: float) -> np.ndarray[Any, np.dtype[Any]]:
                return sabr_approximation(K, forward, alpha, beta, rho, nu, t=self.time_to_expiry)

            try:
                popt_raw, _ = curve_fit(
                    sabr_wrapper,
                    strikes,
                    ivs,
                    p0=[0.3, 0.5, -0.3, 0.4],
                    bounds=([0.001, 0.0, -0.999, 0.001], [5.0, 1.0, 0.999, 5.0]),
                    maxfev=50000,
                )
                self._fitted_params = cast(np.ndarray[Any, np.dtype[Any]], popt_raw)
                self._sabr_forward = forward
            except RuntimeError:
                warnings.warn("SABR no convergió; fallback a polinomio.")
                self.fit_model = "polynomial"
                self._fit_curve()
                return

        k_min = float(strikes.min()) * 0.95
        k_max = float(strikes.max()) * 1.05
        self._strike_grid = np.linspace(k_min, k_max, 160)
        self._iv_fitted = self._predict_iv(self._strike_grid)

    def _predict_iv(self, strikes: np.ndarray[Any, np.dtype[Any]]) -> np.ndarray[Any, np.dtype[Any]]:
        strikes = np.asarray(strikes, dtype=float)
        if self.fit_model == "polynomial":
            log_m = np.log(strikes / self.spot_price)
            assert self._fitted_params is not None
            a, b, c = self._fitted_params
            return polynomial_smile(log_m, a, b, c)
        if self.fit_model == "sabr":
            assert self._fitted_params is not None; alpha, beta, rho, nu = self._fitted_params
            return sabr_approximation(
                strikes, self._sabr_forward, alpha, beta, rho, nu, t=self.time_to_expiry
            )
        return np.full_like(strikes, np.nan)

    def _get_iv_by_delta(self, target_delta: float, option_type: str) -> float:
        assert self._raw_df is not None
        df = self._raw_df
        mask = df["option_type"] == option_type.lower()
        subset = df[mask].copy()
        if subset.empty:
            return float("nan")
        subset["delta_dist"] = (subset["delta"] - target_delta).abs()
        best_row = subset.loc[subset["delta_dist"].idxmin()]
        return float(best_row["iv"])

    def _get_iv_atm(self) -> float:
        if self._fitted_params is not None:
            return float(self._predict_iv(np.array([self.spot_price]))[0])
        assert self._raw_df is not None
        df = self._raw_df.copy()
        df["dist"] = (df["strike"] - self.spot_price).abs()
        return float(df.loc[df["dist"].idxmin(), "iv"])

    def compute_metrics(self, convexity_history: list[float] | None = None) -> SkewMetrics:
        if self._raw_df is None:
            raise RuntimeError("Ejecuta .fit(df) antes de .compute_metrics().")
        metrics = SkewMetrics()
        metrics.iv_25d_put = self._get_iv_by_delta(-0.25, "put")
        metrics.iv_25d_call = self._get_iv_by_delta(0.25, "call")
        metrics.iv_10d_put = self._get_iv_by_delta(-0.10, "put")
        metrics.iv_10d_call = self._get_iv_by_delta(0.10, "call")
        metrics.iv_atm = self._get_iv_atm()
        metrics.slope_25d = metrics.iv_25d_put - metrics.iv_25d_call
        metrics.convexity = (metrics.iv_25d_put + metrics.iv_25d_call) / 2 - metrics.iv_atm
        if self._fitted_params is not None:
            metrics.poly_coeffs = np.array(self._fitted_params)
        history = convexity_history or self._convexity_history
        self._convexity_history.append(metrics.convexity)
        if len(history) >= 15:
            recent_history = history[-15:]
            mean_conv = float(np.mean(recent_history))
            threshold = mean_conv * self.TAIL_RISK_MULTIPLIER
            if metrics.convexity > threshold and mean_conv > 0:
                metrics.tail_risk_alert = True
                pct_above = (metrics.convexity / mean_conv - 1) * 100
                metrics.alert_message = (
                    f"TAIL RISK: convexidad {metrics.convexity:.4f} es {pct_above:.1f}% "
                    f"por encima de la media 15d ({mean_conv:.4f})."
                )
        elif len(history) < 15:
            metrics.alert_message = (
                f"Historial insuficiente ({len(history)}/15) para alerta de tail risk."
            )
        metrics.regime = self._classify_regime(metrics)
        self.metrics = metrics
        return metrics

    def _classify_regime(self, m: SkewMetrics) -> str:
        crash_slope = m.slope_25d >= self.CRASH_RISK_SLOPE_THRESHOLD
        crash_conv = m.convexity >= self.CRASH_RISK_CONVEXITY_THRESHOLD
        bullish = m.slope_25d <= self.BULLISH_SLOPE_THRESHOLD
        if crash_slope and crash_conv:
            return "Crash Risk"
        if bullish:
            return "Bullish Skew"
        return "Normal Skew"

    def local_slope(self, strike: float) -> float:
        h = strike * 0.001
        iv_up = self._predict_iv(np.array([strike + h]))[0]
        iv_down = self._predict_iv(np.array([strike - h]))[0]
        return float((iv_up - iv_down) / (2 * h))

    def local_curvature(self, strike: float) -> float:
        h = strike * 0.001
        iv_up = self._predict_iv(np.array([strike + h]))[0]
        iv_at = self._predict_iv(np.array([strike]))[0]
        iv_down = self._predict_iv(np.array([strike - h]))[0]
        return float((iv_up - 2 * iv_at + iv_down) / h**2)

    def scenario_analysis(self, shock_pct: float) -> dict[str, Any]:
        stressed_strike = self.spot_price * (1 + shock_pct)
        iv_stressed = float(self._predict_iv(np.array([stressed_strike]))[0])
        iv_atm = self._get_iv_atm()
        ratio = iv_stressed / iv_atm if iv_atm > 0 else float("nan")
        return {
            "shock_pct": shock_pct,
            "stressed_strike": stressed_strike,
            "iv_stressed": iv_stressed,
            "iv_atm": iv_atm,
            "iv_premium": iv_stressed - iv_atm,
            "iv_ratio": ratio if not math.isnan(ratio) else None,
        }


def build_skew_frames_from_portfolio(
    df_sd: pd.DataFrame, spot: float, dte_years: float, r: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    from .shadow_delta_engine import bs_delta

    rows: list[dict[str, Any]] = []
    for _, row in df_sd.iterrows():
        K = float(row["strike"])
        iv = float(row["iv"])
        T = float(row["expiry"]) if "expiry" in row.index else float(dte_years)
        is_call = str(row["option_type"]).upper().startswith("C")
        ot_long = "call" if is_call else "put"
        opt_bs = "CALL" if is_call else "PUT"
        try:
            dlt = float(bs_delta(spot, K, T, r, iv, opt_bs))
        except Exception:
            dlt = float("nan")
        rows.append(
            {"strike": K, "option_type": ot_long, "iv": iv, "delta": dlt, "spot_price": spot}
        )
    df_long = pd.DataFrame(rows)
    curve_rows: list[dict[str, Any]] = []
    for K in sorted(df_long["strike"].unique()):
        dfc = df_long[(df_long["strike"] == K) & (df_long["option_type"] == "call")]
        dfp = df_long[(df_long["strike"] == K) & (df_long["option_type"] == "put")]
        if K < spot * 0.999 and not dfp.empty:
            curve_rows.append({"strike": K, "iv": float(dfp.iloc[0]["iv"])})
        elif K > spot * 1.001 and not dfc.empty:
            curve_rows.append({"strike": K, "iv": float(dfc.iloc[0]["iv"])})
        else:
            ivs: list[float] = []
            if not dfc.empty:
                ivs.append(float(dfc.iloc[0]["iv"]))
            if not dfp.empty:
                ivs.append(float(dfp.iloc[0]["iv"]))
            if ivs:
                curve_rows.append({"strike": K, "iv": float(np.mean(ivs))})
    df_curve = (
        pd.DataFrame(curve_rows).sort_values("strike").drop_duplicates(subset=["strike"], keep="first")
    )
    return df_long, df_curve


def compute_volatility_skew_payload(
    df_sd: pd.DataFrame,
    spot: float,
    dte_years: float,
    r: float = 0.04,
) -> dict[str, Any]:
    if df_sd is None or df_sd.empty or spot <= 0:
        return {"ok": False, "error": "empty_portfolio"}
    try:
        df_long, df_curve = build_skew_frames_from_portfolio(df_sd, spot, dte_years, r)
        if len(df_curve) < 5:
            return {"ok": False, "error": "insufficient_smile_points"}
        eng = VolatilitySkewEngine(
            spot_price=float(spot),
            risk_free_rate=float(r),
            time_to_expiry=float(dte_years),
            fit_model="polynomial",
        )
        eng.fit(df_long, df_curve)
        m = eng.compute_metrics()

        def _clean(x: float) -> float:
            try:
                v = float(x)
                return 0.0 if math.isnan(v) else v
            except (TypeError, ValueError):
                return 0.0

        assert eng._strike_grid is not None and eng._iv_fitted is not None
        sg = eng._strike_grid
        idx = np.linspace(0, len(sg) - 1, num=min(100, len(sg)), dtype=int)
        curv_k = [float(sg[i]) for i in idx]
        curv_raw = np.array([eng.local_curvature(k) for k in curv_k], dtype=float)
        mx = float(np.abs(curv_raw).max() + 1e-9)
        curv_norm = (curv_raw / mx).tolist()
        scenarios = [eng.scenario_analysis(s) for s in (-0.05, -0.1, -0.15, -0.2)]
        for s in scenarios:
            if s.get("iv_ratio") is not None and isinstance(s["iv_ratio"], float) and math.isnan(s["iv_ratio"]):
                s["iv_ratio"] = None
        market_points = []
        for row in df_long.itertuples(index=False):
            d = float(row.delta)
            if math.isnan(d):
                d = 0.0
            market_points.append(
                {
                    "strike": float(row.strike),
                    "iv_pct": float(row.iv) * 100.0,
                    "option_type": str(row.option_type),
                    "delta": d,
                }
            )
        fitted = [
            {"strike": float(k), "iv_fitted_pct": float(iv) * 100.0}
            for k, iv in zip(sg.tolist(), eng._iv_fitted.tolist())
        ]
        curv_pts = [{"strike": k, "curvature_norm": v} for k, v in zip(curv_k, curv_norm)]
        coeffs = np.asarray(m.poly_coeffs).astype(float).tolist()
        return {
            "ok": True,
            "spot": float(spot),
            "fit_model": "polynomial",
            "metrics": {
                "slope_25d": _clean(m.slope_25d),
                "convexity": _clean(m.convexity),
                "iv_25d_put": _clean(m.iv_25d_put),
                "iv_25d_call": _clean(m.iv_25d_call),
                "iv_atm": _clean(m.iv_atm),
                "iv_10d_put": _clean(m.iv_10d_put),
                "iv_10d_call": _clean(m.iv_10d_call),
                "regime": str(m.regime),
                "tail_risk_alert": bool(m.tail_risk_alert),
                "alert_message": str(m.alert_message or ""),
                "poly_coeffs": coeffs,
            },
            "market_points": market_points,
            "fitted_curve": fitted,
            "curvature": curv_pts,
            "scenarios": scenarios,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}