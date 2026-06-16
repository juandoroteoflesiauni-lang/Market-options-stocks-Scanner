"""Capa predictiva del módulo Options Strategy (adaptador fino). # [PD-3][TH]"""

from __future__ import annotations

from backend.config.logger_setup import get_logger
from backend.config.r1_enrichment_thresholds import PREDICTIVE_BRIDGE_BLEND_WEIGHT
from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.models.options_strategy import OptionsStrategyInput, PredictiveLayerOutput
from backend.quant_engine.engines.predictive.expected_move_engine import ExpectedMoveEngine
from backend.quant_engine.engines.predictive.fear_greed_engine import FearGreedEngine
from backend.quant_engine.engines.predictive.markov_regime_engine import MarkovRegimeEngine
from backend.quant_engine.engines.predictive.tail_risk_engine import TailRiskEngine
from backend.services.options_strategy._bars import (
    MIN_PREDICTIVE_BARS,
    chain_rows_for_tail_risk,
    ohlcv_frame_from_input,
    resolve_atm_iv,
    resolve_spot_price,
    resolve_target_dte,
)
from backend.services.options_strategy._scoring import (
    clamp01,
    clamp11,
    fear_greed_to_bias,
    markov_label_to_regime,
)

logger = get_logger(__name__)


def _neutral_output(inp: OptionsStrategyInput) -> PredictiveLayerOutput:
    return PredictiveLayerOutput(
        symbol=inp.symbol,
        as_of=inp.as_of,
        insufficient_data=True,
    )


def _fear_greed_market_data(frame) -> dict[str, float]:
    close = frame["close"].astype(float)
    price = float(close.iloc[-1])
    ma_window = min(125, len(close))
    ma125 = float(close.tail(ma_window).mean())
    realized = close.pct_change().rolling(20).std()
    vol_now = float(realized.iloc[-1] or 0.02)
    vol_ma = float(realized.tail(20).mean() or vol_now)
    vix_proxy = max(10.0, min(80.0, vol_now * 100.0 * 15.0))
    vix_ma_proxy = max(10.0, min(80.0, vol_ma * 100.0 * 15.0))
    return {
        "spx_price": price,
        "spx_ma125": ma125,
        "vix_current": vix_proxy,
        "vix_ma50": vix_ma_proxy,
    }


def _run_fear_greed(symbol: str, market_data: dict[str, float]):
    """Fear & Greed sin ``asyncio.run()`` dentro del event loop del bot."""
    engine = FearGreedEngine()
    return engine.compute_sync(symbol=symbol, market_data=market_data)


class PredictiveLayer:
    """Orquesta 4 motores predictivos MVP (sin APIs externas en el happy path)."""

    @classmethod
    def run(
        cls,
        inp: OptionsStrategyInput,
        *,
        config: OptionsStrategyConfigBundle | None = None,
    ) -> PredictiveLayerOutput:
        active_config = config or get_options_strategy_config()
        frame = ohlcv_frame_from_input(inp, min_bars=MIN_PREDICTIVE_BARS)
        if frame is None:
            return _neutral_output(inp)

        spot = resolve_spot_price(inp, frame)
        if spot <= 0:
            return _neutral_output(inp)

        engine_scores: dict[str, float] = {}
        biases: dict[str, float] = {}
        regime_class = "unknown"
        expected_move_pct = 0.0
        expected_move_confidence = 0.0
        left_tail = 0.0
        right_tail = 0.0
        macro_alignment = 0.0
        forecast_dispersion = 0.0

        try:
            markov = MarkovRegimeEngine().analyze(inp.symbol, frame)
            regime_class = markov_label_to_regime(markov.current_state, markov.regime_signal)
            markov_bias = clamp11(
                (0.6 if markov.current_state == "BULL_QUIET" else 0.0)
                + (-0.6 if markov.current_state == "BEAR_VOLATILE" else 0.0)
            )
            biases["markov_regime_engine"] = markov_bias
            engine_scores["markov_regime_engine"] = clamp01(float(markov.state_confidence))
            forecast_dispersion = clamp01(float(markov.transition_risk))
        except Exception as exc:
            logger.warning("predictive_layer.markov_failed symbol=%s error=%s", inp.symbol, exc)

        try:
            iv = resolve_atm_iv(inp)
            dte = resolve_target_dte(active_config)
            em = ExpectedMoveEngine.calculate(spot=spot, iv=iv, dte=dte)
            summary = em.get_summary()
            expected_move_pct = float(summary.get("expected_move_pct", 0.0)) / 100.0
            expected_move_confidence = clamp01(0.5 + min(iv, 0.8) * 0.4)
            engine_scores["expected_move_engine"] = expected_move_confidence
            biases["expected_move_engine"] = 0.0
        except Exception as exc:
            logger.warning(
                "predictive_layer.expected_move_failed symbol=%s error=%s", inp.symbol, exc
            )

        try:
            tail_df = chain_rows_for_tail_risk(inp, spot)
            if tail_df is not None:
                engine = TailRiskEngine()
                metrics = engine.compute_metrics(tail_df, as_of_iso=inp.as_of.isoformat())
                alert = engine.assess_tail_risk(metrics)
                convex_pct = clamp01(float(alert.convexity_percentile) / 100.0)
                left_tail = convex_pct if metrics.skew_25d > 0 else convex_pct * 0.5
                right_tail = convex_pct if metrics.skew_25d < 0 else convex_pct * 0.5
                engine_scores["tail_risk_engine"] = convex_pct
                skew_bias = clamp11(-metrics.skew_25d * 5.0)
                biases["tail_risk_engine"] = skew_bias
            else:
                engine_scores["tail_risk_engine"] = 0.0
        except Exception as exc:
            logger.warning("predictive_layer.tail_risk_failed symbol=%s error=%s", inp.symbol, exc)

        try:
            fg = _run_fear_greed(inp.symbol, _fear_greed_market_data(frame))
            fg_bias = fear_greed_to_bias(fg.score)
            biases["fear_greed_engine"] = fg_bias
            engine_scores["fear_greed_engine"] = clamp01(fg.score / 100.0)
            macro_alignment = clamp11((fg.factors.get("momentum", 50.0) - 50.0) / 50.0)
        except Exception as exc:
            logger.warning(
                "predictive_layer.fear_greed_failed symbol=%s error=%s", inp.symbol, exc
            )

        if not engine_scores:
            return _neutral_output(inp)

        predictive_bias = clamp11(
            sum(biases.values()) / max(len(biases), 1)
            if biases
            else 0.0
        )
        enrichment = inp.r1_enrichment
        if enrichment is not None and enrichment.predictive_meta:
            meta = enrichment.predictive_meta
            bridge_bias = clamp11(float(meta.get("directional_bias") or 0.0))
            bridge_conf = clamp01(float(meta.get("confidence") or 0.0))
            predictive_bias = clamp11(
                (1.0 - PREDICTIVE_BRIDGE_BLEND_WEIGHT) * predictive_bias
                + PREDICTIVE_BRIDGE_BLEND_WEIGHT * bridge_bias
            )
            engine_scores["predictive_bridge"] = bridge_conf

        return PredictiveLayerOutput(
            symbol=inp.symbol,
            as_of=inp.as_of,
            predictive_direction_bias=predictive_bias,
            regime_class=regime_class,
            expected_move_pct=max(0.0, expected_move_pct),
            expected_move_confidence=expected_move_confidence,
            left_tail_risk_score=clamp01(left_tail),
            right_tail_risk_score=clamp01(right_tail),
            macro_alignment_score=macro_alignment,
            forecast_dispersion_score=forecast_dispersion,
            engine_scores=engine_scores,
            insufficient_data=False,
        )


__all__ = ["PredictiveLayer"]
