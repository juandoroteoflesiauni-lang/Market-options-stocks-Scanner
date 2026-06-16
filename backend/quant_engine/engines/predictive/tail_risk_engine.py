"""
Tail risk from volatility smile geometry (cubic spline on OTM-composite IV).
Stateless percentile vs baked-in reference convexities (no DB session state).

Extended with Q-measure moment extraction via risk_neutral_density_engine
(soft dependency — engine works without it if import fails).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]
from scipy.interpolate import CubicSpline  # type: ignore[import-not-found, import-untyped]
from scipy.stats import percentileofscore  # type: ignore[import-not-found, import-untyped]

warnings.filterwarnings("ignore", category=RuntimeWarning)

# Soft import — engine remains functional if RND engine is absent
try:
    from backend.quant_engine.engines.predictive.risk_neutral_density_engine import (
        get_risk_neutral_density as _get_rnd,
    )

    _RND_AVAILABLE = True
except Exception:
    _RND_AVAILABLE = False

# Typical 25Δ butterfly (decimal) distribution for equity index–style surfaces (prior for percentiles).
_REF_CONVEXITIES = np.array(
    [
        0.002,
        0.003,
        0.004,
        0.005,
        0.006,
        0.007,
        0.008,
        0.009,
        0.010,
        0.012,
        0.014,
        0.016,
        0.018,
        0.020,
        0.022,
        0.025,
        0.028,
        0.032,
        0.038,
        0.045,
    ],
    dtype=float,
)


@dataclass
class SmileMetrics:
    skew_25d: float
    convexity_25d: float
    iv_put_25d: float
    iv_call_25d: float
    iv_atm: float
    min_iv_strike: float
    smile_skewness_pct: float
    as_of_iso: str


@dataclass
class TailRiskAlert:
    level: str
    convexity_percentile: float
    skew_regime: str
    message: str
    metrics: SmileMetrics


class TailRiskEngine:
    SKEW_NEUTRAL_BAND = (-0.02, 0.02)
    SKEW_BEARISH_WARN = 0.05
    SKEW_CRASH_HEDGE = 0.10
    CATASTROPHE_PERCENTILE = 90.0
    ELEVATED_PERCENTILE = 70.0

    def __init__(self, spline_smoothing: bool = True):
        self.spline_smoothing = spline_smoothing
        self._spline_cs: CubicSpline | None = None
        self._spline_strikes: np.ndarray[Any, np.dtype[Any]] | None = None

    def _build_smile_spline(
        self, df_iv: pd.DataFrame
    ) -> tuple[CubicSpline, np.ndarray[Any, np.dtype[Any]], np.ndarray[Any, np.dtype[Any]]]:
        df_clean = (
            df_iv[["strike", "iv"]].dropna().sort_values("strike").drop_duplicates(subset="strike")
        )
        if len(df_clean) < 4:
            raise ValueError(
                f"Se necesitan al menos 4 strikes para el spline (recibidos: {len(df_clean)})."
            )
        strikes = df_clean["strike"].values.astype(float)
        ivs = df_clean["iv"].values.astype(float)
        cs = CubicSpline(strikes, ivs, bc_type="not-a-knot")
        return cs, strikes, ivs

    def _find_smile_minimum(
        self, cs: CubicSpline, k_min: float, k_max: float, n_points: int = 500
    ) -> float:
        k_grid = np.linspace(k_min, k_max, n_points)
        iv_grid = cs(k_grid)
        return float(k_grid[np.argmin(iv_grid)])

    @staticmethod
    def _validate_dataframe(df: pd.DataFrame) -> None:
        required = {"strike", "option_type", "delta", "iv", "spot_price"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Columnas faltantes: {missing}")
        if df["iv"].isna().all():
            raise ValueError("Todas las IV son NaN.")

    def _extract_iv_by_delta(
        self, df: pd.DataFrame, option_type: str, target_delta: float
    ) -> float:
        mask = df["option_type"].str.upper() == option_type.upper()
        subset = df[mask].copy()
        if subset.empty:
            return float("nan")
        subset["delta_abs"] = subset["delta"].abs()
        idx = (subset["delta_abs"] - target_delta).abs().idxmin()
        return float(subset.loc[idx, "iv"])

    def _extract_atm_iv(self, df: pd.DataFrame, spot: float) -> float:
        d = df.copy()
        d["dist_spot"] = (d["strike"] - spot).abs()
        nearest_idx = d.groupby("option_type")["dist_spot"].idxmin()
        ivs = d.loc[nearest_idx.values, "iv"].values.astype(float)
        return float(np.nanmean(ivs))

    def compute_metrics(
        self,
        df: pd.DataFrame,
        smile_iv_df: pd.DataFrame | None = None,
        as_of_iso: str | None = None,
    ) -> SmileMetrics:
        self._validate_dataframe(df)
        spot = float(df["spot_price"].iloc[0])
        ts = as_of_iso or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        iv_put_25d = self._extract_iv_by_delta(df, "PUT", 0.25)
        iv_call_25d = self._extract_iv_by_delta(df, "CALL", 0.25)
        iv_atm = self._extract_atm_iv(df, spot)

        skew_25d = iv_put_25d - iv_call_25d
        convexity_25d = (iv_put_25d + iv_call_25d) / 2.0 - iv_atm

        src = (
            smile_iv_df
            if smile_iv_df is not None
            else df[["strike", "iv"]].drop_duplicates(subset="strike")
        )
        cs, strikes, _ = self._build_smile_spline(src)
        self._spline_cs = cs
        self._spline_strikes = strikes
        min_iv_strike = self._find_smile_minimum(cs, float(strikes.min()), float(strikes.max()))
        smile_skewness_pct = (min_iv_strike - spot) / spot if spot > 0 else 0.0

        return SmileMetrics(
            skew_25d=skew_25d,
            convexity_25d=convexity_25d,
            iv_put_25d=iv_put_25d,
            iv_call_25d=iv_call_25d,
            iv_atm=iv_atm,
            min_iv_strike=min_iv_strike,
            smile_skewness_pct=smile_skewness_pct,
            as_of_iso=ts,
        )

    def assess_tail_risk(self, metrics: SmileMetrics) -> TailRiskAlert:
        conv_pct = float(percentileofscore(_REF_CONVEXITIES, metrics.convexity_25d, kind="weak"))

        s = metrics.skew_25d
        if s >= self.SKEW_CRASH_HEDGE:
            skew_regime = "CRASH_HEDGE"
        elif s >= self.SKEW_BEARISH_WARN:
            skew_regime = "BEARISH_REVERSAL"
        elif s <= self.SKEW_NEUTRAL_BAND[0]:
            skew_regime = "BULLISH_REVERSAL"
        else:
            skew_regime = "NEUTRAL"

        if conv_pct >= self.CATASTROPHE_PERCENTILE:
            level = "CATASTROPHE_IMMINENT"
            message = (
                f"ALERTA MÁXIMA: convexidad en percentil ~{conv_pct:.0f} vs referencia histórica típica. "
                f"Skew 25Δ: {metrics.skew_25d*100:.2f} vol pts. Régimen: {skew_regime}. "
                "Considerar reducción de riesgo direccional y vega defensiva."
            )
        elif conv_pct >= self.ELEVATED_PERCENTILE or skew_regime == "CRASH_HEDGE":
            level = "ELEVATED"
            message = (
                f"Riesgo elevado: convexidad ~percentil {conv_pct:.0f}. "
                f"Skew 25Δ: {metrics.skew_25d*100:.2f} vol pts ({skew_regime}). Monitorear flujos y gamma."
            )
        else:
            level = "NORMAL"
            message = (
                f"Régimen estable vs referencia: convexidad ~percentil {conv_pct:.0f}. "
                f"Skew 25Δ: {metrics.skew_25d*100:.2f} vol pts ({skew_regime})."
            )

        return TailRiskAlert(
            level=level,
            convexity_percentile=conv_pct,
            skew_regime=skew_regime,
            message=message,
            metrics=metrics,
        )

    def analyze_risk_reversal(self, metrics: SmileMetrics) -> dict[str, Any]:
        skew = metrics.skew_25d
        skew_pct = skew * 100.0
        abs_skew = abs(skew_pct)
        if abs_skew < 2:
            signal_strength = "DEBIL"
        elif abs_skew < 5:
            signal_strength = "MODERADA"
        elif abs_skew < 10:
            signal_strength = "FUERTE"
        else:
            signal_strength = "EXTREMA"
        direction = "BAJISTA" if skew > 0 else "ALCISTA"
        interpretation = {
            "BAJISTA": (
                f"Puts 25Δ ~{abs_skew:.1f} vol pts por encima de calls 25Δ. "
                "Demanda de protección bajista / hedging institucional."
            ),
            "ALCISTA": (
                f"Calls 25Δ ~{abs_skew:.1f} vol pts por encima de puts 25Δ. "
                "Demanda alcista o cobertura de shorts."
            ),
        }
        return {
            "direction": direction,
            "signal_strength": signal_strength,
            "skew_vol_pts": round(skew_pct, 4),
            "iv_put_25d_pct": round(metrics.iv_put_25d * 100, 2),
            "iv_call_25d_pct": round(metrics.iv_call_25d * 100, 2),
            "iv_atm_pct": round(metrics.iv_atm * 100, 2),
            "convexity_vol_pts": round(metrics.convexity_25d * 100, 4),
            "min_iv_strike": round(metrics.min_iv_strike, 4),
            "smile_skewness_pct": round(metrics.smile_skewness_pct * 100, 2),
            "interpretation": interpretation[direction],
        }


def _extract_implied_moments(
    options_chain: pd.DataFrame,
    spot: float,
    rate: float,
    tte: float,
) -> dict[str, Any] | None:
    """Call RND engine and return relevant moments.

    Returns None (gracefully) if RND engine unavailable or fails.
    options_chain must have columns [strike, call_price].
    """
    if not _RND_AVAILABLE:
        return None
    try:
        # RND engine needs one call_price per unique strike (strictly increasing).
        # options_chain may have duplicate strikes (one CALL row + one PUT row).
        # Deduplicate: keep first call_price per strike.
        rnd_input = (
            options_chain[["strike", "call_price"]]
            .dropna()
            .sort_values("strike")
            .drop_duplicates(subset="strike")
        )
        result = _get_rnd(rnd_input, spot, rate, tte)
        if "error_msg" in result:
            return None
        return {
            "q_skewness": result.get("q_skewness"),
            "q_kurtosis": result.get("q_kurtosis"),
            "modal_price": result.get("modal_price"),
            "is_bimodal": result.get("is_bimodal", False),
        }
    except Exception:
        return None


def get_tail_risk(
    options_chain: pd.DataFrame,
    spot: float,
    rate: float,
    tte: float,
) -> dict[str, Any]:
    """Stateless tail-risk analysis with Q-moment enrichment.

    Parameters
    ----------
    options_chain : DataFrame with required columns:
                    strike, iv, option_type, delta, spot_price
                    Optional but recommended for Q-moments:
                    call_price (enables RND engine call)
                    Optional for tail_asymmetry:
                    call_price, put_price (OTM premium ratio)
    spot          : Current spot price
    rate          : Risk-free rate (annualised)
    tte           : Time to expiry in years

    Returns
    -------
    dict with keys:
        skew_25d, convexity_25d, iv_put_25d, iv_call_25d, iv_atm,
        skew_regime, convexity_percentile, alert_level,
        q_skewness, q_kurtosis, implied_skew_signal, tail_asymmetry,
        bimodal_alert, directional_signal
    On error: dict with error_msg key.
    """
    required = {"strike", "iv", "option_type", "delta", "spot_price"}
    missing = required - set(options_chain.columns)
    if missing:
        return {"error_msg": f"Missing columns: {missing}"}

    df = options_chain.copy()
    df["option_type"] = df["option_type"].str.upper()
    df["spot_price"] = float(spot)

    eng = TailRiskEngine()
    try:
        metrics = eng.compute_metrics(df)
    except Exception as exc:
        return {"error_msg": str(exc)}

    alert = eng.assess_tail_risk(metrics)

    # ------------------------------------------------------------------ #
    # Implied skew signal from 25Δ skew (baseline, always available)      #
    # ------------------------------------------------------------------ #
    # skew_25d = iv_put − iv_call  (positive = bearish skew)
    # Normalise: ÷ 0.10 saturates at 10 vol pts; negate so bearish → negative signal
    baseline_skew_signal = float(np.clip(-metrics.skew_25d / 0.10, -1.0, 1.0))

    # ------------------------------------------------------------------ #
    # Q-moment extraction (soft, requires call_price column)               #
    # ------------------------------------------------------------------ #
    q_skewness: float | None = None
    q_kurtosis: float | None = None
    implied_skew_signal: float = baseline_skew_signal
    bimodal_alert: bool = False

    if "call_price" in df.columns:
        moments = _extract_implied_moments(df, spot, rate, tte)
        if moments is not None:
            q_skewness = moments["q_skewness"]
            q_kurtosis = moments["q_kurtosis"]
            bimodal_alert = bool(moments["is_bimodal"])
            if q_skewness is not None:
                # Req 5: clip(q_skewness / 2.0, -1, 1)
                rnd_signal = float(np.clip(q_skewness / 2.0, -1.0, 1.0))
                # Blend: 60 % RND signal + 40 % baseline 25Δ signal
                implied_skew_signal = float(
                    np.clip(0.6 * rnd_signal + 0.4 * baseline_skew_signal, -1.0, 1.0)
                )

    # ------------------------------------------------------------------ #
    # Tail asymmetry: OTM put premium / OTM call premium                  #
    # ------------------------------------------------------------------ #
    tail_asymmetry: float | None = None
    if {"call_price", "put_price"}.issubset(df.columns):
        try:
            # OTM calls: strike > spot; OTM puts: strike < spot
            otm_calls = df[(df["option_type"] == "CALL") & (df["strike"] > spot)][
                "call_price"
            ].astype(float)
            otm_puts = df[(df["option_type"] == "PUT") & (df["strike"] < spot)]["put_price"].astype(
                float
            )
            put_prem = float(otm_puts.sum())
            call_prem = float(otm_calls.sum())
            if call_prem > 0:
                tail_asymmetry = round(put_prem / call_prem, 4)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Directional signal: 60 % convexity/skew + 40 % implied_skew_signal  #
    # ------------------------------------------------------------------ #
    # Convexity component: high convexity + bearish skew = negative signal
    conv_norm = float(np.clip(-alert.convexity_percentile / 100.0, -1.0, 0.0))
    skew_norm = float(np.clip(-metrics.skew_25d / 0.10, -1.0, 1.0))
    legacy_component = float(np.clip(0.5 * conv_norm + 0.5 * skew_norm, -1.0, 1.0))
    directional_signal = float(
        np.clip(0.6 * legacy_component + 0.4 * implied_skew_signal, -1.0, 1.0)
    )

    return {
        "skew_25d": round(metrics.skew_25d, 6),
        "convexity_25d": round(metrics.convexity_25d, 6),
        "iv_put_25d": round(metrics.iv_put_25d, 6),
        "iv_call_25d": round(metrics.iv_call_25d, 6),
        "iv_atm": round(metrics.iv_atm, 6),
        "skew_regime": alert.skew_regime,
        "convexity_percentile": round(alert.convexity_percentile, 2),
        "alert_level": alert.level,
        "q_skewness": round(q_skewness, 6) if q_skewness is not None else None,
        "q_kurtosis": round(q_kurtosis, 6) if q_kurtosis is not None else None,
        "implied_skew_signal": round(implied_skew_signal, 4),
        "tail_asymmetry": tail_asymmetry,
        "bimodal_alert": bimodal_alert,
        "directional_signal": round(directional_signal, 4),
    }


def compute_tail_risk_payload(
    df_sd: pd.DataFrame,
    spot: float,
    dte_years: float,
    r: float = 0.04,
    as_of: str | None = None,
) -> dict[str, Any]:
    from .volatility_skew_engine import build_skew_frames_from_portfolio

    if df_sd is None or df_sd.empty or spot <= 0:
        return {"ok": False, "error": "empty_portfolio"}
    try:
        df_long, df_curve = build_skew_frames_from_portfolio(df_sd, spot, dte_years, r)
        if len(df_curve) < 4:
            return {"ok": False, "error": "insufficient_smile_points"}
        df_long = df_long.copy()
        df_long["spot_price"] = float(spot)
        df_long["option_type"] = df_long["option_type"].str.upper()

        eng = TailRiskEngine()
        metrics = eng.compute_metrics(df_long, smile_iv_df=df_curve, as_of_iso=as_of)
        alert = eng.assess_tail_risk(metrics)
        rr = eng.analyze_risk_reversal(metrics)

        assert eng._spline_cs is not None and eng._spline_strikes is not None
        cs = eng._spline_cs
        k0, k1 = float(eng._spline_strikes.min()), float(eng._spline_strikes.max())
        k_interp = np.linspace(k0, k1, 140)
        iv_line = cs(k_interp)
        curv = cs(k_interp, 2)

        observed = [
            {
                "strike": float(r.strike),
                "iv_pct": float(r.iv) * 100.0,
                "option_type": str(r.option_type),
                "delta": float(r.delta) if not np.isnan(r.delta) else 0.0,
            }
            for r in df_long.itertuples(index=False)
        ]
        smile_line = [
            {"strike": float(k), "iv_pct": float(iv) * 100.0}
            for k, iv in zip(k_interp, iv_line, strict=False)
        ]
        curv_pts = [
            {"strike": float(k), "curvature": float(c) * 100.0}
            for k, c in zip(k_interp, curv, strict=False)
        ]

        def _nan_clean(x: float) -> float:
            v = float(x)
            return 0.0 if np.isnan(v) else v

        return {
            "ok": True,
            "spot": float(spot),
            "metrics": {
                "skew_25d": _nan_clean(metrics.skew_25d),
                "convexity_25d": _nan_clean(metrics.convexity_25d),
                "iv_put_25d": _nan_clean(metrics.iv_put_25d),
                "iv_call_25d": _nan_clean(metrics.iv_call_25d),
                "iv_atm": _nan_clean(metrics.iv_atm),
                "min_iv_strike": float(metrics.min_iv_strike),
                "smile_skewness_pct": _nan_clean(metrics.smile_skewness_pct),
                "as_of": metrics.as_of_iso,
            },
            "alert": {
                "level": alert.level,
                "convexity_percentile": float(alert.convexity_percentile),
                "skew_regime": alert.skew_regime,
                "message": alert.message,
            },
            "risk_reversal": rr,
            "observed": observed,
            "smile_spline": smile_line,
            "curvature": curv_pts,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
