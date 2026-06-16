"""Capa de opciones del módulo Options Strategy (adaptador fino). # [PD-3][TH]"""

from __future__ import annotations

import numpy as np

from backend.config.logger_setup import get_logger
from backend.config.r1_enrichment_thresholds import HYBRID_OPTIONS_BLEND_WEIGHT
from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.models.options_strategy import (
    DealerRegime,
    IvState,
    OptionsLayerOutput,
    OptionsStrategyInput,
    OptionsStructure,
)
from backend.quant_engine.engines.options.gamma_flip import GammaFlipEngine
from backend.quant_engine.engines.options.iv_primitives import historical_volatility
from backend.quant_engine.engines.options.options_flow_signal import OptionsFlowSignalEngine
from backend.quant_engine.engines.predictive.dealer_flow_dynamics_engine import (
    get_dealer_flow_dynamics,
)
from backend.quant_engine.engines.predictive.dex_engine import DeltaExposureEngine
from backend.services.options_strategy._bars import (
    ohlcv_frame_from_input,
    resolve_atm_iv,
    resolve_spot_price,
    resolve_target_dte,
)
from backend.services.options_strategy._chain import (
    classify_iv_state,
    dealer_flow_frame,
    dex_frame,
    flow_rows_from_chain,
    gamma_flip_array,
)
from backend.services.options_strategy._scoring import clamp01, clamp11, confluence_to_bias

logger = get_logger(__name__)

_ENGINE_WEIGHTS: dict[str, float] = {
    "dealer_flow_dynamics_engine": 0.25,
    "gamma_flip": 0.20,
    "dex_engine": 0.15,
    "options_flow_signal": 0.20,
    "iv_primitives": 0.20,
}

_DEFAULT_RATE = 0.04


def _neutral_output(inp: OptionsStrategyInput) -> OptionsLayerOutput:
    return OptionsLayerOutput(
        symbol=inp.symbol,
        as_of=inp.as_of,
        insufficient_data=True,
    )


def _map_dealer_regime(signal: float, pinning_prob: float) -> DealerRegime:
    if pinning_prob >= 0.6:
        return "pinning"
    if signal >= 0.25:
        return "supportive"
    if signal <= -0.25:
        return "suppressive"
    if abs(signal) < 0.1:
        return "unstable"
    return "unknown"


def _structure_preference_from_bias(bias: float, iv_state: IvState) -> OptionsStructure:
    if abs(bias) < 0.15:
        return OptionsStructure.NO_TRADE
    rich = iv_state in {"rich", "extreme"}
    if bias > 0:
        return OptionsStructure.CALL_DEBIT_SPREAD if rich else OptionsStructure.LONG_CALL
    return OptionsStructure.PUT_DEBIT_SPREAD if rich else OptionsStructure.LONG_PUT


class OptionsLayer:
    """Orquesta motores de opciones MVP sobre snapshot R1 (sin red)."""

    @classmethod
    def run(
        cls,
        inp: OptionsStrategyInput,
        *,
        config: OptionsStrategyConfigBundle | None = None,
    ) -> OptionsLayerOutput:
        if inp.options_context is None or not inp.options_context.available:
            return _neutral_output(inp)

        active = config or get_options_strategy_config()
        spot = resolve_spot_price(inp, None)
        if spot <= 0:
            return _neutral_output(inp)

        atm_iv = resolve_atm_iv(inp)
        dte = resolve_target_dte(active)
        tte = max(dte / 365.0, 1 / 365.0)
        engine_scores: dict[str, float] = {}
        biases: dict[str, float] = {}
        dealer_regime: DealerRegime = "unknown"
        gamma_pressure = 0.0
        flow_conviction = 0.0
        chain_liquidity = 0.0

        frame = ohlcv_frame_from_input(inp, min_bars=20)
        hv = None
        if frame is not None and len(frame) >= 21:
            closes = frame["close"].astype(float)
            log_returns = np.log(closes / closes.shift(1)).dropna().to_numpy()
            hv = historical_volatility(log_returns, min(20, len(log_returns)))
        iv_state: IvState = classify_iv_state(atm_iv, hv)  # type: ignore[assignment]

        try:
            dealer_df = dealer_flow_frame(inp)
            if dealer_df is not None and not dealer_df.empty:
                dynamics = get_dealer_flow_dynamics(
                    dealer_df,
                    spot=spot,
                    vix=atm_iv * 100.0,
                    time_to_expiry=tte,
                    rate=_DEFAULT_RATE,
                )
                if "error_msg" not in dynamics:
                    signal = float(dynamics.get("dealer_directional_signal", 0.0))
                    pinning = float(dynamics.get("pinning_probability", 0.0))
                    biases["dealer_flow_dynamics_engine"] = clamp11(signal)
                    engine_scores["dealer_flow_dynamics_engine"] = clamp01(abs(signal))
                    dealer_regime = _map_dealer_regime(signal, pinning)
        except Exception as exc:
            logger.warning("options_layer.dealer_flow_failed symbol=%s error=%s", inp.symbol, exc)

        try:
            gamma_arr = gamma_flip_array(inp)
            if gamma_arr is not None:
                report = GammaFlipEngine().analyze_gamma_flip(
                    gamma_arr,
                    spot_price=spot,
                    tte=tte,
                    rate=_DEFAULT_RATE,
                    sigma=atm_iv,
                )
                if report.is_success:
                    payload = report.unwrap()
                    regime = str(payload.volatility_regime.regime)
                    dist = payload.volatility_regime.distance_pct or 0.0
                    gamma_pressure = clamp01(abs(dist) / 5.0)
                    flip_bias = 0.3 if regime == "GAMMA_POSITIVE" else -0.3 if regime == "GAMMA_NEGATIVE" else 0.0
                    biases["gamma_flip"] = clamp11(flip_bias)
                    engine_scores["gamma_flip"] = gamma_pressure
        except Exception as exc:
            logger.warning("options_layer.gamma_flip_failed symbol=%s error=%s", inp.symbol, exc)

        try:
            dex_df = dex_frame(inp)
            if dex_df is not None and not dex_df.empty:
                dex = DeltaExposureEngine(dex_df).compute(inp.symbol)
                nominal = float(dex.dex_total_nominal)
                bias = clamp11(nominal / max(abs(nominal), spot * 1e6) * 5.0)
                biases["dex_engine"] = bias
                engine_scores["dex_engine"] = clamp01(abs(bias))
        except Exception as exc:
            logger.warning("options_layer.dex_failed symbol=%s error=%s", inp.symbol, exc)

        try:
            flow = OptionsFlowSignalEngine().analyze(flow_rows_from_chain(inp))
            flow_conviction = clamp01(float(flow.confidence))
            biases["options_flow_signal"] = clamp11(float(flow.directional_score))
            engine_scores["options_flow_signal"] = flow_conviction
            total_oi = sum(
                float(row.get("open_interest", 0.0)) for row in flow_rows_from_chain(inp)
            )
            chain_liquidity = clamp01(
                min(total_oi / max(active.universe.min_open_interest * 10, 1.0), 1.0)
            )
        except Exception as exc:
            logger.warning("options_layer.flow_failed symbol=%s error=%s", inp.symbol, exc)

        iv_score = 0.5
        if iv_state == "cheap":
            iv_score = 0.8
        elif iv_state == "fair":
            iv_score = 0.6
        elif iv_state == "rich":
            iv_score = 0.35
        elif iv_state == "extreme":
            iv_score = 0.15
        engine_scores["iv_primitives"] = iv_score
        biases["iv_primitives"] = clamp11((0.5 - iv_score) * 2.0)

        if not engine_scores:
            return _neutral_output(inp)

        options_bias = clamp11(sum(biases.values()) / max(len(biases), 1))
        hybrid_score = 0.0
        enrichment = inp.r1_enrichment
        if enrichment is not None and enrichment.hybrid_confluence is not None:
            conf = enrichment.hybrid_confluence
            hybrid_score = float(conf.score)
            hybrid_bias = confluence_to_bias(conf)
            options_bias = clamp11(
                (1.0 - HYBRID_OPTIONS_BLEND_WEIGHT) * options_bias
                + HYBRID_OPTIONS_BLEND_WEIGHT * hybrid_bias
            )
            engine_scores["hybrid_confluence"] = hybrid_score
            for engine, score in conf.by_engine.items():
                engine_scores[f"hybrid_{engine}"] = clamp01(abs(float(score)))

        structure_pref = _structure_preference_from_bias(options_bias, iv_state)

        return OptionsLayerOutput(
            symbol=inp.symbol,
            as_of=inp.as_of,
            options_direction_bias=options_bias,
            dealer_regime=dealer_regime,
            gamma_pressure_score=gamma_pressure,
            iv_state=iv_state,
            flow_conviction_score=flow_conviction,
            chain_liquidity_score=chain_liquidity,
            structure_preference=structure_pref,
            hybrid_confluence_score=hybrid_score,
            engine_scores=engine_scores,
            insufficient_data=False,
        )


__all__ = ["OptionsLayer"]
