"""
Zero-day / near-expiry gamma wall, pinning probability, and squeeze-style alerts.
Adapted for API use: stateless runs, no matplotlib. Chain built from options snapshot rows.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from .gamma_flip_engine import bs_gamma

warnings.filterwarnings("ignore", category=RuntimeWarning)

_ET = ZoneInfo("America/New_York")


def _f(x: Any, default: float = 0.0) -> float:
    if x is None:
        return default
    try:
        v = float(x)
        return default if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return default


def _norm_pdf(x: float) -> float:
    """Standard normal PDF (scalar)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via erf (avoids untyped scipy.stats)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_theta(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Black–Scholes theta per year (same units as typical chain thetas); returns negative for long options."""
    if T <= 1e-12 or sigma <= 1e-12 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    term1 = -S * _norm_pdf(d1) * sigma / (2.0 * math.sqrt(T))
    if is_call:
        term2 = -r * K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        term2 = r * K * math.exp(-r * T) * _norm_cdf(-d2)
    return float(term1 + term2)


def estimate_minutes_to_close(
    expiry_str: str | None,
    as_of_iso: str | None,
    dte_years_fallback: float,
) -> float:
    """Minutes until 16:00 ET on expiry date; fallback to session-minute proxy from DTE years."""
    try:
        if expiry_str and as_of_iso:
            dpart = str(expiry_str)[:10]
            exp_d = datetime.strptime(dpart, "%Y-%m-%d").date()
            raw = as_of_iso.replace("Z", "+00:00")
            now = datetime.fromisoformat(raw)
            if now.tzinfo is None:
                now = now.replace(tzinfo=ZoneInfo("UTC"))
            now_et = now.astimezone(_ET)
            exp_close = datetime(exp_d.year, exp_d.month, exp_d.day, 16, 0, tzinfo=_ET).timestamp()
            mins = (exp_close - now_et.timestamp()) / 60.0
            if mins > 0:
                return float(min(max(mins, 5.0), 1_000_000.0))
    except Exception:
        pass
    return float(max(30.0, float(dte_years_fallback) * 252.0 * 390.0))


def chain_to_zero_day_dataframe(
    chain: Sequence[Any],
    spot: float,
    dte_years: float,
    r: float = 0.04,
) -> pd.DataFrame:
    """Build long-format chain for ZeroDayEngine from OptionStrikeRow-like objects."""
    rows: list[dict[str, Any]] = []
    T = max(float(dte_years), 1e-8)
    S = float(spot)
    for row in chain:
        strike = _f(getattr(row, "strike", 0))
        if strike <= 0:
            continue

        coi = _f(getattr(row, "call_oi", 0))
        civ = getattr(row, "call_iv", None)
        if coi > 0 and civ is not None and float(civ) > 1e-8:
            sig = float(civ)
            cg = _f(getattr(row, "call_gamma", 0))
            if cg == 0:
                cg = bs_gamma(S, strike, T, r, sig)
            cb = getattr(row, "call_bid", None)
            ca = getattr(row, "call_ask", None)
            mid = _f(getattr(row, "call_last", None))
            if mid == 0 and cb is not None and ca is not None:
                mid = (_f(cb) + _f(ca)) / 2.0
            th = getattr(row, "call_theta", None)
            if th is None:
                th = bs_theta(S, strike, T, r, sig, True)
            else:
                th = float(th)
            rows.append(
                {
                    "strike": strike,
                    "option_type": "C",
                    "bid": _f(cb),
                    "ask": _f(ca),
                    "last_price": mid,
                    "volume": _f(getattr(row, "call_volume", 0)),
                    "open_interest": int(coi),
                    "delta": _f(getattr(row, "call_delta", 0)),
                    "gamma": float(cg),
                    "theta": float(th),
                    "IV": sig,
                }
            )

        poi = _f(getattr(row, "put_oi", 0))
        piv = getattr(row, "put_iv", None)
        if poi > 0 and piv is not None and float(piv) > 1e-8:
            sig = float(piv)
            pg = _f(getattr(row, "put_gamma", 0))
            if pg == 0:
                pg = bs_gamma(S, strike, T, r, sig)
            pb = getattr(row, "put_bid", None)
            pa = getattr(row, "put_ask", None)
            mid = _f(getattr(row, "put_last", None))
            if mid == 0 and pb is not None and pa is not None:
                mid = (_f(pb) + _f(pa)) / 2.0
            th = getattr(row, "put_theta", None)
            if th is None:
                th = bs_theta(S, strike, T, r, sig, False)
            else:
                th = float(th)
            rows.append(
                {
                    "strike": strike,
                    "option_type": "P",
                    "bid": _f(pb),
                    "ask": _f(pa),
                    "last_price": mid,
                    "volume": _f(getattr(row, "put_volume", 0)),
                    "open_interest": int(poi),
                    "delta": _f(getattr(row, "put_delta", 0)),
                    "gamma": float(pg),
                    "theta": float(th),
                    "IV": sig,
                }
            )

    return pd.DataFrame(rows)


@dataclass
class SqueezeAlert:
    alert_type: str
    severity: str
    strike: float
    message: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GravityLevel:
    strike: float
    gex: float
    level_type: str
    strength: float
    oi_concentration: float
    pinning_prob: float


@dataclass
class EngineSnapshot:
    timestamp: pd.Timestamp
    spot: float
    gamma_flip: float
    total_gex: float
    put_wall: float
    call_wall: float
    vanna_pressure: float
    charm_decay: float
    imbalance_ratio: float
    pinning_strike: float
    pinning_prob: float
    alerts: list[SqueezeAlert]
    gravity_map: list[GravityLevel]


class ZeroDayEngine:
    SKEW_NEUTRAL_BAND = (-0.02, 0.02)

    def __init__(
        self,
        spot_price: float,
        spot_multiplier: int = 100,
        rvol_threshold: float = 3.0,
        gamma_rent_threshold: float = 50.0,
        pinning_window_pct: float = 0.02,
        minutes_to_close: float = 390.0,
        stateless: bool = True,
    ):
        self.spot = float(spot_price)
        self.multiplier = int(spot_multiplier)
        self.rvol_threshold = float(rvol_threshold)
        self.gamma_rent_threshold = float(gamma_rent_threshold)
        self.pinning_window_pct = float(pinning_window_pct)
        self.minutes_to_close = float(minutes_to_close)
        self.stateless = stateless
        self._history: list[EngineSnapshot] = []
        self._initial_oi: pd.Series | None = None

    def run(self, df: pd.DataFrame, timestamp: pd.Timestamp | None = None) -> EngineSnapshot:
        ts = timestamp or pd.Timestamp.now(tz="UTC")
        df = self._prepare_chain(df)
        if self._initial_oi is None:
            self._initial_oi = df.set_index(["strike", "option_type"])["open_interest"].copy()

        gex_series = self._compute_gex(df)
        rvol_series = self._compute_rvol(df)
        gamma_rent = self._compute_gamma_rent(df)
        vanna_p = self._compute_vanna_pressure(df)
        charm_p = self._compute_charm_cascade(df)
        pinning = self._compute_pinning_probability(df, gex_series)
        gravity = self._build_gravity_map(df, gex_series, pinning)
        imbalance = self._compute_imbalance(df)
        gamma_flip = self._find_gamma_flip(gex_series)
        walls = self._find_walls(gex_series)
        alerts = self._generate_alerts(
            df, rvol_series, gamma_rent, vanna_p, charm_p, imbalance, pinning, gex_series
        )

        snap = EngineSnapshot(
            timestamp=ts,
            spot=self.spot,
            gamma_flip=gamma_flip,
            total_gex=float(gex_series["gex"].sum()),
            put_wall=walls["put_wall"],
            call_wall=walls["call_wall"],
            vanna_pressure=vanna_p,
            charm_decay=charm_p,
            imbalance_ratio=imbalance,
            pinning_strike=float(pinning.idxmax()) if len(pinning) > 0 else self.spot,
            pinning_prob=float(pinning.max()) if len(pinning) > 0 else 0.0,
            alerts=alerts,
            gravity_map=gravity,
        )
        if not self.stateless:
            self._history.append(snap)
        return snap

    def _prepare_chain(self, df: pd.DataFrame) -> pd.DataFrame:
        required = [
            "strike",
            "option_type",
            "bid",
            "ask",
            "volume",
            "open_interest",
            "delta",
            "gamma",
            "theta",
            "IV",
        ]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Columnas faltantes: {missing}")
        df = df.copy()
        df["option_type"] = df["option_type"].astype(str).str.upper().str[0]
        df["mid"] = (df["bid"] + df["ask"]) / 2.0
        df["moneyness"] = df["strike"] / self.spot
        df["otm_flag"] = np.where(
            df["option_type"] == "C",
            df["strike"] > self.spot,
            df["strike"] < self.spot,
        )
        if "vanna" not in df.columns:
            df["vanna"] = df.get("vega", df["gamma"] * self.spot * 0.01) / (
                self.spot * df["IV"].clip(0.01)
            )
        if "charm" not in df.columns:
            t_years = max(self.minutes_to_close / (252 * 390), 1e-6)
            df["charm"] = -df["gamma"] * df["IV"] / (2 * np.sqrt(t_years) * self.spot)
        return df.reset_index(drop=True)

    def _compute_gex(self, df: pd.DataFrame) -> pd.DataFrame:
        spot_sq = self.spot**2
        gex_raw = df["gamma"] * df["open_interest"] * self.multiplier * spot_sq / 1e9
        sign = np.where(df["option_type"] == "C", 1.0, -1.0)
        df = df.copy()
        df["gex_raw"] = gex_raw * sign
        return (
            df.groupby("strike")["gex_raw"].sum().reset_index().rename(columns={"gex_raw": "gex"})
        )

    def _compute_rvol(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        oi_initial = self._initial_oi
        if oi_initial is not None:
            idx = df.set_index(["strike", "option_type"]).index
            oi_reindexed = oi_initial.reindex(idx)
            oi_ref = np.where(
                oi_reindexed.isna().values,
                df["open_interest"].values,
                oi_reindexed.values,
            )
            oi_ref = np.maximum(oi_ref, 1)
        else:
            oi_ref = np.maximum(df["open_interest"].values, 1)
        df["rvol"] = df["volume"].values / oi_ref
        df["rvol_flag"] = df["rvol"] > self.rvol_threshold
        return df[["strike", "option_type", "volume", "open_interest", "rvol", "rvol_flag"]].copy()

    def _compute_gamma_rent(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        spot_sq = self.spot**2
        gex_unit = df["gamma"] * self.multiplier * spot_sq / 1e6
        theta_abs = np.abs(df["theta"]).clip(0.001)
        df["gamma_rent"] = gex_unit / theta_abs
        df["ignition_flag"] = df["gamma_rent"] > self.gamma_rent_threshold
        return df[["strike", "option_type", "gamma_rent", "ignition_flag"]].copy()

    def _compute_vanna_pressure(self, df: pd.DataFrame) -> float:
        sign = np.where(df["option_type"] == "C", 1.0, -1.0)
        vanna_usd = (
            df["vanna"].values
            * sign
            * df["open_interest"].values
            * self.multiplier
            * self.spot
            / 1e9
        )
        return float(np.sum(vanna_usd))

    def _compute_charm_cascade(self, df: pd.DataFrame) -> float:
        sign = np.where(df["option_type"] == "C", 1.0, -1.0)
        charm_flow = (
            df["charm"].values
            * sign
            * df["open_interest"].values
            * self.multiplier
            * self.spot
            / 1e6
        )
        return float(np.sum(charm_flow))

    def _compute_pinning_probability(self, df: pd.DataFrame, gex_series: pd.DataFrame) -> pd.Series:
        oi_by_strike = df.groupby("strike")["open_interest"].sum()
        oi_norm = oi_by_strike / oi_by_strike.sum().clip(1)
        gex_indexed = gex_series.set_index("strike")["gex"]
        gex_neg = gex_indexed.clip(upper=0).abs()
        gex_neg_norm = gex_neg / gex_neg.sum().clip(1)
        strikes = oi_by_strike.index.values
        proximity = np.exp(-0.5 * ((strikes - self.spot) / (self.spot * 0.01)) ** 2)
        prox_series = pd.Series(proximity, index=oi_by_strike.index)
        prox_norm = prox_series / prox_series.sum().clip(1)
        time_factor = np.exp(-self.minutes_to_close / 60.0)
        all_strikes = oi_norm.index.union(gex_neg_norm.index)
        oi_a = oi_norm.reindex(all_strikes).fillna(0)
        gex_a = gex_neg_norm.reindex(all_strikes).fillna(0)
        prox_a = prox_norm.reindex(all_strikes).fillna(0)
        raw_score = (0.35 * gex_a + 0.35 * oi_a + 0.30 * prox_a) * time_factor
        prob = 1.0 / (1.0 + np.exp(-10.0 * (raw_score - raw_score.mean())))
        return prob

    def build_gravity_map(self, df: pd.DataFrame) -> tuple[list[GravityLevel], list[GravityLevel]]:
        gex_series = self._compute_gex(df)
        pinning_map = self._compute_pinning_probability(df, gex_series)
        oi_by_str = df.groupby("strike")["open_interest"].sum()
        max_gex = gex_series["gex"].abs().max()
        max_oi = oi_by_str.max()
        gex_idx = gex_series.set_index("strike")["gex"]
        attractions: list[GravityLevel] = []
        repulsions: list[GravityLevel] = []
        for strike in gex_series["strike"].values:
            gex_val = float(gex_idx.get(strike, 0))
            oi_val = float(oi_by_str.get(strike, 0))
            pin_prob = float(pinning_map.get(strike, 0))
            strength = abs(gex_val) / max(max_gex, 1e-9)
            oi_conc = oi_val / max(max_oi, 1)
            level = GravityLevel(
                strike=float(strike),
                gex=gex_val,
                level_type="ATTRACTION" if gex_val < 0 else "REPULSION",
                strength=float(strength),
                oi_concentration=float(oi_conc),
                pinning_prob=pin_prob,
            )
            if gex_val < 0:
                attractions.append(level)
            else:
                repulsions.append(level)
        attractions.sort(key=lambda x: x.strength, reverse=True)
        repulsions.sort(key=lambda x: x.strength, reverse=True)
        return attractions, repulsions

    def _build_gravity_map(
        self, df: pd.DataFrame, gex_series: pd.DataFrame, pinning: pd.Series
    ) -> list[GravityLevel]:
        a, r = self.build_gravity_map(df)
        return a + r

    def _find_gamma_flip(self, gex_series: pd.DataFrame) -> float:
        gex_sorted = gex_series.sort_values("strike")
        strikes = gex_sorted["strike"].values
        gex_cum = np.cumsum(gex_sorted["gex"].values)
        sign_changes = np.where(np.diff(np.sign(gex_cum)))[0]
        if len(sign_changes) == 0:
            return float(strikes[np.argmin(np.abs(gex_cum))])
        candidate_strikes = strikes[sign_changes]
        closest_idx = int(np.argmin(np.abs(candidate_strikes - self.spot)))
        flip_strike = float(candidate_strikes[closest_idx])
        idx = int(sign_changes[closest_idx])
        if idx + 1 < len(strikes):
            g0, g1 = gex_cum[idx], gex_cum[idx + 1]
            s0, s1 = strikes[idx], strikes[idx + 1]
            if g1 - g0 != 0:
                flip_strike = float(s0 + (0 - g0) * (s1 - s0) / (g1 - g0))
        return flip_strike

    def _find_walls(self, gex_series: pd.DataFrame) -> dict[str, float]:
        gex = gex_series.set_index("strike")["gex"]
        call_strikes = gex[gex > 0]
        put_strikes = gex[gex < 0]
        call_wall = float(call_strikes.idxmax()) if len(call_strikes) > 0 else self.spot
        put_wall = float(put_strikes.idxmin()) if len(put_strikes) > 0 else self.spot
        return {"call_wall": call_wall, "put_wall": put_wall}

    def _compute_imbalance(self, df: pd.DataFrame) -> float:
        otm_calls = df[(df["option_type"] == "C") & df["otm_flag"]]
        otm_puts = df[(df["option_type"] == "P") & df["otm_flag"]]
        call_vol = float(otm_calls["volume"].sum())
        put_vol = float(otm_puts["volume"].sum())
        if put_vol < 1:
            return float("inf") if call_vol > 0 else 1.0
        return call_vol / put_vol

    def _generate_alerts(
        self,
        df: pd.DataFrame,
        rvol_series: pd.DataFrame,
        gamma_rent: pd.DataFrame,
        vanna_p: float,
        charm_p: float,
        imbalance: float,
        pinning: pd.Series,
        gex_series: pd.DataFrame,
    ) -> list[SqueezeAlert]:
        alerts: list[SqueezeAlert] = []
        flagged_rvol = rvol_series[rvol_series["rvol_flag"]]
        for _, row in flagged_rvol.iterrows():
            severity = "CRITICAL" if row["rvol"] > 6 else "HIGH" if row["rvol"] > 4 else "MEDIUM"
            alerts.append(
                SqueezeAlert(
                    alert_type="GAMMA_SQUEEZE",
                    severity=severity,
                    strike=float(row["strike"]),
                    message=(
                        f"RVOL elevado en {row['option_type']} strike {row['strike']:.0f}: "
                        f"{row['rvol']:.1f}x OI de referencia."
                    ),
                    confidence=min(0.95, float(row["rvol"]) / 10.0),
                    metadata={"rvol": float(row["rvol"]), "option_type": str(row["option_type"])},
                )
            )
        flagged_gr = gamma_rent[gamma_rent["ignition_flag"]]
        for _, row in flagged_gr.iterrows():
            alerts.append(
                SqueezeAlert(
                    alert_type="GAMMA_SQUEEZE",
                    severity="HIGH",
                    strike=float(row["strike"]),
                    message=(
                        f"Gamma rent alto en {row['option_type']} strike {row['strike']:.0f}: "
                        f"ratio {row['gamma_rent']:.1f}."
                    ),
                    confidence=min(0.85, float(row["gamma_rent"]) / 200.0),
                    metadata={"gamma_rent": float(row["gamma_rent"])},
                )
            )
        vanna_threshold = 0.5
        if abs(vanna_p) > vanna_threshold:
            direction = "ALCISTA" if vanna_p > 0 else "BAJISTA"
            alerts.append(
                SqueezeAlert(
                    alert_type="VANNA_FLUSH",
                    severity="HIGH",
                    strike=self.spot,
                    message=(
                        f"Vanna pressure {direction}: {vanna_p:.2f} B USD por 1% IV. "
                        "Movimientos de IV pueden forzar rebalanceo de delta."
                    ),
                    confidence=min(0.90, abs(vanna_p) / 2.0),
                    metadata={"vanna_pressure_bn": float(vanna_p)},
                )
            )
        charm_threshold = 100.0
        if abs(charm_p) > charm_threshold and self.minutes_to_close < 120:
            direction = "VENDEDOR" if charm_p < 0 else "COMPRADOR"
            alerts.append(
                SqueezeAlert(
                    alert_type="CHARM_CASCADE",
                    severity="CRITICAL" if self.minutes_to_close < 60 else "HIGH",
                    strike=self.spot,
                    message=(
                        f"Charm cascade {direction}: {charm_p:.1f} M USD/min; "
                        f"{self.minutes_to_close:.0f} min aprox. al cierre de la expiración."
                    ),
                    confidence=min(0.92, abs(charm_p) / 500.0),
                    metadata={
                        "charm_flow_mm": float(charm_p),
                        "minutes_to_close": self.minutes_to_close,
                    },
                )
            )
        if imbalance > 2.0 and self.minutes_to_close < 90:
            alerts.append(
                SqueezeAlert(
                    alert_type="GAMMA_SQUEEZE",
                    severity="HIGH",
                    strike=self.spot,
                    message=f"Desequilibrio volumen OTM calls/puts: ratio {imbalance:.1f}:1.",
                    confidence=min(0.80, imbalance / 5.0),
                    metadata={"imbalance_ratio": float(imbalance)},
                )
            )
        elif imbalance < 0.5 and imbalance > 0 and self.minutes_to_close < 90:
            inv = 1.0 / imbalance
            alerts.append(
                SqueezeAlert(
                    alert_type="GAMMA_SQUEEZE",
                    severity="HIGH",
                    strike=self.spot,
                    message=f"Desequilibrio volumen OTM puts/calls: ratio {inv:.1f}:1.",
                    confidence=min(0.80, inv / 5.0),
                    metadata={"imbalance_ratio": float(imbalance)},
                )
            )
        if len(pinning) > 0:
            pin_strike = pinning.idxmax()
            pin_prob = float(pinning.max())
            if pin_prob > 0.60 and self.minutes_to_close < 90:
                alerts.append(
                    SqueezeAlert(
                        alert_type="PINNING",
                        severity="MEDIUM",
                        strike=float(pin_strike),
                        message=(
                            f"Pinning relativamente alto en strike {float(pin_strike):.0f} "
                            f"(prob modelo {pin_prob:.1%})."
                        ),
                        confidence=pin_prob,
                        metadata={"pinning_prob": pin_prob},
                    )
                )
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        alerts.sort(key=lambda a: severity_order.get(a.severity, 4))
        return alerts


def _safe_imbalance(x: float) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return float(x)


def compute_zero_day_payload(
    chain: Sequence[Any],
    spot: float,
    dte_years: float,
    r: float = 0.04,
    contract_multiplier: int = 100,
    expiry_hint: str | None = None,
    as_of_iso: str | None = None,
) -> dict[str, Any]:
    """JSON-serializable bundle for Predictive Options 2 (Plotly-friendly)."""
    if not chain or float(spot) <= 0:
        return {"ok": False, "error": "empty_chain"}
    try:
        df = chain_to_zero_day_dataframe(chain, float(spot), float(dte_years), r)
        if df.empty or len(df) < 4:
            return {"ok": False, "error": "insufficient_chain_rows"}
        mtc = estimate_minutes_to_close(expiry_hint, as_of_iso, float(dte_years))
        eng = ZeroDayEngine(
            spot_price=float(spot),
            spot_multiplier=contract_multiplier,
            minutes_to_close=mtc,
            stateless=True,
        )
        eng._initial_oi = df.set_index(["strike", "option_type"])["open_interest"].copy()
        snap = eng.run(df, timestamp=pd.Timestamp.now(tz="UTC"))
        dfp = eng._prepare_chain(df.copy())
        gex_series = eng._compute_gex(dfp)
        pinning = eng._compute_pinning_probability(dfp, gex_series)
        gamma_flip = float(snap.gamma_flip)
        walls = eng._find_walls(gex_series)

        strikes = gex_series["strike"].astype(float).tolist()
        gex_vals = gex_series["gex"].astype(float).tolist()
        pin_aligned = [float(pinning.get(s, 0.0)) for s in strikes]

        if snap.spot > gamma_flip:
            zone = {"x0": gamma_flip, "x1": float(snap.spot), "kind": "positive_stabilization"}
        else:
            zone = {"x0": float(snap.spot), "x1": gamma_flip, "kind": "negative_instability"}

        def _meta(m: dict[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for k, v in m.items():
                if isinstance(v, bool | str):
                    out[str(k)] = v
                elif isinstance(v, int | float | np.floating | np.integer):
                    fv = float(v)
                    out[str(k)] = fv if math.isfinite(fv) else None
                else:
                    out[str(k)] = v
            return out

        alerts_out = [
            {
                "alert_type": a.alert_type,
                "severity": a.severity,
                "strike": float(a.strike),
                "message": a.message,
                "confidence": float(a.confidence),
                "metadata": _meta(a.metadata),
            }
            for a in snap.alerts[:24]
        ]

        return {
            "ok": True,
            "spot": float(snap.spot),
            "minutes_to_close": float(mtc),
            "gamma_flip": gamma_flip,
            "call_wall": float(walls["call_wall"]),
            "put_wall": float(walls["put_wall"]),
            "total_gex_bn": float(snap.total_gex),
            "vanna_pressure_bn": float(snap.vanna_pressure),
            "charm_decay_mm": float(snap.charm_decay),
            "imbalance_ratio": _safe_imbalance(snap.imbalance_ratio),
            "pinning_strike": float(snap.pinning_strike),
            "pinning_prob": float(snap.pinning_prob),
            "zone": zone,
            "gex_bars": [
                {"strike": float(s), "gex_bn": float(g)}
                for s, g in zip(strikes, gex_vals, strict=False)
            ],
            "pin_curve": [
                {"strike": float(s), "pin_prob": float(p)}
                for s, p in zip(strikes, pin_aligned, strict=False)
            ],
            "alerts": alerts_out,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
