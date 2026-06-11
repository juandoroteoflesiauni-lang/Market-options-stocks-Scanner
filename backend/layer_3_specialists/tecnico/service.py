"""Bloque tecnico para thesis a partir de OHLCV interno."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from backend.domain.thesis_v2 import ThesisBlock

from .smc import SMCEngine
from .smc_fractal_engine import SMCFractalEngine
from .volume_profile import VolumeProfileEngine
from .vsa import VSAEngine


def build_technical_thesis_block_from_ohlcv(symbol: str, df: pd.DataFrame | None) -> ThesisBlock:
    """Metricas de tendencia, rango, volumen y estructura proxy desde OHLCV."""
    if df is None or df.empty or "close" not in df.columns:
        return ThesisBlock(
            metrics={"symbol": symbol},
            source="UNAVAILABLE",
            limitations=["Insufficient OHLCV rows for technical block."],
            confidence=0.0,
        )

    frame = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    close = frame["close"].dropna()
    if len(close) < 5:
        return ThesisBlock(
            metrics={"symbol": symbol, "bars": len(close)},
            source="UNAVAILABLE",
            limitations=["Need at least 5 closes for realized volatility estimate."],
            confidence=0.0,
        )

    rets = close.pct_change().dropna()
    rv20 = float(rets.tail(min(20, len(rets))).std() * np.sqrt(252)) if len(rets) else 0.0
    cum_ret_60 = float(close.iloc[-1] / close.iloc[max(0, len(close) - 60)] - 1.0)

    ema_21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    ema_50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    metrics: dict[str, object] = {
        "symbol": symbol,
        "last_close": float(close.iloc[-1]),
        "realized_vol_annualized_20d": rv20,
        "cumulative_return_60d": cum_ret_60,
        "bars_used": len(close),
        "ema_21": float(ema_21),
        "ema_50": float(ema_50),
        "ema_200": (
            float(close.ewm(span=200, adjust=False).mean().iloc[-1]) if len(close) >= 50 else None
        ),
    }

    delta = close.diff()
    gains = delta.clip(lower=0).rolling(14).mean()
    losses = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gains / losses.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    metrics["rsi_14"] = float(rsi.dropna().iloc[-1]) if not rsi.dropna().empty else None

    if {"high", "low", "close"}.issubset(frame.columns):
        valid = frame[["high", "low", "close"]].dropna()
        if not valid.empty:
            prev_close = valid["close"].shift(1)
            tr = pd.concat(
                [
                    valid["high"] - valid["low"],
                    (valid["high"] - prev_close).abs(),
                    (valid["low"] - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr14 = tr.rolling(14).mean().dropna()
            metrics["atr_14"] = float(atr14.iloc[-1]) if not atr14.empty else None
            metrics["support_20d"] = float(valid["low"].tail(min(20, len(valid))).min())
            metrics["resistance_20d"] = float(valid["high"].tail(min(20, len(valid))).max())
            metrics["pivot_point_last"] = float(
                (valid["high"].iloc[-1] + valid["low"].iloc[-1] + valid["close"].iloc[-1]) / 3
            )

    if {"high", "low"}.issubset(frame.columns):
        valid_hl = frame[["high", "low"]].dropna()
        if len(valid_hl) >= 20:
            recent = valid_hl.tail(20)
            prior = valid_hl.iloc[-40:-20] if len(valid_hl) >= 40 else valid_hl.iloc[:-20]
            metrics["structure_proxy_20d"] = {
                "higher_high": bool(not prior.empty and recent["high"].max() > prior["high"].max()),
                "higher_low": bool(not prior.empty and recent["low"].min() > prior["low"].min()),
                "lower_low": bool(not prior.empty and recent["low"].min() < prior["low"].min()),
                "lower_high": bool(not prior.empty and recent["high"].max() < prior["high"].max()),
            }

    if "volume" in frame.columns:
        volume = frame["volume"].dropna()
        if len(volume) >= 5:
            avg20 = volume.tail(min(20, len(volume))).mean()
            metrics["volume_last"] = float(volume.iloc[-1])
            metrics["volume_avg_20d"] = float(avg20)
            metrics["relative_volume_20d"] = float(volume.iloc[-1] / avg20) if avg20 else None
        if {"high", "low", "close"}.issubset(frame.columns):
            vwap_frame = (
                frame[["high", "low", "close", "volume"]].dropna().tail(min(60, len(frame)))
            )
            vol_sum = vwap_frame["volume"].sum()
            if vol_sum:
                typical = (vwap_frame["high"] + vwap_frame["low"] + vwap_frame["close"]) / 3
                metrics["vwap_approx_60d"] = float((typical * vwap_frame["volume"]).sum() / vol_sum)

    metrics["trend_regime"] = (
        "bullish"
        if float(ema_21) > float(ema_50)
        else "bearish" if float(ema_21) < float(ema_50) else "neutral"
    )

    logger = logging.getLogger("quantum_analyzer.technical_service")

    try:
        smc_result = SMCEngine().analyze(frame, ticker=symbol)
        if smc_result is not None:
            metrics["smc_bias"] = smc_result.bias
            metrics["smc_confidence"] = float(smc_result.aggregate_confidence)
            metrics["smc_ob_count"] = int(smc_result.ob_count_active)
            metrics["smc_fvg_count"] = int(smc_result.fvg_count_active)
            metrics["smc_composite_score"] = float(smc_result.composite_score)
            metrics["smc_sesgo"] = smc_result.sesgo.value
            metrics["smc_ote_top"] = smc_result.ote_top
            metrics["smc_ote_bottom"] = smc_result.ote_bottom
    except Exception as exc:
        logger.warning("SMCEngine failed for %s: %s", symbol, exc)

    try:
        fractal_result = SMCFractalEngine.analyze(df_ohlcv=frame, ticker=symbol)
        if fractal_result is not None:
            metrics["fractal_bias"] = fractal_result.bias
            metrics["fractal_fvg_active"] = bool(fractal_result.is_fvg_active)
            metrics["fractal_fvg_size"] = float(fractal_result.fvg_size)
            metrics["fractal_entropy_score"] = float(fractal_result.entropy_score)
    except Exception as exc:
        logger.warning("SMCFractalEngine failed for %s: %s", symbol, exc)

    try:
        vsa_result = VSAEngine().analyze(frame, ticker=symbol)
        if vsa_result is not None:
            metrics["vsa_signal"] = vsa_result.signal.value
            metrics["vsa_rvol"] = float(vsa_result.rvol)
            metrics["vsa_buy_absorption"] = bool(vsa_result.buy_absorption)
            metrics["vsa_long_signal"] = bool(vsa_result.long_signal_active)
            metrics["vsa_composite_score"] = float(vsa_result.composite_score)
            labels = {lbl.value for lbl in vsa_result.recent_labels}
            metrics["vsa_stopping_volume"] = "STOPPING_VOLUME" in labels
            metrics["vsa_no_supply"] = "NO_SUPPLY" in labels
            metrics["vsa_no_demand"] = "NO_DEMAND" in labels
            metrics["vsa_selling_climax"] = "CLIMAX_SELL" in labels
    except Exception as exc:
        logger.warning("VSAEngine failed for %s: %s", symbol, exc)

    try:
        vp_engine = VolumeProfileEngine()
        vp_result = (
            vp_engine.compute(frame)
            if hasattr(vp_engine, "compute")
            else vp_engine.calculate(frame)
        )
        if vp_result is not None:
            metrics["vol_profile_poc"] = float(vp_result.poc)
            metrics["vol_profile_vah"] = float(vp_result.vah)
            metrics["vol_profile_val"] = float(vp_result.val)
            metrics["vol_profile_is_above_poc"] = bool(vp_result.is_above_poc)
    except Exception as exc:
        logger.warning("VolumeProfileEngine failed for %s: %s", symbol, exc)

    _smc_ok = "smc_bias" in metrics
    _vsa_ok = "vsa_signal" in metrics
    if _smc_ok and _vsa_ok:
        confidence = 0.75
    elif _smc_ok or _vsa_ok:
        confidence = 0.6
    else:
        confidence = 0.4

    return ThesisBlock(
        metrics=metrics,
        source="INTERNAL_OHLCV_SMC_VSA",
        limitations=[
            "SMC/VSA/Fractal/VolProfile calculados sobre OHLCV interno.",
        ],
        confidence=confidence,
    )
