"""Orchestrate options quantitative engines and SignalCombiner."""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from backend.config.logger_setup import get_logger
from backend.services.research.research_types import _now_iso, _safe_float

logger = get_logger(__name__)

_MIN_CHAIN_LENGTH = 4


def _fractal_chain_rows(chain: list[dict[str, Any]], spot: float) -> list[dict[str, Any]]:
    """Convierte filas de chain snapshot al formato ``FractalOIEngine``."""
    rows: list[dict[str, Any]] = []
    for row in chain:
        if not isinstance(row, dict):
            continue
        strike = _safe_float(row.get("strike")) or spot
        call_oi = int(_safe_float(row.get("call_oi")) or 0)
        put_oi = int(_safe_float(row.get("put_oi")) or 0)
        if call_oi > 0:
            rows.append(
                {"strike": strike, "open_interest": call_oi, "option_type": "CALL"},
            )
        if put_oi > 0:
            rows.append(
                {"strike": strike, "open_interest": put_oi, "option_type": "PUT"},
            )
    return rows


def _json_safe_combiner(value: Any) -> Any:
    """Recursively convert combiner payloads to JSON-serializable values."""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe_combiner(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_combiner(item) for item in value]
    return value


def run_options_combiner(
    underlying_symbol: str,
    *,
    snapshot: dict[str, Any],
    klines: tuple | list | None = None,
    spot: float | None = None,
) -> dict[str, Any]:
    """Run options engines and SignalCombiner for one snapshot.

    Never raises. Returns ``{"ok", "reason", "combiner", "ndde"}``.
    """
    resolved_spot = spot if spot is not None else _safe_float(snapshot.get("spot"))
    chain = snapshot.get("chain") or []

    if resolved_spot is None or resolved_spot <= 0:
        return {"ok": False, "reason": "invalid_spot", "combiner": None, "ndde": None}
    if not isinstance(chain, list) or len(chain) < _MIN_CHAIN_LENGTH:
        return {"ok": False, "reason": "chain_too_short", "combiner": None, "ndde": None}

    recent_klines = klines[-30:] if klines and len(klines) >= 30 else (klines or [])
    if not recent_klines:
        return {"ok": False, "reason": "no_klines", "combiner": None, "ndde": None}

    try:
        from backend.quant_engine.engines.options.bb_dynamic import (
            BBGEXEngine,
            CandleBar,
            OptionsRegime,
        )
        from backend.quant_engine.engines.options.blocks_suite import (
            BlockSweepEngine,
            IcebergVannaEngine,
            VolumeBubbleGammaEngine,
        )
        from backend.quant_engine.engines.options.cvd_suite import (
            CVDFootprintEngine,
            CVDGammaWeightedEngine,
            CVDNddeDivergenceEngine,
        )
        from backend.quant_engine.engines.options.delta_profile_hibrido import (
            DeltaProfileHibridoEngine,
        )
        from backend.quant_engine.engines.options.delta_rsi import DeltaRSIEngine, OptionsFlow
        from backend.quant_engine.engines.options.fractal_oi import FractalOIEngine
        from backend.quant_engine.engines.options.gex_profile import GEXProfileEngine
        from backend.quant_engine.engines.options.hull_iv import HullIVEngine
        from backend.quant_engine.engines.options.hybrid_ribbon import HybridEMADeltaRibbonEngine
        from backend.quant_engine.engines.options.shadow_macd import (
            OptionsChainSnapshot,
            OptionStrike,
            ShadowMACDEngine,
        )
        from backend.quant_engine.engines.options.signal_combiner import (
            BBGEXInput,
            BlockSweepInput,
            CVDDivergenceInput,
            CVDFootprintInput,
            CVDGammaInput,
            DeltaProfileInput,
            DeltaRSIInput,
            FractalOIInput,
            GEXProfileInput,
            GEXVWAPInput,
            HullIVInput,
            HybridRibbonInput,
            IcebergVannaInput,
            ShadowMACDInput,
            SignalCombiner,
            SMAGammaInput,
            VidyaCVDInput,
            VidyaGammaInput,
            VidyaIVInput,
            VolumeBubbleInput,
            VolumeProfileOIInput,
        )
        from backend.quant_engine.engines.options.sma_gamma import SMAGammaEngine
        from backend.quant_engine.engines.options.vidya_suite import (
            VidyaCvdEngine,
            VidyaGammaSpeedEngine,
            VidyaIVAdaptiveEngine,
        )
        from backend.quant_engine.engines.options.volume_profile_oi import VolumeProfileOIEngine

        gex_levels: dict[str, Any] = snapshot.get("gex_levels") or {}
        engine_signal: dict[str, Any] = snapshot.get("engine_signal") or {}
        iv_surface: dict[str, Any] = snapshot.get("iv_surface") or {}
        gamma_flip = _safe_float(gex_levels.get("zero_gamma_level"))

        opt_regime = OptionsRegime(
            timestamp=pd.Timestamp(_now_iso()),
            ticker=underlying_symbol,
            iv_atm=iv_surface.get("atm_iv", 0.20) or 0.20,
            iv_25d_call=iv_surface.get("iv_25d_call", 0.18) or 0.18,
            iv_25d_put=iv_surface.get("iv_25d_put", 0.22) or 0.22,
            iv_term_1w=0.20,
            iv_term_1m=0.22,
            net_gex=_safe_float(engine_signal.get("total_gex")) or 0.0,
            gamma_flip=gamma_flip or resolved_spot,
            gamma_wall_up=gex_levels.get("call_wall", resolved_spot * 1.05) or resolved_spot * 1.05,
            gamma_wall_down=gex_levels.get("put_wall", resolved_spot * 0.95)
            or resolved_spot * 0.95,
        )

        strikes_data = []
        for row in chain:
            if isinstance(row, dict):
                strikes_data.append(
                    OptionStrike(
                        strike=_safe_float(row.get("strike")) or resolved_spot,
                        expiry=str(row.get("expiry", "")),
                        call_delta=_safe_float(row.get("call_delta")) or 0.0,
                        put_delta=_safe_float(row.get("put_delta")) or 0.0,
                        call_oi=int(_safe_float(row.get("call_oi")) or 0),
                        put_oi=int(_safe_float(row.get("put_oi")) or 0),
                        call_gamma=_safe_float(row.get("call_gamma")) or 0.0,
                        put_gamma=_safe_float(row.get("put_gamma")) or 0.0,
                        iv=_safe_float(row.get("iv")) or 0.2,
                    )
                )

        chain_snap = OptionsChainSnapshot(
            timestamp=pd.Timestamp(_now_iso()),
            ticker=underlying_symbol,
            spot_price=resolved_spot,
            strikes=strikes_data,
        )

        opt_flow = OptionsFlow(
            timestamp=pd.Timestamp(_now_iso()),
            ticker=underlying_symbol,
            call_buy_vol_delta=1,
            put_sell_vol_delta=0,
            put_buy_vol_delta=0,
            call_sell_vol_delta=1,
            net_premium=0,
            sweep_count=0,
            iv_atm=opt_regime.iv_atm,
            net_gex=opt_regime.net_gex,
        )

        bb_engine = BBGEXEngine(ticker=underlying_symbol, sigma_mode="iv")
        smacd_engine = ShadowMACDEngine(ticker=underlying_symbol)
        drsi_engine = DeltaRSIEngine(ticker=underlying_symbol)
        smag_engine = SMAGammaEngine(ticker=underlying_symbol)
        fractal_engine = FractalOIEngine(ticker=underlying_symbol)
        hull_engine = HullIVEngine(ticker=underlying_symbol)
        hybrid_engine = HybridEMADeltaRibbonEngine(ticker=underlying_symbol)
        vp_engine = VolumeProfileOIEngine(ticker=underlying_symbol)
        gp_engine = GEXProfileEngine(ticker=underlying_symbol)
        dp_engine = DeltaProfileHibridoEngine(ticker=underlying_symbol)
        cd_engine = CVDNddeDivergenceEngine(ticker=underlying_symbol)
        cg_engine = CVDGammaWeightedEngine(ticker=underlying_symbol)
        cf_engine = CVDFootprintEngine(ticker=underlying_symbol)
        bs_engine = BlockSweepEngine(ticker=underlying_symbol)
        vb_engine = VolumeBubbleGammaEngine(ticker=underlying_symbol)
        ib_engine = IcebergVannaEngine(ticker=underlying_symbol)
        vi_engine = VidyaIVAdaptiveEngine(ticker=underlying_symbol)
        vg_engine = VidyaGammaSpeedEngine(ticker=underlying_symbol)
        vc_engine = VidyaCvdEngine(ticker=underlying_symbol)
        combiner = SignalCombiner(ticker=underlying_symbol)

        bb_res = smacd_res = drsi_res = None
        smag_res = fractal_res = hull_res = hybrid_res = None
        comb_res = None

        for i, k in enumerate(recent_klines):
            is_last = i == len(recent_klines) - 1
            k_ts = pd.to_datetime(k.get("open_time_ms", time.time() * 1000), unit="ms")
            candle = CandleBar(
                timestamp=k_ts,
                ticker=underlying_symbol,
                open=_safe_float(k.get("open")) or resolved_spot,
                high=_safe_float(k.get("high")) or resolved_spot,
                low=_safe_float(k.get("low")) or resolved_spot,
                close=_safe_float(k.get("close")) or resolved_spot,
                volume=_safe_float(k.get("volume")) or 0.0,
            )

            bb_res = bb_engine.update(candle, opt_regime if is_last else None)
            smacd_res = smacd_engine.update(candle, chain_snap if is_last else None)
            drsi_res = drsi_engine.update(candle, opt_flow if is_last else None)

            if is_last:
                net_sd = opt_flow.call_buy_vol_delta - opt_flow.put_buy_vol_delta
                smag_res = smag_engine.update(
                    close=candle.close,
                    net_gex=opt_regime.net_gex,
                    timestamp=k_ts,
                )
                fractal_res = fractal_engine.update(
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    chain=_fractal_chain_rows(chain, resolved_spot) if is_last else [],
                    timestamp=k_ts,
                )
                hull_res = hull_engine.update(
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    iv_atm=opt_regime.iv_atm,
                    timestamp=k_ts,
                )
                hybrid_res = hybrid_engine.update(
                    close=candle.close,
                    net_shadow_delta=net_sd,
                    iv_atm=opt_regime.iv_atm,
                    net_gex=opt_regime.net_gex,
                    gamma_flip=opt_regime.gamma_flip,
                    sweep_count=opt_flow.sweep_count,
                    timestamp=k_ts,
                )

            ndde_val = smacd_res.get("ndde", 0.0) if smacd_res else 0.0

            vp_res = vp_engine.update(
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                chain_snap=chain_snap if is_last else None,
            )
            gp_res = gp_engine.update(
                close=candle.close, chain_snap=chain_snap if is_last else None
            )
            dp_res = dp_engine.update(
                high=candle.high,
                low=candle.low,
                close=candle.close,
                spot_delta=0.0,
                options_net_flow=ndde_val,
            )
            cd_res = cd_engine.update(close=candle.close, delta=0.0, ndde=ndde_val)
            cg_res = cg_engine.update(close=candle.close, delta=0.0, total_gex=opt_regime.net_gex)
            cf_res = cf_engine.update(
                open=candle.open,
                close=candle.close,
                delta=0.0,
                call_buy_vol_delta=opt_flow.call_buy_vol_delta,
                put_buy_vol_delta=opt_flow.put_buy_vol_delta,
            )
            bs_res = bs_engine.update(
                close=candle.close,
                volume=candle.volume,
                delta=0.0,
                sweep_count=opt_flow.sweep_count,
                call_buy_vol_delta=opt_flow.call_buy_vol_delta,
                put_buy_vol_delta=opt_flow.put_buy_vol_delta,
            )
            vb_res = vb_engine.update(
                close=candle.close,
                volume=candle.volume,
                delta=0.0,
                chain_snap=chain_snap if is_last else None,
            )
            ib_res = ib_engine.update(
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                delta=0.0,
            )
            vi_res = vi_engine.update(close=candle.close, iv_atm=opt_regime.iv_atm)
            vg_res = vg_engine.update(close=candle.close, total_gex=opt_regime.net_gex)
            vc_res = vc_engine.update(
                close=candle.close, volume=candle.volume, delta=0.0, ndde=ndde_val
            )

            if is_last:
                vwap_inp = GEXVWAPInput(
                    signal="NEUTRAL",
                    price_vs_vwap=0.0,
                    shadow_ratio=0.0,
                    regime=bb_res.get("regime", "GAMMA_POS"),
                    net_gamma=opt_regime.net_gex,
                    band_mult=2.0,
                )
                bb_inp = BBGEXInput(
                    signal=bb_res.get("signal", "NEUTRAL"),
                    pct_b=bb_res.get("pct_b", 0.5),
                    bandwidth=bb_res.get("bandwidth", 2.0),
                    k_multiplier=bb_res.get("k_multiplier", 2.0),
                    regime=bb_res.get("regime", "GAMMA_POS"),
                    gamma_flip_cross=bb_res.get("gamma_flip_cross", False),
                )
                drsi_inp = DeltaRSIInput(
                    signal=drsi_res.get("signal", "NEUTRAL"),
                    strength=drsi_res.get("strength", 0),
                    delta_rsi=(
                        drsi_res.get("delta_rsi", 50.0)
                        if not pd.isna(drsi_res.get("delta_rsi", 50.0))
                        else 50.0
                    ),
                    histogram=(
                        drsi_res.get("histogram", 0.0)
                        if not pd.isna(drsi_res.get("histogram", 0.0))
                        else 0.0
                    ),
                    zone=drsi_res.get("zone", "NEUTRAL"),
                    sweep_count=drsi_res.get("sweep_count", 0),
                    flow_ratio=drsi_res.get("flow_ratio", 0.0),
                )
                smacd_inp = ShadowMACDInput(
                    signal_name=smacd_res.get("signal_name", "NEUTRAL"),
                    strength=smacd_res.get("strength", 0),
                    macd=(
                        smacd_res.get("macd", 0.0)
                        if not pd.isna(smacd_res.get("macd", 0.0))
                        else 0.0
                    ),
                    histogram=(
                        smacd_res.get("histogram", 0.0)
                        if not pd.isna(smacd_res.get("histogram", 0.0))
                        else 0.0
                    ),
                    ndde=smacd_res.get("ndde", 0.0),
                    charm_flow=smacd_res.get("charm_flow", 0.0),
                    put_call_ratio=smacd_res.get("put_call_ratio", 1.0),
                )

                smag_inp = SMAGammaInput(
                    signal=smag_res.get("signal", "NEUTRAL") if smag_res else "NEUTRAL",
                    strength=smag_res.get("strength", 0) if smag_res else 0,
                    bias=smag_res.get("bias", "NEUTRAL") if smag_res else "NEUTRAL",
                    deviation=smag_res.get("deviation", 0.0) if smag_res else 0.0,
                )
                fractal_inp = FractalOIInput(
                    signal=(fractal_res.get("signal", "NEUTRAL") if fractal_res else "NEUTRAL"),
                    strength=fractal_res.get("strength", 0) if fractal_res else 0,
                    zona_rechazo=(fractal_res.get("zona_rechazo", False) if fractal_res else False),
                )
                hull_inp = HullIVInput(
                    signal=hull_res.get("signal", "NEUTRAL") if hull_res else "NEUTRAL",
                    strength=hull_res.get("strength", 0) if hull_res else 0,
                    regimen=hull_res.get("regimen", "neutral") if hull_res else "neutral",
                )
                hybrid_inp = HybridRibbonInput(
                    signal=hybrid_res.get("signal", "NEUTRAL") if hybrid_res else "NEUTRAL",
                    strength=hybrid_res.get("strength", 0) if hybrid_res else 0,
                    score=hybrid_res.get("score", 0.0) if hybrid_res else 0.0,
                )

                vp_inp = VolumeProfileOIInput(
                    signal=vp_res.get("signal", "NEUTRAL") if vp_res else "NEUTRAL",
                    strength=vp_res.get("strength", 0) if vp_res else 0,
                    score=vp_res.get("score", 0.0) if vp_res else 0.0,
                )
                gp_inp = GEXProfileInput(
                    signal=gp_res.get("signal", "NEUTRAL") if gp_res else "NEUTRAL",
                    strength=gp_res.get("strength", 0) if gp_res else 0,
                    score=gp_res.get("score", 0.0) if gp_res else 0.0,
                )
                dp_inp = DeltaProfileInput(
                    signal=dp_res.get("signal", "NEUTRAL") if dp_res else "NEUTRAL",
                    strength=dp_res.get("strength", 0) if dp_res else 0,
                    score=dp_res.get("score", 0.0) if dp_res else 0.0,
                )
                cd_inp = CVDDivergenceInput(
                    signal=cd_res.get("signal", "NEUTRAL") if cd_res else "NEUTRAL",
                    strength=cd_res.get("strength", 0) if cd_res else 0,
                    score=cd_res.get("score", 0.0) if cd_res else 0.0,
                )
                cg_inp = CVDGammaInput(
                    signal=cg_res.get("signal", "NEUTRAL") if cg_res else "NEUTRAL",
                    strength=cg_res.get("strength", 0) if cg_res else 0,
                    score=cg_res.get("score", 0.0) if cg_res else 0.0,
                )
                cf_inp = CVDFootprintInput(
                    signal=cf_res.get("signal", "NEUTRAL") if cf_res else "NEUTRAL",
                    strength=cf_res.get("strength", 0) if cf_res else 0,
                    score=cf_res.get("score", 0.0) if cf_res else 0.0,
                )
                bs_inp = BlockSweepInput(
                    signal=bs_res.get("signal", "NEUTRAL") if bs_res else "NEUTRAL",
                    strength=bs_res.get("strength", 0) if bs_res else 0,
                    score=bs_res.get("score", 0.0) if bs_res else 0.0,
                )
                vb_inp = VolumeBubbleInput(
                    signal=vb_res.get("signal", "NEUTRAL") if vb_res else "NEUTRAL",
                    strength=vb_res.get("strength", 0) if vb_res else 0,
                    score=vb_res.get("score", 0.0) if vb_res else 0.0,
                )
                ib_inp = IcebergVannaInput(
                    signal=ib_res.get("signal", "NEUTRAL") if ib_res else "NEUTRAL",
                    strength=ib_res.get("strength", 0) if ib_res else 0,
                    score=ib_res.get("score", 0.0) if ib_res else 0.0,
                )
                vi_inp = VidyaIVInput(
                    signal=vi_res.get("signal", "NEUTRAL") if vi_res else "NEUTRAL",
                    strength=vi_res.get("strength", 0) if vi_res else 0,
                    score=vi_res.get("score", 0.0) if vi_res else 0.0,
                )
                vg_inp = VidyaGammaInput(
                    signal=vg_res.get("signal", "NEUTRAL") if vg_res else "NEUTRAL",
                    strength=vg_res.get("strength", 0) if vg_res else 0,
                    score=vg_res.get("score", 0.0) if vg_res else 0.0,
                )
                vc_inp = VidyaCVDInput(
                    signal=vc_res.get("signal", "NEUTRAL") if vc_res else "NEUTRAL",
                    strength=vc_res.get("strength", 0) if vc_res else 0,
                    score=vc_res.get("score", 0.0) if vc_res else 0.0,
                )

                comb_res = combiner.combine(
                    k_ts,
                    vwap_inp,
                    bb_inp,
                    drsi_inp,
                    smacd_inp,
                    smag_inp,
                    fractal_inp,
                    hull_inp,
                    hybrid_inp,
                    vp_inp,
                    gp_inp,
                    dp_inp,
                    cd_inp,
                    cg_inp,
                    cf_inp,
                    bs_inp,
                    vb_inp,
                    ib_inp,
                    vi_inp,
                    vg_inp,
                    vc_inp,
                )

        if comb_res is None:
            return {"ok": False, "reason": "combiner_not_run", "combiner": None, "ndde": None}

        ndde_out = float(smacd_res.get("ndde", 0.0)) if smacd_res else 0.0
        return {
            "ok": True,
            "reason": None,
            "combiner": _json_safe_combiner(comb_res.to_dict()),
            "ndde": ndde_out,
        }

    except Exception as exc:
        logger.warning(
            "run_options_combiner failure symbol=%s error=%s",
            underlying_symbol,
            str(exc)[:180],
        )
        return {
            "ok": False,
            "reason": f"{type(exc).__name__}:{str(exc)[:120]}",
            "combiner": None,
            "ndde": None,
        }
