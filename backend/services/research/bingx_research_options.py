from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

import pandas as pd

# ruff: noqa: F403, F405


logger = logging.getLogger(__name__)
from backend.config.sqlite_db_paths import OPTIONS_GEX_SNAPSHOTS_DB
from backend.services.research.research_types import *
from backend.services.research.research_types import (
    _bucket_tail_risk,
    _now_iso,
    _safe_float,
    _unavailable_desk_status,
)


def _build_gamma_flip_chain_dataframe_local(chain: list[dict[str, Any]], spot: float) -> Any:
    """Long-format chain (one row per call/put leg) for GammaFlipEngine."""
    import pandas as pd

    rows: list[dict[str, Any]] = []
    for r in chain:
        if not isinstance(r, dict):
            continue
        coi = int(r.get("call_oi") or 0)
        poi = int(r.get("put_oi") or 0)
        call_gamma = r.get("call_gamma")
        put_gamma = r.get("put_gamma")
        strike = _safe_float(r.get("strike"))
        if strike is None:
            continue
        if coi > 0 and call_gamma is not None:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "call",
                    "gamma": float(call_gamma),
                    "open_interest": coi,
                    "current_spot": float(spot),
                }
            )
        if poi > 0 and put_gamma is not None:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "put",
                    "gamma": float(put_gamma),
                    "open_interest": poi,
                    "current_spot": float(spot),
                }
            )
    return pd.DataFrame(rows)


def _build_shadow_delta_portfolio_df_local(
    chain: list[dict[str, Any]], spot: float, dte_years: float, r_rate: float
) -> Any:
    """Long-format rows (CALL/PUT per strike) for ShadowDeltaEngine from OptionStrikeRow chain."""
    import pandas as pd

    rows: list[dict[str, Any]] = []
    for row in chain:
        if not isinstance(row, dict):
            continue
        strike = _safe_float(row.get("strike"))
        if strike is None:
            continue
        coi = float(row.get("call_oi") or 0)
        poi = float(row.get("put_oi") or 0)
        civ = row.get("call_iv")
        piv = row.get("put_iv")
        if coi > 0 and civ is not None and float(civ) > 1e-6:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "CALL",
                    "iv": float(civ),
                    "open_interest": int(coi),
                    "quantity": float(coi),
                    "expiry": float(dte_years),
                    "r": float(r_rate),
                }
            )
        if poi > 0 and piv is not None and float(piv) > 1e-6:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "PUT",
                    "iv": float(piv),
                    "open_interest": int(poi),
                    "quantity": float(poi),
                    "expiry": float(dte_years),
                    "r": float(r_rate),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)


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


async def _fetch_options_gex_desk(
    underlying_symbol: str,
    market_type: str,
    *,
    options_snapshot: dict[str, Any] | None = None,
    klines: tuple[dict[str, Any], ...] | None = None,
) -> OptionsGexDeskState:
    """Read the latest Options/GEX snapshot from ``options_gex_snapshots.sqlite3``.

    Connects to the SQLite database in **read-only** mode (``mode=ro`` URI) so
    it never blocks or corrupts the write path.  All failures — missing file,
    locked DB, missing table, JSON parse errors — are caught and the desk
    degrades to ``unavailable``.

    Field mapping from ``options_gex_snapshots``
    -------------------------------------------
    Column          : ``features_json`` (JSON blob) and ``snapshot_json``
    gamma_flip      : ``snapshot_json.gex_levels.zero_gamma_level``
                       (the GEX zero-gamma level IS the gamma flip in this pipeline)
    is_gamma_neg    : derived — ``spot < gamma_flip_level``
    shadow_delta    : ``features_json.shadow_delta_signal`` (normalised [-1, 1])
    tail_risk       : bucketed from ``features_json.tail_risk_directional_signal``
    speed_warning   : ``True`` when ``features_json.composite_directional_signal``
                      has flipped sign vs ``features_json.gamma_flip_directional_signal``
                      (proxy until Speed engine writes its own column)
    zomma_risk      : normalised from engine_signal.total_gex / spot — proxy
                      until the Zomma engine exposes its own field directly.
    """
    source_tag = "options_gex_snapshots_db"

    # Crypto perps do not have equity options chains — skip immediately.
    if market_type not in ("stock_perp", "stock_index_perp"):
        return OptionsGexDeskState(
            desk_status=_unavailable_desk_status(
                source=source_tag,
                reason=REASON_MARKET_TYPE_EXCLUDED,
            )
        )

    t0 = time.monotonic()
    try:
        db_path = OPTIONS_GEX_SNAPSHOTS_DB
        if not db_path.exists():
            return OptionsGexDeskState(
                desk_status=_unavailable_desk_status(
                    source=source_tag,
                    reason="db_file_not_found",
                )
            )

        # Open in read-only mode via URI to avoid any write-lock contention.
        uri = f"file:{db_path.as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=3.0, check_same_thread=False)
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT features_json, snapshot_json, as_of, data_quality_score "
                "FROM options_gex_snapshots "
                "WHERE symbol = ? "
                "ORDER BY as_of DESC LIMIT 1",
                (underlying_symbol,),
            )
            row = cur.fetchone()
        finally:
            con.close()

        if row is None:
            return OptionsGexDeskState(
                desk_status=_unavailable_desk_status(
                    source=source_tag,
                    reason=f"no_snapshot_for_{underlying_symbol}",
                )
            )

        features_raw, snapshot_raw, as_of, db_quality = row
        features: dict[str, Any] = json.loads(features_raw) if features_raw else {}
        snapshot: dict[str, Any] = json.loads(snapshot_raw) if snapshot_raw else {}

        # ── Extract fields ──────────────────────────────────────────────
        gex_levels: dict[str, Any] = snapshot.get("gex_levels") or {}
        engine_signal: dict[str, Any] = snapshot.get("engine_signal") or {}
        iv_surface: dict[str, Any] = snapshot.get("iv_surface") or {}
        snapshot.get("confluence") or {}

        spot = _safe_float(snapshot.get("spot"))

        # Default fallbacks from DB
        gamma_flip = _safe_float(gex_levels.get("zero_gamma_level"))
        shadow_delta_raw = _safe_float(features.get("shadow_delta_signal"))
        zero_day_pinning = _safe_float(gex_levels.get("max_pain"))
        pinning_prob = None

        speed_instability = False
        gf_dir = _safe_float(features.get("gamma_flip_directional_signal")) or 0.0
        composite = _safe_float(features.get("composite_directional_signal")) or 0.0
        if gf_dir != 0.0 and composite != 0.0:
            speed_instability = gf_dir * composite < 0

        tail_risk_raw = _safe_float(features.get("tail_risk_directional_signal"))
        tail_risk_severity = _bucket_tail_risk(tail_risk_raw)

        net_gex = _safe_float(engine_signal.get("total_gex"))
        if net_gex is not None and spot is not None and spot > 0:
            zomma_risk = min(1.0, abs(net_gex) / 1_000_000_000.0)
        else:
            zomma_risk = None

        # ── Run Quantitative Engines Dynamically ──────────────────────────
        predictive_report = None
        chain = snapshot.get("chain") or []
        if spot is not None and spot > 0 and isinstance(chain, list) and len(chain) >= 4:
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
                from backend.quant_engine.engines.options.delta_rsi import (
                    DeltaRSIEngine,
                    OptionsFlow,
                )
                from backend.quant_engine.engines.options.fractal_oi import FractalOIEngine
                from backend.quant_engine.engines.options.gex_profile import GEXProfileEngine
                from backend.quant_engine.engines.options.hull_iv import HullIVEngine
                from backend.quant_engine.engines.options.hybrid_ribbon import (
                    HybridEMADeltaRibbonEngine,
                )
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
                from backend.quant_engine.engines.options.volume_profile_oi import (
                    VolumeProfileOIEngine,
                )

                # ── Dynamic Execution ─────────────────────────────────────────
                recent_klines = klines[-30:] if klines and len(klines) >= 30 else (klines or [])

                # Build OptionRegime and OptionsChainSnapshot for the current tick
                opt_regime = OptionsRegime(
                    timestamp=pd.Timestamp(_now_iso()),
                    ticker=underlying_symbol,
                    iv_atm=iv_surface.get("atm_iv", 0.20) or 0.20,
                    iv_25d_call=iv_surface.get("iv_25d_call", 0.18) or 0.18,
                    iv_25d_put=iv_surface.get("iv_25d_put", 0.22) or 0.22,
                    iv_term_1w=0.20,
                    iv_term_1m=0.22,
                    net_gex=_safe_float(engine_signal.get("total_gex")) or 0.0,
                    gamma_flip=gamma_flip or spot,
                    gamma_wall_up=gex_levels.get("call_wall", spot * 1.05) or spot * 1.05,
                    gamma_wall_down=gex_levels.get("put_wall", spot * 0.95) or spot * 0.95,
                )

                strikes_data = []
                for row in chain:
                    if isinstance(row, dict):
                        strikes_data.append(
                            OptionStrike(
                                strike=_safe_float(row.get("strike")) or spot,
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
                    spot_price=spot,
                    strikes=strikes_data,
                )

                # Flow (neutral dummy for stateless run)
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

                # Initialize engines
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

                # Feed historical klines to build state (if available)
                for i, k in enumerate(recent_klines):
                    is_last = i == len(recent_klines) - 1
                    k_ts = pd.to_datetime(k.get("open_time_ms", time.time() * 1000), unit="ms")
                    candle = CandleBar(
                        timestamp=k_ts,
                        ticker=underlying_symbol,
                        open=_safe_float(k.get("open")) or spot,
                        high=_safe_float(k.get("high")) or spot,
                        low=_safe_float(k.get("low")) or spot,
                        close=_safe_float(k.get("close")) or spot,
                        volume=_safe_float(k.get("volume")) or 0.0,
                    )

                    # Only feed real options data on the last candle
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
                            chain=_fractal_chain_rows(chain, spot) if is_last else [],
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
                    cg_res = cg_engine.update(
                        close=candle.close, delta=0.0, total_gex=opt_regime.net_gex
                    )
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
                            signal=(
                                fractal_res.get("signal", "NEUTRAL") if fractal_res else "NEUTRAL"
                            ),
                            strength=fractal_res.get("strength", 0) if fractal_res else 0,
                            zona_rechazo=(
                                fractal_res.get("zona_rechazo", False) if fractal_res else False
                            ),
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

                        # Populate predictive report with combined results
                        from backend.domain.probabilistic_models import (
                            PredictiveOptionsBundleReport,
                        )

                        gamma_neg = (
                            spot is not None and gamma_flip is not None and spot < gamma_flip
                        )
                        predictive_report = PredictiveOptionsBundleReport(
                            gamma_flip_level=float(gamma_flip) if gamma_flip is not None else 0.0,
                            is_gamma_negative_regime=bool(gamma_neg),
                            shadow_delta_imbalance=(
                                float(smacd_res.get("ndde", 0.0)) if smacd_res else 0.0
                            ),
                            zero_day_pinning_strike=(
                                float(zero_day_pinning) if zero_day_pinning is not None else 0.0
                            ),
                            speed_instability_warning=not comb_res.entry_allowed,
                            tail_risk_severity=str(tail_risk_severity or "LOW"),
                            zomma_risk_score=float(zomma_risk) if zomma_risk is not None else 0.0,
                            pinning_probability=(
                                float(pinning_prob) if pinning_prob is not None else 0.0
                            ),
                        )

            except Exception as e:
                logger.warning(
                    "_fetch_options_gex_desk dynamic engines failure: %s",
                    str(e)[:180],
                )

        is_gamma_negative = spot is not None and gamma_flip is not None and spot < gamma_flip

        # Supplementary fields.
        atm_iv = _safe_float(iv_surface.get("atm_iv"))
        call_wall = _safe_float(gex_levels.get("call_wall"))
        put_wall = _safe_float(gex_levels.get("put_wall"))
        net_gex_total = _safe_float(gex_levels.get("net_gex_total"))
        dealer_bias = str(gex_levels.get("dealer_bias") or "NEUTRAL")

        quality_score = _safe_float(db_quality) if db_quality is not None else None
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        logger.debug(
            "_fetch_options_gex_desk | symbol=%s as_of=%s gamma_flip=%.4f "
            "shadow_delta=%s tail=%s speed=%s zomma=%s latency=%.0fms",
            underlying_symbol,
            as_of,
            gamma_flip or 0.0,
            shadow_delta_raw,
            tail_risk_severity,
            speed_instability,
            zomma_risk,
            latency_ms,
        )

        # Build the real PredictiveOptionsBundleReport
        if predictive_report is None:
            # Build the real PredictiveOptionsBundleReport
            from backend.domain.probabilistic_models import PredictiveOptionsBundleReport

            predictive_report = PredictiveOptionsBundleReport(
                gamma_flip_level=float(gamma_flip) if gamma_flip is not None else 0.0,
                is_gamma_negative_regime=bool(is_gamma_negative),
                shadow_delta_imbalance=(
                    float(shadow_delta_raw) if shadow_delta_raw is not None else 0.0
                ),
                zero_day_pinning_strike=(
                    float(zero_day_pinning) if zero_day_pinning is not None else 0.0
                ),
                speed_instability_warning=bool(speed_instability),
                tail_risk_severity=str(tail_risk_severity or "LOW"),
                zomma_risk_score=float(zomma_risk) if zomma_risk is not None else 0.0,
                pinning_probability=float(pinning_prob) if pinning_prob is not None else 0.0,
            )

        return OptionsGexDeskState(
            desk_status=DeskReadStatus(
                status="available",
                source=source_tag,
                reason=None,
                quality_score=quality_score,
                latency_ms=latency_ms,
                captured_at=str(as_of),
            ),
            gamma_flip_level=gamma_flip,
            is_gamma_negative_regime=is_gamma_negative,
            shadow_delta_imbalance=shadow_delta_raw,
            zero_day_pinning_strike=zero_day_pinning,
            speed_instability_warning=speed_instability,
            tail_risk_severity=tail_risk_severity,
            zomma_risk_score=zomma_risk,
            atm_iv=atm_iv,
            call_wall=call_wall,
            put_wall=put_wall,
            net_gex_total=net_gex_total,
            dealer_bias=dealer_bias,
            predictive_report=predictive_report,
        )

    except Exception as exc:
        reason = f"{REASON_DESK_FETCH_FAILED}:{type(exc).__name__}:{str(exc)[:120]}"
        logger.warning(
            "_fetch_options_gex_desk.failed symbol=%s error=%s",
            underlying_symbol,
            reason,
        )
        return OptionsGexDeskState(
            desk_status=_unavailable_desk_status(
                source=source_tag,
                reason=reason,
            )
        )
