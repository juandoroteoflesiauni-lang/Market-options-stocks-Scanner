"""Replay broadcast de snapshot ~5min + 8 motores híbridos para R1. # [PD-3][TH]"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.config.alpaca_r1_options_scoring_config import R1_FAMILY_ENGINES
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_options_models import (
    OptionsDirection,
    OptionsEngineSignal,
    OptionsFamily,
    Route1OptionsSnapshotContext,
)
from backend.services.research.research_types import _safe_float

logger = get_logger(__name__)

_BULLISH_TOKENS = frozenset(
    {
        "LONG",
        "BULL",
        "BULL_TRAP",
        "CONFIRMED_UP",
        "REVERSAL_LONG",
        "SQUEEZE_BREAK_LONG",
        "GAMMA_FLIP_LONG",
        "MOMENTUM_LONG",
        "ACCUMULATION_BOTTOM",
        "FLOW_EXHAUSTION_LONG",
        "SWEEP_SURGE_LONG",
    }
)
_BEARISH_TOKENS = frozenset(
    {
        "SHORT",
        "BEAR",
        "BEAR_TRAP",
        "CONFIRMED_DOWN",
        "REVERSAL_SHORT",
        "SQUEEZE_BREAK_SHORT",
        "GAMMA_FLIP_SHORT",
        "MOMENTUM_SHORT",
        "DISTRIBUTION_TOP",
        "FLOW_EXHAUSTION_SHORT",
        "SWEEP_SURGE_SHORT",
    }
)

_ENGINE_FAMILY: dict[str, OptionsFamily] = {
    engine: family  # type: ignore[assignment]
    for family, engines in R1_FAMILY_ENGINES.items()
    for engine in engines
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _motor_direction(signal_name: str) -> OptionsDirection:
    token = str(signal_name or "NEUTRAL").upper()
    if token in _BULLISH_TOKENS or "LONG" in token:
        return "BULL"
    if token in _BEARISH_TOKENS or "SHORT" in token or "BEAR" in token:
        return "BEAR"
    return "NEUTRAL"


def _motor_score(result: dict[str, Any] | None) -> float:
    """Normaliza la fuerza de un motor a [0,1].

    Convenciones de los motores de opciones:
      - ``score``: ya viene normalizado en [0,1] (volume_profile_oi, hybrid_ribbon,
        vidya, cvd) → se usa tal cual.
      - ``strength`` / ``signal_strength``: fuerza discreta. La mayoría reporta una
        escala 0-3 (delta_rsi, sma_gamma, shadow_macd, bb_gex); volume_profile_oi
        reporta 0-100. Se detecta la escala por el rango del valor.
    """
    if not result:
        return 0.0
    raw = result.get("score")
    if raw is not None:
        return _clamp01(float(raw))
    # bb_gex usa la clave 'signal_strength'; el resto usa 'strength'.
    strength = result.get("strength")
    if strength is None:
        strength = result.get("signal_strength")
    if strength is not None:
        value = float(strength)
        # >3 ⇒ escala 0-100; en caso contrario escala discreta 0-3.
        return _clamp01(value / 100.0) if value > 3.0 else _clamp01(value / 3.0)
    return 0.0


def _signal_from_result(
    engine: str,
    result: dict[str, Any] | None,
    *,
    signal_key: str = "signal",
) -> OptionsEngineSignal:
    family = _ENGINE_FAMILY[engine]
    if not result:
        return OptionsEngineSignal(
            engine=engine,
            family=family,
            direction="NEUTRAL",
            score=0.0,
            detail={},
        )
    token = str(
        result.get(signal_key)
        or result.get("signal_name")
        or result.get("bias")
        or "NEUTRAL"
    )
    return OptionsEngineSignal(
        engine=engine,
        family=family,
        direction=_motor_direction(token),
        score=_motor_score(result),
        detail={"raw_signal": token, **{k: v for k, v in result.items() if k != "history"}},
    )


def _merge_signals(
    engine: str,
    results: list[dict[str, Any] | None],
    *,
    signal_key: str = "signal",
) -> OptionsEngineSignal:
    valid = [r for r in results if r]
    if not valid:
        return _signal_from_result(engine, None)
    score = sum(_motor_score(r) for r in valid) / len(valid)
    dirs = [_motor_direction(str(r.get(signal_key) or r.get("signal_name") or "NEUTRAL")) for r in valid]
    bull = dirs.count("BULL")
    bear = dirs.count("BEAR")
    direction: OptionsDirection = "NEUTRAL"
    if bull > bear:
        direction = "BULL"
    elif bear > bull:
        direction = "BEAR"
    return OptionsEngineSignal(
        engine=engine,
        family=_ENGINE_FAMILY[engine],
        direction=direction,
        score=_clamp01(score),
        detail={"merged_from": len(valid)},
    )


def _kline_ts(k: dict[str, Any]) -> pd.Timestamp:
    raw = k.get("open_time_ms") or k.get("t") or k.get("timestamp")
    if raw is None:
        return pd.Timestamp.utcnow()
    if isinstance(raw, (int, float)):
        unit = "ms" if float(raw) > 1e12 else "s"
        return pd.to_datetime(raw, unit=unit)
    return pd.Timestamp(raw)


def _normalize_klines(klines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for k in klines:
        out.append(
            {
                "open": _safe_float(k.get("open") or k.get("o")) or 0.0,
                "high": _safe_float(k.get("high") or k.get("h")) or 0.0,
                "low": _safe_float(k.get("low") or k.get("l")) or 0.0,
                "close": _safe_float(k.get("close") or k.get("c")) or 0.0,
                "volume": _safe_float(k.get("volume") or k.get("v")) or 0.0,
                "open_time_ms": k.get("open_time_ms") or k.get("t"),
            }
        )
    return out


class AlpacaR1OptionsReplay:
    """Broadcast del último snapshot sobre barras R1 y corrida de 8 motores."""

    @classmethod
    def run(
        cls,
        klines: list[dict[str, Any]],
        context: Route1OptionsSnapshotContext | None,
    ) -> list[OptionsEngineSignal]:
        """Ejecuta motores; sin contexto → lista vacía (passthrough)."""
        if context is None or not context.available:
            return []
        normalized = _normalize_klines(klines)
        if len(normalized) < 5:
            return []
        try:
            return cls._run_engines(context, normalized)
        except Exception as exc:
            logger.warning(
                "alpaca_r1_options_replay.failed symbol=%s error=%s",
                context.symbol,
                str(exc)[:120],
            )
            return []

    @classmethod
    def _run_engines(
        cls,
        context: Route1OptionsSnapshotContext,
        klines: list[dict[str, Any]],
    ) -> list[OptionsEngineSignal]:
        from backend.quant_engine.engines.options.bb_dynamic import (
            BBGEXEngine,
            CandleBar,
            OptionsRegime,
        )
        from backend.quant_engine.engines.options.cvd_suite import (
            CVDGammaWeightedEngine,
            CVDNddeDivergenceEngine,
        )
        from backend.quant_engine.engines.options.delta_rsi import (
            DeltaRSIEngine,
            OptionsFlow,
        )
        from backend.quant_engine.engines.options.hybrid_ribbon import HybridEMADeltaRibbonEngine
        from backend.quant_engine.engines.options.shadow_macd import (
            OptionStrike,
            OptionsChainSnapshot,
            ShadowMACDEngine,
        )
        from backend.quant_engine.engines.options.sma_gamma import SMAGammaEngine
        from backend.quant_engine.engines.options.vidya_suite import (
            VidyaGammaSpeedEngine,
            VidyaIVAdaptiveEngine,
        )
        from backend.quant_engine.engines.options.volume_profile_oi import VolumeProfileOIEngine

        snapshot = context.snapshot
        features = context.features
        symbol = context.symbol
        gex_levels: dict[str, Any] = snapshot.get("gex_levels") or {}
        engine_signal: dict[str, Any] = snapshot.get("engine_signal") or {}
        iv_surface: dict[str, Any] = snapshot.get("iv_surface") or {}
        chain = snapshot.get("chain") or []
        spot = _safe_float(snapshot.get("spot")) or _safe_float(klines[-1].get("close")) or 0.0
        if spot <= 0:
            return []

        gamma_flip = _safe_float(gex_levels.get("zero_gamma_level")) or spot
        net_gex = _safe_float(engine_signal.get("total_gex")) or 0.0
        iv_atm = _safe_float(iv_surface.get("atm_iv")) or 0.20
        call_wall = context.call_wall or _safe_float(gex_levels.get("call_wall")) or spot * 1.05
        put_wall = context.put_wall or _safe_float(gex_levels.get("put_wall")) or spot * 0.95

        opt_regime = OptionsRegime(
            timestamp=pd.Timestamp(context.as_of),
            ticker=symbol,
            iv_atm=iv_atm,
            iv_25d_call=_safe_float(iv_surface.get("iv_25d_call")) or iv_atm,
            iv_25d_put=_safe_float(iv_surface.get("iv_25d_put")) or iv_atm,
            iv_term_1w=iv_atm,
            iv_term_1m=iv_atm,
            net_gex=net_gex,
            gamma_flip=gamma_flip,
            gamma_wall_up=float(call_wall or spot * 1.05),
            gamma_wall_down=float(put_wall or spot * 0.95),
        )

        strikes_data = []
        if isinstance(chain, list):
            for row in chain:
                if not isinstance(row, dict):
                    continue
                strike = _safe_float(row.get("strike"))
                if strike is None:
                    continue
                strikes_data.append(
                    OptionStrike(
                        strike=strike,
                        expiry=str(row.get("expiry", "")),
                        call_delta=_safe_float(row.get("call_delta")) or 0.0,
                        put_delta=_safe_float(row.get("put_delta")) or 0.0,
                        call_oi=int(_safe_float(row.get("call_oi")) or 0),
                        put_oi=int(_safe_float(row.get("put_oi")) or 0),
                        call_gamma=_safe_float(row.get("call_gamma")) or 0.0,
                        put_gamma=_safe_float(row.get("put_gamma")) or 0.0,
                        iv=_safe_float(row.get("iv")) or iv_atm,
                    )
                )

        chain_snap = OptionsChainSnapshot(
            timestamp=pd.Timestamp(context.as_of),
            ticker=symbol,
            spot_price=spot,
            strikes=strikes_data,
        )

        shadow_delta = _safe_float(features.get("shadow_delta_signal")) or 0.0
        ndde_seed = _safe_float(features.get("ndde_signal")) or shadow_delta * 1000.0
        opt_flow = OptionsFlow(
            timestamp=pd.Timestamp(context.as_of),
            ticker=symbol,
            call_buy_vol_delta=max(1.0, shadow_delta * 100.0) if shadow_delta > 0 else 1.0,
            put_sell_vol_delta=0.0,
            put_buy_vol_delta=max(1.0, -shadow_delta * 100.0) if shadow_delta < 0 else 0.0,
            call_sell_vol_delta=1.0,
            net_premium=0.0,
            sweep_count=0,
            iv_atm=iv_atm,
            net_gex=net_gex,
        )

        bb_engine = BBGEXEngine(ticker=symbol, sigma_mode="iv")
        smacd_engine = ShadowMACDEngine(ticker=symbol)
        drsi_engine = DeltaRSIEngine(ticker=symbol)
        smag_engine = SMAGammaEngine(ticker=symbol)
        hybrid_engine = HybridEMADeltaRibbonEngine(ticker=symbol)
        vp_engine = VolumeProfileOIEngine(ticker=symbol)
        cd_engine = CVDNddeDivergenceEngine(ticker=symbol)
        cg_engine = CVDGammaWeightedEngine(ticker=symbol)
        vi_engine = VidyaIVAdaptiveEngine(ticker=symbol)
        vg_engine = VidyaGammaSpeedEngine(ticker=symbol)

        bb_res = smacd_res = drsi_res = None
        smag_res = hybrid_res = None
        vp_res = None
        cd_res = cg_res = None
        vi_res = vg_res = None

        recent = klines[-30:] if len(klines) >= 30 else klines
        for i, k in enumerate(recent):
            is_last = i == len(recent) - 1
            k_ts = _kline_ts(k)
            candle = CandleBar(
                timestamp=k_ts,
                ticker=symbol,
                open=float(k["open"] or spot),
                high=float(k["high"] or spot),
                low=float(k["low"] or spot),
                close=float(k["close"] or spot),
                volume=float(k["volume"] or 0.0),
            )

            bb_res = bb_engine.update(candle, opt_regime if is_last else None)
            smacd_res = smacd_engine.update(candle, chain_snap if is_last else None)
            drsi_res = drsi_engine.update(candle, opt_flow if is_last else None)
            ndde_val = float(smacd_res.get("ndde", ndde_seed)) if smacd_res else ndde_seed

            if is_last:
                smag_res = smag_engine.update(
                    close=candle.close,
                    net_gex=net_gex,
                    timestamp=k_ts,
                )
                hybrid_res = hybrid_engine.update(
                    close=candle.close,
                    net_shadow_delta=shadow_delta,
                    iv_atm=iv_atm,
                    net_gex=net_gex,
                    gamma_flip=gamma_flip,
                    sweep_count=0,
                    timestamp=k_ts,
                )

            vp_res = vp_engine.update(
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                chain_snap=chain_snap if is_last else None,
            )
            cd_res = cd_engine.update(close=candle.close, delta=shadow_delta, ndde=ndde_val)
            cg_res = cg_engine.update(
                close=candle.close, delta=shadow_delta, total_gex=net_gex
            )
            vi_res = vi_engine.update(close=candle.close, iv_atm=iv_atm)
            vg_res = vg_engine.update(close=candle.close, total_gex=net_gex)

        return [
            _signal_from_result("delta_rsi", drsi_res),
            _signal_from_result("shadow_macd", smacd_res, signal_key="signal_name"),
            _merge_signals("vidya_iv_gamma", [vi_res, vg_res]),
            _merge_signals("cvd_ndde_gamma", [cd_res, cg_res]),
            _signal_from_result("volume_profile_oi", vp_res),
            _signal_from_result("bb_gex", bb_res),
            _signal_from_result("sma_gamma", smag_res),
            _signal_from_result("hybrid_ribbon", hybrid_res),
        ]


__all__ = ["AlpacaR1OptionsReplay"]
