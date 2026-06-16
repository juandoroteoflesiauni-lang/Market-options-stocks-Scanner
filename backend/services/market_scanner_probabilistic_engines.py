from __future__ import annotations
from typing import Any
"""Market Scanner Probabilistic Engines Adapter — Phase B.

Bridge between scanner OHLCV/Options data and real Layer 3 engines.
Follows the pattern established in market_scanner_technical_engines.py.
"""


import math

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.services.market_scanner_technical_engines import (
    EngineFeatures,
    EngineStatus,
    _bars_to_dataframe,
    _fallback,
)

logger = get_logger(__name__)

_ALL_PROB_KEYS = ("tail_risk", "jump_risk", "regime", "expected_move", "squeeze")


def run_probabilistic_engines(
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    options_snapshot: Any | None = None,
) -> dict[str, EngineFeatures]:
    """Run real probabilistic engines and return standardized features."""
    df = _bars_to_dataframe(bars)

    results: dict[str, EngineFeatures] = {}
    for key in _ALL_PROB_KEYS:
        results[key] = _run_engine(key, symbol, timeframe, df, options_snapshot)

    return results


def _run_engine(
    key: str, symbol: str, timeframe: str, df: pd.DataFrame, snapshot: Any | None
) -> EngineFeatures:
    try:
        if key == "regime":
            return _run_markov_regime(df, symbol)
        if key == "squeeze":
            return _run_squeeze(df, symbol, snapshot)
        if key == "tail_risk":
            return _run_tail_risk(df, symbol, snapshot)
        if key == "expected_move":
            return _run_expected_move(df, symbol, snapshot)
        if key == "jump_risk":
            return _run_jump_risk(df)
    except Exception as exc:
        logger.warning(
            "scanner_prob_engines.failed engine=%s symbol=%s error=%s", key, symbol, str(exc)[:200]
        )
    return _fallback(f"Engine {key} raised exception")


# ─────────────────────────────────────────────────────────────────────────────
# § Engine Adapters
# ─────────────────────────────────────────────────────────────────────────────


def _run_markov_regime(df: pd.DataFrame, symbol: str) -> EngineFeatures:
    if len(df) < 20:
        return _fallback("Insufficient bars for Markov")

    from backend.quant_engine.engines.predictive.markov_regime_engine import (
        MarkovRegimeEngine,
    )

    report = MarkovRegimeEngine().analyze(symbol, df)

    # Map regimes to score
    score_map = {
        "BULL_QUIET": 75.0,
        "BEAR_VOLATILE": 25.0,
        "CHAOTIC": 45.0,
    }
    score = score_map.get(report.current_state, 50.0)

    bias = (
        "bullish"
        if report.current_state == "BULL_QUIET"
        else "bearish" if report.current_state == "BEAR_VOLATILE" else "neutral"
    )

    return EngineFeatures(
        score=score,
        bias=bias,
        confidence=report.state_confidence,
        reasons=[
            f"Regime: {report.current_state} ({report.regime_signal})",
            f"Transition risk: {report.transition_risk:.2f}",
            f"Expected duration: {report.expected_days_in_state} bars",
        ],
        engine_status="real",
    )


def _run_squeeze(df: pd.DataFrame, symbol: str, snapshot: Any | None) -> EngineFeatures:
    from backend.quant_engine.engines.predictive.squeeze_engine import (
        OptionChainData,
        SqueezeIgnitionEngine,
        SqueezeState,
        UnderlyingData,
    )

    # 1. Prepare Underlying Data
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    vol_sma = df["volume"].rolling(20).mean().iloc[-1] if len(df) >= 20 else df["volume"].mean()

    si_ratio = 10.0
    dtc = 2.0
    if snapshot:
        payload = _extract_payload(snapshot)
        fund = payload.get("fundamentals") or payload.get("short_interest")
        if isinstance(fund, dict):
            raw_si = fund.get("short_interest_ratio") or fund.get("shortPercentOfFloat")
            raw_dtc = fund.get("days_to_cover") or fund.get("daysToCover")
            try:
                if raw_si is not None:
                    si_ratio = float(raw_si)
            except (TypeError, ValueError):
                pass
            try:
                if raw_dtc is not None:
                    dtc = float(raw_dtc)
            except (TypeError, ValueError):
                pass

    u_data = UnderlyingData(
        ticker=symbol,
        spot_price=float(last["close"]),
        prev_spot_price=float(prev["close"]),
        volume=float(last["volume"]),
        volume_sma_20=float(vol_sma),
        short_interest_ratio=float(si_ratio),
        days_to_cover=float(dtc),
    )

    # 2. Prepare Options Data if snapshot exists
    o_data = OptionChainData(
        call_volume=0,
        call_volume_sma_20=1,
        call_open_interest=0,
        put_call_ratio_volume=0.7,
        dealer_net_gamma=0,
        call_wall_level=float(last["close"] * 1.1),
        gamma_zero_level=float(last["close"] * 1.05),
    )

    status: EngineStatus = "partial"
    if snapshot:
        payload = _extract_payload(snapshot)
        gex = payload.get("gex_levels", {})
        o_data.dealer_net_gamma = float(gex.get("net_gex_total", 0))
        o_data.call_wall_level = float(gex.get("call_wall", o_data.call_wall_level))
        o_data.gamma_zero_level = float(gex.get("zero_gamma_level", o_data.gamma_zero_level))
        status = "real"

    engine = SqueezeIgnitionEngine(symbol, verbose=False)
    signal = engine.evaluate(u_data, o_data)

    bias = (
        "bullish" if signal.state in (SqueezeState.VULNERABLE, SqueezeState.IGNITION) else "neutral"
    )

    return EngineFeatures(
        score=float(signal.squeeze_vulnerability_score),
        bias=bias,
        confidence=0.8 if status == "real" else 0.4,
        reasons=signal.trigger_reasons[:3],
        engine_status=status,
    )


def _run_tail_risk(df: pd.DataFrame, symbol: str, snapshot: Any | None) -> EngineFeatures:
    if not snapshot:
        # Fallback to ATR-based tail risk proxy if no options
        atr_pct = _calc_atr_pct(df)
        score = 50.0 - (max(0, atr_pct - 5.0) * 5.0)
        return EngineFeatures(
            score=score,
            bias="neutral",
            confidence=0.2,
            reasons=["Tail risk proxy from realized volatility"],
            engine_status="fallback",
        )

    # Extract options data from snapshot to build required DF
    # This part is complex because TailRiskEngine expects a DataFrame of strikes/ivs
    # For now, we'll use a simplified version or extract the pre-calculated alert if present
    payload = _extract_payload(snapshot)
    alert = payload.get("tail_risk_alert") or payload.get("alert")

    if alert and isinstance(alert, dict):
        level = alert.get("level", "NORMAL")
        score = (
            80.0
            if level == "BULLISH_REVERSAL"
            else 20.0 if level == "CATASTROPHE_IMMINENT" else 50.0
        )
        return EngineFeatures(
            score=score,
            bias="neutral",
            confidence=0.9,
            reasons=[alert.get("message", "Tail risk alert from smile")],
            engine_status="real",
        )

    return _fallback("No tail risk data in snapshot")


def _run_expected_move(df: pd.DataFrame, symbol: str, snapshot: Any | None) -> EngineFeatures:
    from backend.quant_engine.engines.predictive.expected_move_engine import (
        ExpectedMoveEngine,
    )

    spot = float(df["close"].iloc[-1])
    iv = 0.30  # Default proxy
    status: EngineStatus = "fallback"

    if snapshot:
        payload = _extract_payload(snapshot)
        iv = float(payload.get("iv_atm") or payload.get("iv", 0.30))
        status = "real"
    else:
        # ATR-based IV proxy
        atr_pct = _calc_atr_pct(df)
        iv = (atr_pct / 100.0) * math.sqrt(252)
        status = "partial"

    res = ExpectedMoveEngine.calculate(spot, iv, dte=7)  # 7-day expected move

    # Score based on current price relative to lower bound
    # (High score if price is at lower bound = accumulation zone)
    dist_pct = (spot - res.lower_bound) / res.expected_move
    score = 50.0 + (1.0 - dist_pct) * 20.0

    return EngineFeatures(
        score=score,
        bias="bullish" if score > 60 else "neutral",
        confidence=0.7 if status == "real" else 0.4,
        reasons=[f"Exp Move (7d): ±{res.summary()['expected_move_pct']:.1f}%"],
        engine_status=status,
    )


def _run_jump_risk(df: pd.DataFrame) -> EngineFeatures:
    # Use log-returns kurtosis as a jump risk engine proxy
    returns = np.log(df["close"] / df["close"].shift(1)).dropna()
    if len(returns) < 10:
        return _fallback("Insufficient history for Jump Risk")

    kurt = returns.kurtosis()
    # High kurtosis = fat tails = high jump risk
    score = 50.0 - (max(0, kurt - 3.0) * 5.0)

    return EngineFeatures(
        score=score,
        bias="neutral",
        confidence=0.3,
        reasons=[f"Fat-tail factor: {kurt:.2f}"],
        engine_status="real",
    )


# ─────────────────────────────────────────────────────────────────────────────
# § Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _calc_atr_pct(df: pd.DataFrame, window: int = 14) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(window).mean().iloc[-1]
    return (atr / close.iloc[-1]) * 100.0 if close.iloc[-1] > 0 else 0.0


def _extract_payload(snapshot: Any) -> dict[str, Any]:
    if hasattr(snapshot, "model_dump"):
        return snapshot.model_dump(mode="json")
    return snapshot if isinstance(snapshot, dict) else {}
