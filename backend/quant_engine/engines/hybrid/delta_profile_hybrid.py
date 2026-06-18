"""Motor 21 — Delta Profile Híbrido (spot delta + options flow by strike)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

# [PD-8] Calibration constants — tuned for 5m FMP bars (~120 bars ≈ 10h session)
PRICE_BINS: int = 50
LOOKBACK_CANDLES: int = 120
STRONG_DELTA_PCT: float = 0.60
PROXIMITY_PCT: float = 0.005


@dataclass(frozen=True)
class DeltaProfileResult:
    """Spot delta profile summary."""

    vap_delta_pos: float
    vap_delta_neg: float
    delta_by_level: pd.DataFrame


def _bars_to_frame(candles: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    for row in candles[-LOOKBACK_CANDLES:]:
        o = float(row.get("open") or row.get("o") or 0.0)
        h = float(row.get("high") or row.get("h") or 0.0)
        low = float(row.get("low") or row.get("l") or 0.0)
        c = float(row.get("close") or row.get("c") or 0.0)
        vol = float(row.get("volume") or row.get("v") or 0.0)
        if c <= 0 or vol <= 0:
            continue
        signed = vol if c >= o else -vol
        rows.append({"open": o, "high": h, "low": low, "close": c, "volume": vol, "delta": signed})
    return pd.DataFrame(rows)


def build_delta_profile(df: pd.DataFrame, bins: int = PRICE_BINS) -> DeltaProfileResult:
    """Build cumulative delta profile across price bins."""
    p_min = float(df["low"].min())
    p_max = float(df["high"].max())
    if p_max <= p_min:
        p_max = p_min + 1e-6
    edges = np.linspace(p_min, p_max, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    delta_arr = np.zeros(bins)

    for _, row in df.iterrows():
        mask = (centers >= row["low"]) & (centers <= row["high"])
        count = int(mask.sum())
        if count > 0:
            delta_arr[mask] += float(row["delta"]) / count

    profile = pd.DataFrame({"price_level": centers, "delta_cumulative": delta_arr})
    pos_idx = int(np.argmax(delta_arr))
    neg_idx = int(np.argmin(delta_arr))
    return DeltaProfileResult(
        vap_delta_pos=float(centers[pos_idx]),
        vap_delta_neg=float(centers[neg_idx]),
        delta_by_level=profile,
    )


def compute_options_delta_by_strike(chain_rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Aggregate net options flow proxy per strike from chain snapshot rows."""
    if not chain_rows:
        return pd.DataFrame(columns=["strike", "net_flow"])

    records: list[dict[str, float]] = []
    for row in chain_rows:
        strike = row.get("strike")
        if strike is None:
            continue
        call_oi = float(row.get("call_oi") or row.get("open_interest_call") or 0.0)
        put_oi = float(row.get("put_oi") or row.get("open_interest_put") or 0.0)
        call_delta = float(row.get("call_delta") or row.get("delta_call") or 0.5)
        put_delta = float(row.get("put_delta") or row.get("delta_put") or -0.5)
        net_flow = call_oi * call_delta + put_oi * put_delta
        records.append({"strike": float(strike), "net_flow": net_flow})

    if not records:
        return pd.DataFrame(columns=["strike", "net_flow"])

    out = pd.DataFrame(records).groupby("strike", as_index=False)["net_flow"].sum()
    return out.sort_values("strike").reset_index(drop=True)


def find_aligned_levels(
    delta_profile: DeltaProfileResult,
    opt_delta_df: pd.DataFrame,
    current_price: float,
    tolerance_pct: float = PROXIMITY_PCT,
) -> list[dict[str, Any]]:
    """Find price levels where spot delta and options flow align."""
    aligned: list[dict[str, Any]] = []
    profile_df = delta_profile.delta_by_level
    max_abs = float(profile_df["delta_cumulative"].abs().max())
    if max_abs <= 0:
        return aligned

    for _, opt_row in opt_delta_df.iterrows():
        strike = float(opt_row["strike"])
        net_opt = float(opt_row["net_flow"])
        if strike <= 0:
            continue
        diffs = (profile_df["price_level"] - strike).abs()
        near_idx = int(diffs.idxmin())
        if float(diffs.iloc[near_idx]) / strike > tolerance_pct:
            continue

        spot_delta = float(profile_df.loc[near_idx, "delta_cumulative"])
        rel_strength = abs(spot_delta) / max_abs

        if spot_delta > 0 and net_opt > 0:
            kind = "ACCUMULATION"
        elif spot_delta < 0 and net_opt < 0:
            kind = "DISTRIBUTION"
        elif spot_delta > 0 and net_opt < 0:
            kind = "DISGUISED_DISTRIBUTION"
        elif spot_delta < 0 and net_opt > 0:
            kind = "SILENT_ACCUMULATION"
        else:
            continue

        aligned.append(
            {
                "price": float(profile_df.loc[near_idx, "price_level"]),
                "spot_delta": spot_delta,
                "options_net_flow": net_opt,
                "rel_strength": rel_strength,
                "type": kind,
                "near_current": abs(strike - current_price) / current_price <= PROXIMITY_PCT * 2,
            }
        )

    return sorted(aligned, key=lambda x: x["rel_strength"], reverse=True)


def run_delta_profile_hybrid(
    *,
    symbol: str,
    candles: list[dict[str, Any]],
    chain_rows: list[dict[str, Any]] | None = None,
    spot: float | None = None,
) -> dict[str, Any]:
    """Run motor 21 and return a decision-engine block."""
    df = _bars_to_frame(candles)
    if df.empty:
        return {"ok": False, "reason": "insufficient_candles", "signal": "NEUTRAL"}

    current_price = spot if spot is not None else float(df["close"].iloc[-1])
    profile = build_delta_profile(df)
    opt_df = compute_options_delta_by_strike(chain_rows or [])
    aligned = find_aligned_levels(profile, opt_df, current_price)

    if not aligned:
        return {
            "ok": True,
            "signal": "NEUTRAL",
            "direction": "NEUTRAL",
            "direction_bias": "NEUTRAL",
            "score": 0.05,
            "strength": 0,
            "vap_delta_pos": profile.vap_delta_pos,
            "vap_delta_neg": profile.vap_delta_neg,
            "aligned_levels": 0,
        }

    top = aligned[0]
    score = min(0.95, top["rel_strength"] * 0.9 + (0.1 if top["near_current"] else 0.0))
    direction = "NEUTRAL"
    signal = "NEUTRAL"
    strength = 1

    if top["type"] == "ACCUMULATION":
        direction = "LONG"
        signal = "ACCUMULATION_ALIGNED"
        strength = 3 if score > STRONG_DELTA_PCT else 2
    elif top["type"] == "DISTRIBUTION":
        direction = "SHORT"
        signal = "DISTRIBUTION_ALIGNED"
        strength = 3 if score > STRONG_DELTA_PCT else 2
    elif top["type"] in ("DISGUISED_DISTRIBUTION", "SILENT_ACCUMULATION"):
        signal = top["type"]
        score *= 0.7
        strength = 1

    return {
        "ok": True,
        "signal": signal,
        "direction": direction,
        "direction_bias": direction,
        "score": round(score, 4),
        "strength": strength,
        "top_level": top,
        "aligned_levels": len(aligned),
        "vap_delta_pos": round(profile.vap_delta_pos, 4),
        "vap_delta_neg": round(profile.vap_delta_neg, 4),
    }
