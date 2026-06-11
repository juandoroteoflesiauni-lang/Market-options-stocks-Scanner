"""options_order_flow_toxicity_engine.py
=========================================
Adapts the VPIN (Volume-Synchronized Probability of Informed Trading) model
of Easley et al. to the options market.

In equity options, raw volume is insufficient — what matters is the premium
paid (aggressive buyer hits ask) vs received (aggressive seller hits bid).
Lee-Ready classification on the premium determines trade initiation direction.

Four analytical components:
  1. Lee-Ready trade classification per contract type (call / put)
  2. Premium-weighted order imbalance per bucket
  3. VPIN — informed-trading toxicity across rolling volume buckets
  4. Unusual Activity Rating (UAR) scoring per trade

Public API
----------
get_options_flow_toxicity(trades, lookback_buckets=50) -> dict
"""

from __future__ import annotations

import warnings
from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]

from backend.config.logger_setup import get_logger

warnings.filterwarnings("ignore", category=RuntimeWarning)

logger = get_logger(__name__)

# Minimum number of trades required
_MIN_TRADES = 5
# Dollar multiplier: 1 contract = 100 shares
_CONTRACT_MULTIPLIER = 100
# Minimum bucket size (prevents division-by-zero on tiny datasets)
_MIN_BUCKET_SIZE = 1.0
# Top-N unusual trades to surface
_TOP_UAR = 5
# UAR thresholds
_UAR_OI_RATIO_THRESHOLD = 3.0  # volume > 3× OI
_UAR_OTM_THRESHOLD = 0.05  # OTM fraction > 5%
_ZERO_DTE_MULTIPLIER = 1.5
# Flow regime thresholds
_REGIME_STRONG = 0.20
_HEDGING_IMBALANCE_THRESHOLD = 0.30  # calls and puts both active (requires clear buying pressure)


def get_options_flow_toxicity(
    trades: pd.DataFrame,
    lookback_buckets: int = 50,
) -> dict[str, Any]:
    """Compute VPIN-adapted options flow toxicity and order-flow signals.

    Parameters
    ----------
    trades           : DataFrame with columns [timestamp, option_type, strike,
                       expiry, volume, premium, bid, ask, implied_vol, delta]
                       option_type must be 'C' or 'P' (case-insensitive).
    lookback_buckets : Number of volume buckets used to compute vpin_total.

    Returns
    -------
    dict with keys:
        call_flow_signal, put_flow_signal, net_options_flow, vpin_total,
        vpin_percentile, top_uar_trades, flow_regime
    On insufficient data: dict with error_msg key.
    """
    required = {"option_type", "volume", "premium", "bid", "ask"}
    missing = required - set(trades.columns)
    if missing:
        return {"error_msg": f"Missing columns: {missing}"}

    df = trades.copy()
    df["option_type"] = df["option_type"].astype(str).str.upper()
    df = df[df["option_type"].isin({"C", "P"})].copy()
    df = df.dropna(subset=["volume", "premium", "bid", "ask"])
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    df = df[df["volume"] > 0].reset_index(drop=True)

    if len(df) < _MIN_TRADES:
        return {"error_msg": f"Insufficient trades: {len(df)} < {_MIN_TRADES} required"}

    # ------------------------------------------------------------------ #
    # 1. LEE-READY CLASSIFICATION                                          #
    # ------------------------------------------------------------------ #
    mid = (df["bid"].astype(float) + df["ask"].astype(float)) / 2.0
    prem = df["premium"].astype(float)

    df["buyer_initiated"] = (prem > mid).astype(float)
    df["seller_initiated"] = (prem < mid).astype(float)
    # Trades at mid: split 50/50
    at_mid = prem == mid
    df.loc[at_mid, "buyer_initiated"] = 0.5
    df.loc[at_mid, "seller_initiated"] = 0.5

    # Dollar premium per trade (volume × option_price × 100)
    df["dollar_premium"] = df["volume"] * prem * _CONTRACT_MULTIPLIER

    df["buy_dollar_prem"] = df["dollar_premium"] * df["buyer_initiated"]
    df["sell_dollar_prem"] = df["dollar_premium"] * df["seller_initiated"]
    df["buy_vol"] = df["volume"] * df["buyer_initiated"]
    df["sell_vol"] = df["volume"] * df["seller_initiated"]

    calls = df[df["option_type"] == "C"]
    puts = df[df["option_type"] == "P"]

    # ------------------------------------------------------------------ #
    # 2. PREMIUM-WEIGHTED ORDER IMBALANCE                                  #
    # ------------------------------------------------------------------ #
    call_flow_signal = _imbalance(calls["buy_dollar_prem"], calls["sell_dollar_prem"])
    put_flow_signal_raw = _imbalance(puts["buy_dollar_prem"], puts["sell_dollar_prem"])
    # Put buying is bearish for the underlying → negate for directional convention
    put_flow_signal = -put_flow_signal_raw

    # ------------------------------------------------------------------ #
    # 3. VPIN — VOLUME-SYNCHRONISED TOXICITY                               #
    # ------------------------------------------------------------------ #
    total_volume = float(df["volume"].sum())
    n_buckets = max(1, lookback_buckets)
    bucket_size = max(total_volume / n_buckets, _MIN_BUCKET_SIZE)

    bucket_toxicities = _compute_vpin_buckets(df, bucket_size)

    if bucket_toxicities:
        recent = bucket_toxicities[-lookback_buckets:]
        vpin_total = float(np.mean(recent))
        # Percentile of current VPIN vs all observed buckets
        vpin_percentile = float(
            np.searchsorted(np.sort(bucket_toxicities), vpin_total) / max(len(bucket_toxicities), 1)
        )
        vpin_percentile = round(min(vpin_percentile, 1.0), 4)
    else:
        vpin_total = 0.0
        vpin_percentile = 0.0

    # ------------------------------------------------------------------ #
    # 4. UNUSUAL ACTIVITY RATING (UAR)                                     #
    # ------------------------------------------------------------------ #
    top_uar_trades = _score_uar(df)

    # ------------------------------------------------------------------ #
    # 5. NET SIGNAL & FLOW REGIME                                          #
    # ------------------------------------------------------------------ #
    # Combine call buying pressure (bullish) + put selling pressure (bearish)
    # put_flow_signal already negated: put buyers → negative contribution
    net_options_flow = float(np.clip(0.6 * call_flow_signal + 0.4 * put_flow_signal, -1.0, 1.0))

    flow_regime = _classify_regime(call_flow_signal, put_flow_signal_raw, net_options_flow)

    logger.debug(
        "options_toxicity call=%.3f put=%.3f net=%.3f vpin=%.3f regime=%s",
        call_flow_signal,
        put_flow_signal,
        net_options_flow,
        vpin_total,
        flow_regime,
    )

    return {
        "call_flow_signal": round(call_flow_signal, 4),
        "put_flow_signal": round(put_flow_signal, 4),
        "net_options_flow": round(net_options_flow, 4),
        "vpin_total": round(vpin_total, 4),
        "vpin_percentile": vpin_percentile,
        "top_uar_trades": top_uar_trades,
        "flow_regime": flow_regime,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def toxicity_position_multiplier(
    vpin_total: float,
    vpin_percentile: float,
    flow_regime: str,
    base_multiplier: float = 1.0,
    high_toxicity_threshold: float = 0.70,  # VPIN percentile > 70% = tóxico
    toxicity_reduction: float = 0.75,  # reduce a 75% de la posición original
) -> dict[str, float | str]:
    """
    Retorna multiplicador de posición basado en toxicidad de flow.

    INPUTS:
    - vpin_total: float (resultado de get_options_flow_toxicity)
    - vpin_percentile: float (0-1, de get_options_flow_toxicity)
    - flow_regime: str ("NORMAL", "HEDGING", "STRESS", etc.)

    OUTPUTS dict:
    - multiplier: float ∈ [0.50, 1.0] (si toxicity alto → reduce size)
    - toxicity_score: float (0-1)
    - reason: str (explicación)

    LÓGICA:
    if vpin_percentile > high_toxicity_threshold:
        if flow_regime == "STRESS":
            multiplier = toxicity_reduction * 0.80  # extra penalización en estrés
        else:
            multiplier = toxicity_reduction
    else:
        multiplier = 1.0

    RESTRICCIÓN FTMO:
    - Nunca reduce debajo de 50% del base_size_pct
    - Si vpin_percentile > 0.95 → return error dict {multiplier: 0.0, reason: "EXTREME_TOXICITY_BLOCK"}
    """
    if vpin_percentile > 0.95:
        return {
            "multiplier": 0.0,
            "toxicity_score": float(vpin_percentile),
            "reason": "EXTREME_TOXICITY_BLOCK",
        }

    if vpin_percentile > high_toxicity_threshold:
        if flow_regime == "STRESS":
            multiplier = toxicity_reduction * 0.80
            reason = "High toxicity + STRESS regime"
        else:
            multiplier = toxicity_reduction
            reason = "High toxicity detected"
    else:
        multiplier = 1.0
        reason = "Normal flow toxicity"

    # Clamp to [0.5, 1.0] as per constraints
    multiplier = float(max(0.50, min(1.0, multiplier * base_multiplier)))

    return {"multiplier": multiplier, "toxicity_score": float(vpin_percentile), "reason": reason}


def _imbalance(buy_prem: pd.Series, sell_prem: pd.Series) -> float:
    """Premium-weighted order imbalance ∈ [-1, 1]."""
    total = float(buy_prem.sum() + sell_prem.sum())
    if total <= 0:
        return 0.0
    return float(np.clip((buy_prem.sum() - sell_prem.sum()) / total, -1.0, 1.0))


def _compute_vpin_buckets(df: pd.DataFrame, bucket_size: float) -> list[float]:
    """Fill fixed-volume buckets and compute per-bucket toxicity."""
    toxicities: list[float] = []
    bucket_buy = 0.0
    bucket_sell = 0.0
    bucket_vol = 0.0

    for _, row in df.iterrows():
        bv = float(row["buy_vol"])
        sv = float(row["sell_vol"])
        vol = float(row["volume"])

        bucket_buy += bv
        bucket_sell += sv
        bucket_vol += vol

        if bucket_vol >= bucket_size:
            tox = abs(bucket_buy - bucket_sell) / max(bucket_vol, 1e-12)
            toxicities.append(min(tox, 1.0))
            bucket_buy = 0.0
            bucket_sell = 0.0
            bucket_vol = 0.0

    return toxicities


def _score_uar(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Score each trade for unusual activity; return top-N dicts."""
    rows = []
    today = date.today()

    for _, row in df.iterrows():
        score = 0.0
        flags: list[str] = []

        vol = float(row["volume"])
        prem = float(row["premium"])
        opt_type = str(row["option_type"])

        # OI ratio check
        if "open_interest" in df.columns:
            oi = float(row.get("open_interest", 0) or 0)
            if oi > 0 and vol > _UAR_OI_RATIO_THRESHOLD * oi:
                score += 3.0
                flags.append(f"vol>{_UAR_OI_RATIO_THRESHOLD:.0f}×OI")
        else:
            # Without OI data: scale by raw volume magnitude
            vol_pctile = vol / max(df["volume"].max(), 1.0)
            score += vol_pctile * 2.0

        # OTM aggressiveness
        if "delta" in df.columns and "strike" in df.columns:
            delta = abs(float(row.get("delta", 0.5) or 0.5))
            # OTM threshold: |delta| < (0.50 - OTM_THRESHOLD)
            if delta < (0.50 - _UAR_OTM_THRESHOLD):
                score += 2.0
                flags.append("OTM_aggressive")

        # Expiry pressure: short-dated option flow is more unusual because
        # gamma/decay concentrates information into a smaller time window.
        if "expiry" in df.columns:
            expiry_ts = pd.to_datetime(row["expiry"], errors="coerce")
            if pd.isna(expiry_ts):
                flags.append("expiry_unknown")
            else:
                expiry_date = expiry_ts.date()
                dte = (expiry_date - today).days
                if dte < 0:
                    score *= 0.50
                    flags.append("expired_contract")
                elif dte == 0:
                    score *= _ZERO_DTE_MULTIPLIER
                    flags.append("0DTE")
                elif dte <= 7:
                    score += 1.0
                    flags.append("near_expiry")
                elif dte <= 30:
                    score += 0.5
                    flags.append("front_month")

        # Premium size weight
        score += min(prem / 10.0, 2.0)  # cap at 2 points for premium

        rows.append(
            {
                "score": float(round(float(score), 3)),
                "option_type": opt_type,
                "strike": float(row.get("strike", 0)),
                "volume": float(vol),
                "premium": float(prem),
                "flags": flags,
            }
        )

    rows.sort(key=lambda r: float(cast(Any, r["score"])), reverse=True)
    return rows[:_TOP_UAR]


def _classify_regime(
    call_signal: float,
    put_signal_raw: float,
    net: float,
) -> str:
    """Classify options flow into four market regimes.

    call_signal    : imbalance in calls (positive = call buying)
    put_signal_raw : raw put imbalance (positive = put buying, i.e. bearish)
    net            : combined directional signal
    """
    both_active = (
        abs(call_signal) > _HEDGING_IMBALANCE_THRESHOLD
        and abs(put_signal_raw) > _HEDGING_IMBALANCE_THRESHOLD
    )
    # Both calls bought AND puts bought → hedging / volatility play
    if both_active and call_signal > 0 and put_signal_raw > 0:
        return "HEDGING"

    if net > _REGIME_STRONG:
        return "BULLISH_FLOW"
    if net < -_REGIME_STRONG:
        return "BEARISH_FLOW"
    return "NEUTRAL"
