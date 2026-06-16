"""Capa técnica del módulo Options Strategy (adaptador fino). # [PD-3][TH]"""

from __future__ import annotations

from backend.config.logger_setup import get_logger
from backend.config.r1_enrichment_thresholds import L2_OFI_BLEND_WEIGHT
from backend.models.options_strategy import OptionsStrategyInput, TechnicalLayerOutput
from backend.quant_engine.engines.technical.market_structure_engine import (
    analyze_market_structure_from_ohlcv,
)
from backend.quant_engine.engines.technical.ofi_engine import OFIEngine
from backend.quant_engine.engines.technical.smc_engine import SMCEngine
from backend.quant_engine.engines.technical.volume_profile_engine import VolumeProfileEngine
from backend.quant_engine.engines.technical.vwap_engine import analyze_vwap_from_ohlcv
from backend.services.options_strategy._bars import MIN_TECHNICAL_BARS, ohlcv_frame_from_input
from backend.services.options_strategy._scoring import (
    clamp01,
    clamp11,
    infer_breakout_state,
    l2_microstructure_score_from_bundle,
    l2_ofi_bias_from_microstructure,
    market_regime_to_bias,
    ofi_regime_to_bias,
    smc_sesgo_to_bias,
)

logger = get_logger(__name__)

_ENGINE_WEIGHTS: dict[str, float] = {
    "smc_engine": 0.25,
    "market_structure_engine": 0.25,
    "vwap_engine": 0.20,
    "volume_profile_engine": 0.15,
    "ofi_engine": 0.15,
}


def _neutral_output(inp: OptionsStrategyInput) -> TechnicalLayerOutput:
    return TechnicalLayerOutput(
        symbol=inp.symbol,
        as_of=inp.as_of,
        insufficient_data=True,
    )


class TechnicalLayer:
    """Orquesta 5 motores técnicos MVP sobre OHLCV local (sin red)."""

    @classmethod
    def run(cls, inp: OptionsStrategyInput) -> TechnicalLayerOutput:
        frame = ohlcv_frame_from_input(inp, min_bars=MIN_TECHNICAL_BARS)
        if frame is None:
            return _neutral_output(inp)

        engine_scores: dict[str, float] = {}
        biases: dict[str, float] = {}
        structure = None
        ofi_bias = 0.0
        liquidity = 0.0
        l2_micro_score = 0.0

        try:
            smc = SMCEngine().analyze(frame, ticker=inp.symbol, timeframe="5m")
            smc_bias = smc_sesgo_to_bias(str(smc.sesgo))
            biases["smc_engine"] = smc_bias
            engine_scores["smc_engine"] = clamp01(float(smc.composite_score or 0.0))
        except Exception as exc:
            logger.warning("technical_layer.smc_failed symbol=%s error=%s", inp.symbol, exc)

        try:
            structure = analyze_market_structure_from_ohlcv(frame)
            ms_bias = market_regime_to_bias(str(structure.regime))
            biases["market_structure_engine"] = ms_bias
            align = 0.0
            if structure.ok:
                align = clamp01(0.4 + 0.1 * structure.mss_count + 0.05 * structure.sweep_count)
            engine_scores["market_structure_engine"] = align
        except Exception as exc:
            logger.warning(
                "technical_layer.structure_failed symbol=%s error=%s", inp.symbol, exc
            )

        try:
            vwap = analyze_vwap_from_ohlcv(frame)
            z = float(vwap.price_zscore or 0.0) if vwap.ok else 0.0
            biases["vwap_engine"] = clamp11(z / 2.0)
            engine_scores["vwap_engine"] = clamp01(abs(z) / 3.0)
        except Exception as exc:
            logger.warning("technical_layer.vwap_failed symbol=%s error=%s", inp.symbol, exc)

        try:
            profile = VolumeProfileEngine().analyze(inp.symbol, frame)
            last_close = float(frame["close"].iloc[-1])
            poc = float(profile.poc or last_close)
            dist = abs(last_close - poc) / max(last_close, 1e-9)
            liquidity = clamp01(1.0 - min(dist / 0.05, 1.0))
            engine_scores["volume_profile_engine"] = liquidity
            biases["volume_profile_engine"] = clamp11((last_close - poc) / max(poc, 1e-9) * 5.0)
        except Exception as exc:
            logger.warning("technical_layer.vp_failed symbol=%s error=%s", inp.symbol, exc)
            liquidity = 0.0

        try:
            ofi_engine = OFIEngine()
            ofi = ofi_engine.analyze_ohlcv_proxy(frame)
            proxy_bias = ofi_regime_to_bias(str(ofi.regime)) if ofi.ok else 0.0
            l2_score = 0.0
            enrichment = inp.r1_enrichment
            if enrichment is not None and enrichment.l2_ok and enrichment.l2_microstructure:
                l2_bias = l2_ofi_bias_from_microstructure(enrichment.l2_microstructure)
                l2_score = l2_microstructure_score_from_bundle(enrichment.l2_microstructure)
                ofi_bias = clamp11(
                    (1.0 - L2_OFI_BLEND_WEIGHT) * proxy_bias + L2_OFI_BLEND_WEIGHT * l2_bias
                )
                engine_scores["l2_microstructure"] = l2_score
                engine_scores["l2_vpin"] = clamp01(
                    float(enrichment.l2_microstructure.get("vpin") or 0.0)
                )
            else:
                ofi_bias = proxy_bias
            biases["ofi_engine"] = ofi_bias
            engine_scores["ofi_engine"] = clamp01(abs(ofi.latest_accumulated_ofi or 0.0))
        except Exception as exc:
            logger.warning("technical_layer.ofi_failed symbol=%s error=%s", inp.symbol, exc)

        l2_micro_score = engine_scores.get("l2_microstructure", 0.0)

        if not biases:
            return _neutral_output(inp)

        weighted_bias = sum(
            biases.get(name, 0.0) * weight for name, weight in _ENGINE_WEIGHTS.items()
        ) / sum(_ENGINE_WEIGHTS.values())
        trend_quality = clamp01(
            sum(engine_scores.get(name, 0.0) * weight for name, weight in _ENGINE_WEIGHTS.items())
        )
        structure_alignment = clamp01(engine_scores.get("market_structure_engine", 0.0))

        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        close = frame["close"].astype(float)
        range_pct = float((high.max() - low.min()) / max(close.iloc[-1], 1e-9))
        mss_count = structure.mss_count if structure is not None else 0
        sweep_count = structure.sweep_count if structure is not None else 0
        breakout = infer_breakout_state(
            mss_count=mss_count,
            sweep_count=sweep_count,
            ofi_bias=ofi_bias,
            range_pct=range_pct,
        )
        reversal_risk = clamp01(0.2 * sweep_count + (0.3 if breakout == "failed" else 0.0))

        return TechnicalLayerOutput(
            symbol=inp.symbol,
            as_of=inp.as_of,
            technical_direction_bias=clamp11(weighted_bias),
            trend_quality_score=trend_quality,
            breakout_state=breakout,
            liquidity_location_score=liquidity,
            reversal_risk_score=reversal_risk,
            structure_alignment_score=structure_alignment,
            l2_microstructure_score=l2_micro_score,
            engine_scores=engine_scores,
            insufficient_data=False,
        )


__all__ = ["TechnicalLayer"]
